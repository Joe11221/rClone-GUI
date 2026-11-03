import os
import stat

import pytest

from app import create_app
from app.auth import MAX_FAILURES
from app.extensions import db
from tests.conftest import TestConfig

HTML = {"Accept": "text/html"}
PASSWORD = "correct horse"


def _make_app(**overrides):
    config = type("AuthConfig", (TestConfig,), overrides)
    return create_app(config)


@pytest.fixture()
def pw_app():
    app = _make_app(AUTH_MODE="password")
    yield app
    with app.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def pw_client(pw_app):
    return pw_app.test_client()


def _setup(client, password=PASSWORD):
    return client.post("/setup", data={"password": password, "confirm": password})


@pytest.fixture()
def logged_in(pw_client):
    _setup(pw_client)
    return pw_client


@pytest.fixture()
def configured(pw_app):
    """A password is set, but this client is not logged in."""
    _setup(pw_app.test_client())
    return pw_app.test_client()


# --- First run ---


def test_redirects_to_setup_when_no_password(pw_client):
    response = pw_client.get("/jobs/", headers=HTML)
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/setup")


def test_setup_sets_hash_and_logs_in(pw_client):
    response = _setup(pw_client)
    assert response.status_code == 302
    assert pw_client.get("/jobs/", headers=HTML).status_code == 200
    from app.services.app_settings import get_setting

    with pw_client.application.app_context():
        stored = get_setting("auth_password_hash")
    assert stored and PASSWORD not in stored


def test_setup_validates_password(pw_client):
    assert _setup(pw_client, "short").status_code == 400
    mismatch = pw_client.post("/setup", data={"password": PASSWORD, "confirm": "other-one!"})
    assert mismatch.status_code == 400
    assert pw_client.get("/jobs/", headers=HTML).status_code == 302


def test_setup_refused_once_password_exists(configured):
    response = configured.post(
        "/setup", data={"password": "hijack-attempt", "confirm": "hijack-attempt"}
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")
    assert configured.get("/setup").headers["Location"].endswith("/login")
    # The original password still works
    assert configured.post("/login", data={"password": PASSWORD}).status_code == 302


# --- Login ---


def test_unauthenticated_page_redirects_to_login_with_next(configured):
    response = configured.get("/jobs/", headers=HTML)
    assert response.status_code == 302
    assert "/login?next=/jobs/" in response.headers["Location"]


def test_login_success_and_failure(configured):
    bad = configured.post("/login", data={"password": "wrong"})
    assert bad.status_code == 401
    assert b"Incorrect password" in bad.data
    assert configured.get("/jobs/", headers=HTML).status_code == 302

    good = configured.post("/login", data={"password": PASSWORD})
    assert good.status_code == 302
    assert configured.get("/jobs/", headers=HTML).status_code == 200


def test_login_redirects_to_safe_next(configured):
    response = configured.post("/login?next=/jobs/", data={"password": PASSWORD})
    assert response.headers["Location"].endswith("/jobs/")


@pytest.mark.parametrize(
    "target",
    ["//evil.com", "https://evil.com", "/\\evil.com", "evil.com", "javascript:alert(1)"],
)
def test_login_rejects_open_redirect(configured, target):
    response = configured.post("/login", data={"password": PASSWORD, "next": target})
    assert response.status_code == 302
    location = response.headers["Location"]
    assert "evil" not in location and "javascript" not in location


def test_lockout_after_repeated_failures(configured):
    for _ in range(MAX_FAILURES):
        configured.post("/login", data={"password": "wrong"})
    # Even the right password is refused while locked out
    locked = configured.post("/login", data={"password": PASSWORD})
    assert locked.status_code == 429
    assert b"Too many failed attempts" in locked.data
    assert configured.get("/jobs/", headers=HTML).status_code == 302


def test_lockout_expires(configured, monkeypatch):
    import app.auth as auth

    for _ in range(MAX_FAILURES):
        configured.post("/login", data={"password": "wrong"})
    real_time = auth.time.time
    monkeypatch.setattr(auth.time, "time", lambda: real_time() + auth.LOCKOUT_SECONDS + 1)
    assert configured.post("/login", data={"password": PASSWORD}).status_code == 302


def test_logout_requires_post(logged_in):
    assert logged_in.get("/logout").status_code == 405
    assert logged_in.get("/jobs/", headers=HTML).status_code == 200
    response = logged_in.post("/logout")
    assert response.status_code == 302
    assert logged_in.get("/jobs/", headers=HTML).status_code == 302


def test_session_cookie_is_httponly_and_permanent(configured):
    response = configured.post("/login", data={"password": PASSWORD})
    cookie = response.headers["Set-Cookie"]
    assert "HttpOnly" in cookie
    assert "Expires=" in cookie  # permanent session
    assert "Secure" not in cookie


# --- Enforcement ---


def test_json_and_sse_get_401_not_redirect(configured):
    for path in ("/api/events", "/jobs/"):
        response = configured.get(path)  # no text/html in Accept
        assert response.status_code == 401
        assert response.get_json() == {"error": "login required"}
    sse = configured.get("/api/events", headers=HTML)
    assert sse.status_code == 401


def test_static_files_and_favicon_reachable(pw_client):
    assert pw_client.get("/static/css/style.css").status_code == 200
    assert pw_client.get("/favicon.ico").status_code == 200


def test_login_page_has_no_nav_or_stream(configured):
    html = configured.get("/login").data.decode()
    assert 'id="navbarNav"' not in html
    assert 'data-public="1"' in html


def test_navbar_logout_button_when_logged_in(logged_in):
    html = logged_in.get("/").data.decode()
    assert 'action="/logout"' in html


# --- Change password ---


def test_change_password_flow(logged_in, pw_app):
    other = pw_app.test_client()
    other.post("/login", data={"password": PASSWORD})
    assert other.get("/jobs/", headers=HTML).status_code == 200

    wrong = logged_in.post(
        "/account/password",
        data={"current_password": "nope", "new_password": "brand new pw", "confirm_password": "brand new pw"},
        follow_redirects=True,
    )
    assert b"Current password is incorrect" in wrong.data

    short = logged_in.post(
        "/account/password",
        data={"current_password": PASSWORD, "new_password": "short", "confirm_password": "short"},
        follow_redirects=True,
    )
    assert b"at least 8" in short.data

    ok = logged_in.post(
        "/account/password",
        data={"current_password": PASSWORD, "new_password": "brand new pw", "confirm_password": "brand new pw"},
        follow_redirects=True,
    )
    assert b"Password changed" in ok.data
    # This browser stays signed in, others are signed out
    assert logged_in.get("/jobs/", headers=HTML).status_code == 200
    assert other.get("/jobs/", headers=HTML).status_code == 302

    fresh = pw_app.test_client()
    assert fresh.post("/login", data={"password": PASSWORD}).status_code == 401
    assert fresh.post("/login", data={"password": "brand new pw"}).status_code == 302


def test_settings_shows_password_section_only_in_password_mode(logged_in, client):
    assert b"Change password" in logged_in.get("/settings/").data
    assert b"Change password" not in client.get("/settings/").data


def test_change_password_requires_login(configured):
    response = configured.post("/account/password", data={})
    assert response.status_code == 401


# --- Proxy mode ---


@pytest.fixture()
def proxy_app():
    app = _make_app(AUTH_MODE="proxy")
    yield app
    with app.app_context():
        db.session.remove()
        db.drop_all()


def test_proxy_mode_requires_header(proxy_app):
    client = proxy_app.test_client()
    missing = client.get("/jobs/", headers=HTML)
    assert missing.status_code == 401
    assert b"Remote-User" in missing.data
    assert client.get("/jobs/", headers={**HTML, "Remote-User": "joe"}).status_code == 200
    assert client.get("/jobs/", headers={**HTML, "Remote-User": ""}).status_code == 401


def test_proxy_mode_custom_header():
    app = _make_app(AUTH_MODE="proxy", AUTH_PROXY_HEADER="X-Forwarded-User")
    client = app.test_client()
    assert client.get("/jobs/").status_code == 401
    assert client.get("/jobs/", headers={"X-Forwarded-User": "joe"}).status_code == 200
    # No login pages in proxy mode
    assert client.get("/login").status_code == 302


def test_proxy_mode_statics_reachable(proxy_app):
    assert proxy_app.test_client().get("/static/css/style.css").status_code == 200


# --- Config and banner ---


@pytest.mark.parametrize("mode", ["", "Password1", "basic", "off"])
def test_invalid_auth_mode_fails_at_startup(mode):
    with pytest.raises(RuntimeError, match="AUTH_MODE"):
        _make_app(AUTH_MODE=mode)


def test_none_mode_shows_warning_banner(client):
    html = client.get("/").data.decode()
    assert "Login is turned off" in html
    assert b"Log out" not in client.get("/").data


def test_desktop_mode_hides_warning_banner():
    app = _make_app(AUTH_MODE="none", DESKTOP_MODE=True)
    html = app.test_client().get("/").data.decode()
    assert "Login is turned off" not in html


def test_password_mode_has_no_warning_banner(logged_in):
    assert b"Login is turned off" not in logged_in.get("/").data


def test_secure_cookies_setting_is_applied():
    app = _make_app(AUTH_MODE="password", SESSION_COOKIE_SECURE=True)
    client = app.test_client()
    response = client.post(
        "/setup", data={"password": PASSWORD, "confirm": PASSWORD}, base_url="https://localhost"
    )
    assert "Secure" in response.headers["Set-Cookie"]


# --- SECRET_KEY autogeneration ---


def test_secret_key_generated_once_and_reused(tmp_path):
    first = _make_app(SECRET_KEY="", INSTANCE_PATH=str(tmp_path))
    key_file = tmp_path / "secret_key"
    assert key_file.exists()
    key = key_file.read_text().strip()
    assert len(key) >= 32
    assert first.config["SECRET_KEY"] == key

    second = _make_app(SECRET_KEY="", INSTANCE_PATH=str(tmp_path))
    assert second.config["SECRET_KEY"] == key
    assert key_file.read_text().strip() == key


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
def test_secret_key_file_is_private(tmp_path):
    _make_app(SECRET_KEY="", INSTANCE_PATH=str(tmp_path))
    mode = stat.S_IMODE((tmp_path / "secret_key").stat().st_mode)
    assert mode == 0o600


def test_explicit_secret_key_is_not_written(tmp_path):
    app = _make_app(SECRET_KEY="my-own-key", INSTANCE_PATH=str(tmp_path))
    assert app.config["SECRET_KEY"] == "my-own-key"
    assert not (tmp_path / "secret_key").exists()


def test_placeholder_secret_key_still_rejected(tmp_path):
    with pytest.raises(RuntimeError, match="placeholder"):
        _make_app(SECRET_KEY="change-me", INSTANCE_PATH=str(tmp_path))
    assert not (tmp_path / "secret_key").exists()
