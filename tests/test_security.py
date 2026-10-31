import pytest

from app import create_app
from app.models import Job
from tests.conftest import TestConfig

JOB = {"name": "j", "source": "/a", "destination": "b:", "sync_type": "sync"}


def _post(client, **headers):
    return client.post("/jobs/create", data=JOB, headers=headers)


@pytest.mark.parametrize(
    "headers",
    [
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "same-site"},
        {"Origin": "https://evil.example"},
        {"Origin": "null"},
    ],
)
def test_cross_site_post_is_rejected(app, client, headers):
    assert _post(client, **headers).status_code == 403
    with app.app_context():
        assert Job.query.count() == 0


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Sec-Fetch-Site": "same-origin"},
        {"Origin": "http://localhost"},
        # A proxy rewrote Host, but the browser still vouches for the request.
        {"Sec-Fetch-Site": "same-origin", "Origin": "https://nas.example"},
    ],
)
def test_same_origin_post_is_allowed(client, headers):
    assert _post(client, **headers).status_code == 302


def test_cross_site_get_is_harmless_and_allowed(client):
    assert client.get("/", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 200


def test_allowed_hosts_blocks_dns_rebinding():
    class Cfg(TestConfig):
        ALLOWED_HOSTS = ["127.0.0.1:8765"]

    client = create_app(Cfg).test_client()
    assert client.get("/", headers={"Host": "127.0.0.1:8765"}).status_code == 200
    assert client.get("/", headers={"Host": "evil.example:8765"}).status_code == 403


def test_session_cookie_is_same_site(app):
    assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"
