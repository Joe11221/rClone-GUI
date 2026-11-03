"""Login for the web UI.

AUTH_MODE decides who may use the app:

* ``password``: a single shared password, chosen on first visit (/setup).
* ``proxy``:    a reverse proxy has already authenticated the user and says so
                in a header (AUTH_PROXY_HEADER). The app's port must only be
                reachable by that proxy, or anyone can forge the header.
* ``none``:     no login at all. An explicit opt-out.
"""

import hashlib
import os
import secrets
import time
from urllib.parse import urlsplit

from flask import (
    Blueprint,
    Response,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

from app.services.app_settings import get_setting, set_setting

AUTH_MODES = ("password", "proxy", "none")
PASSWORD_SETTING = "auth_password_hash"
MIN_PASSWORD_LENGTH = 8

# Brute-force slowdown, per remote address.
MAX_FAILURES = 5
FAILURE_WINDOW_SECONDS = 15 * 60
LOCKOUT_SECONDS = 15 * 60

# Endpoints reachable without logging in.
_PUBLIC_ENDPOINTS = {"auth.login", "auth.setup", "auth.logout", "static", "favicon"}
# Blueprints that do their own authentication (the Windows tray's control token).
_SELF_AUTHENTICATED_BLUEPRINTS = {"desktop_control"}

auth_bp = Blueprint("auth", __name__)


# --- Secret key ---


def load_or_create_secret_key(instance_path):
    """Return the key stored in ``<instance>/secret_key``, creating it once."""
    path = os.path.join(instance_path, "secret_key")
    try:
        with open(path, encoding="utf-8") as handle:
            key = handle.read().strip()
        if key:
            return key
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise RuntimeError(f"Could not read {path}: {exc}") from exc

    key = secrets.token_hex(32)
    try:
        os.makedirs(instance_path, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(key)
    except OSError as exc:
        raise RuntimeError(
            f"SECRET_KEY is not set and {path} could not be written ({exc}). "
            "Set SECRET_KEY in your .env file, or make that folder writable."
        ) from exc
    return key


# --- Helpers ---


def _mode():
    return current_app.config["AUTH_MODE"]


def _password_hash():
    return get_setting(PASSWORD_SETTING)


def _hash_fingerprint(password_hash):
    """Ties a session to one password, so changing it signs everyone else out."""
    return hashlib.sha256(password_hash.encode()).hexdigest()[:16]


def _is_authenticated():
    password_hash = _password_hash()
    return bool(
        password_hash
        and session.get("authenticated")
        and session.get("pwv") == _hash_fingerprint(password_hash)
    )


def _start_session(password_hash):
    session.clear()  # new session contents, so nothing from before login survives
    session["authenticated"] = True
    session["pwv"] = _hash_fingerprint(password_hash)
    session.permanent = True


def safe_next(target):
    """Return ``target`` only if it is a path on this site, else None."""
    if not target or not target.startswith("/") or target.startswith("//"):
        return None
    if "\\" in target or any(ord(c) < 32 for c in target):
        return None
    parts = urlsplit(target)
    if parts.scheme or parts.netloc:
        return None
    return target


def _wants_json():
    if request.path.startswith("/api/") or request.is_json:
        return True
    return "text/html" not in request.headers.get("Accept", "")


def _state():
    return current_app.extensions["auth_state"]


def _locked_until(addr):
    now = time.time()
    until = _state()["locked"].get(addr, 0)
    if until <= now:
        _state()["locked"].pop(addr, None)
        return 0
    return until


def _record_failure(addr):
    now = time.time()
    state = _state()
    # Drop stale entries so the dicts can't grow without bound.
    for key in [
        k
        for k, times in state["failures"].items()
        if not times or times[-1] < now - FAILURE_WINDOW_SECONDS
    ]:
        del state["failures"][key]
    for key in [k for k, until in state["locked"].items() if until <= now]:
        del state["locked"][key]

    recent = [t for t in state["failures"].get(addr, []) if t > now - FAILURE_WINDOW_SECONDS]
    recent.append(now)
    state["failures"][addr] = recent
    if len(recent) >= MAX_FAILURES:
        state["locked"][addr] = now + LOCKOUT_SECONDS
        state["failures"].pop(addr, None)


def _clear_failures(addr):
    _state()["failures"].pop(addr, None)
    _state()["locked"].pop(addr, None)


def _lockout_message(until):
    minutes = max(1, int((until - time.time() + 59) // 60))
    return (
        "Too many failed attempts. Try again in "
        f"{minutes} minute{'s' if minutes != 1 else ''}."
    )


def _check_password(password, addr):
    """Verify against the stored hash. Returns (ok, error_message)."""
    until = _locked_until(addr)
    if until:
        return False, _lockout_message(until)
    password_hash = _password_hash()
    if password_hash and check_password_hash(password_hash, password or ""):
        _clear_failures(addr)
        return True, None
    _record_failure(addr)
    until = _locked_until(addr)
    if until:
        return False, _lockout_message(until)
    return False, "Incorrect password."


# --- Enforcement ---


def init_auth(app):
    mode = app.config["AUTH_MODE"]
    if mode not in AUTH_MODES:
        raise RuntimeError(
            f"Invalid AUTH_MODE {mode!r}; use one of: {', '.join(AUTH_MODES)}."
        )
    header = app.config["AUTH_PROXY_HEADER"]
    if mode == "proxy" and not header:
        raise RuntimeError("AUTH_MODE=proxy needs AUTH_PROXY_HEADER to be set.")

    app.extensions["auth_state"] = {"failures": {}, "locked": {}}
    app.register_blueprint(auth_bp)

    @app.before_request
    def _require_login():
        if mode == "none":
            return None
        if (
            request.endpoint is None
            or request.endpoint in _PUBLIC_ENDPOINTS
            or request.blueprint in _SELF_AUTHENTICATED_BLUEPRINTS
        ):
            return None

        if mode == "proxy":
            if request.headers.get(header):
                return None
            return Response(
                f"Unauthorized: this app expects your reverse proxy to log you in "
                f"and send the {header} header, and it was missing. Check the "
                "proxy's configuration.\n",
                status=401,
                mimetype="text/plain",
            )

        if _is_authenticated():
            return None
        if _wants_json():
            return jsonify(error="login required"), 401
        if not _password_hash():
            return redirect(url_for("auth.setup"))
        if request.method == "GET":
            target = request.full_path.rstrip("?")
            return redirect(url_for("auth.login", next=target))
        return redirect(url_for("auth.login"))

    @app.context_processor
    def _inject_auth():
        return {
            "auth_mode": mode,
            "show_logout": mode == "password" and bool(session.get("authenticated")),
            "show_auth_warning": mode == "none" and not app.config["DESKTOP_MODE"],
        }


# --- Routes ---


def _password_mode_only():
    """Login pages mean nothing outside password mode."""
    if _mode() != "password":
        return redirect(url_for("dashboard"))
    return None


@auth_bp.route("/setup", methods=["GET", "POST"])
def setup():
    if (other := _password_mode_only()) is not None:
        return other
    if _password_hash():
        return redirect(url_for("auth.login"))

    error = None
    if request.method == "POST":
        password = request.form.get("password", "")
        if len(password) < MIN_PASSWORD_LENGTH:
            error = f"Use at least {MIN_PASSWORD_LENGTH} characters."
        elif password != request.form.get("confirm", ""):
            error = "The passwords do not match."
        else:
            password_hash = generate_password_hash(password)
            set_setting(PASSWORD_SETTING, password_hash)
            _start_session(password_hash)
            return redirect(url_for("dashboard"))
    return render_template(
        "auth/setup.html", error=error, min_length=MIN_PASSWORD_LENGTH, public_page=True
    ), (400 if error else 200)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if (other := _password_mode_only()) is not None:
        return other
    if not _password_hash():
        return redirect(url_for("auth.setup"))

    target = safe_next(request.values.get("next"))
    error = None
    status = 200
    if request.method == "POST":
        ok, error = _check_password(request.form.get("password", ""), request.remote_addr)
        if ok:
            _start_session(_password_hash())
            return redirect(target or url_for("dashboard"))
        status = 429 if _locked_until(request.remote_addr) else 401
    return (
        render_template("auth/login.html", error=error, next=target, public_page=True),
        status,
    )


@auth_bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    if _mode() == "password":
        return redirect(url_for("auth.login"))
    return redirect(url_for("dashboard"))


@auth_bp.route("/account/password", methods=["POST"])
def change_password():
    if _mode() != "password":
        return redirect(url_for("settings.index"))

    ok, error = _check_password(
        request.form.get("current_password", ""), request.remote_addr
    )
    new = request.form.get("new_password", "")
    if not ok:
        flash(error.replace("Incorrect password.", "Current password is incorrect."), "danger")
    elif len(new) < MIN_PASSWORD_LENGTH:
        flash(f"The new password needs at least {MIN_PASSWORD_LENGTH} characters.", "danger")
    elif new != request.form.get("confirm_password", ""):
        flash("The new passwords do not match.", "danger")
    else:
        new_hash = generate_password_hash(new)
        set_setting(PASSWORD_SETTING, new_hash)
        _start_session(new_hash)  # other devices are signed out
        flash("Password changed. Other signed-in browsers have been logged out.", "success")
    return redirect(url_for("settings.index"))
