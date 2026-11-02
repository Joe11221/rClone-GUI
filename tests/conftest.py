import pytest

from app import create_app
from app.config import Config
from app.extensions import db


class TestConfig(Config):
    TESTING = True
    SECRET_KEY = "test-only-secret-key"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    TIMEZONE = "America/New_York"
    BACKGROUND_TASKS_ENABLED = False


@pytest.fixture()
def app():
    app = create_app(TestConfig)
    yield app
    with app.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


class FakeRclone:
    """Stand-in for RcloneClient that records calls instead of doing HTTP."""

    def __init__(self):
        self.calls = []
        self.bytes_per_second = -1  # what rclone's limiter is set to

    def get_bwlimit(self):
        return {"bytesPerSecond": self.bytes_per_second}

    def set_bwlimit(self, rate):
        self.calls.append(("bwlimit", rate))
        self.bytes_per_second = -1 if rate == "off" else int(rate.rstrip("b"))
        return {"rate": rate}

    def _start(self, kind, **kwargs):
        self.calls.append((kind, kwargs))
        return {"jobid": 42}

    def start_sync(self, **kwargs):
        return self._start("sync", **kwargs)

    def start_copy(self, **kwargs):
        return self._start("copy", **kwargs)

    def start_move(self, **kwargs):
        return self._start("move", **kwargs)

    def list_remotes(self):
        return {"remotes": ["gdrive", "pcloud"]}

    def list_dirs(self, fs, remote="", timeout=None):
        return {"list": [{"Name": "b"}, {"Name": "a"}]}


@pytest.fixture()
def fake_rclone(app):
    fake = FakeRclone()
    app.config["RCLONE_CLIENT"] = fake
    app.config["JOB_RUNNER"].rclone = fake
    app.config["BANDWIDTH"].rclone = fake
    return fake
