import pytest

from app import create_app
from app.config import Config


def _config(**overrides):
    attrs = {
        "TESTING": True,
        "SECRET_KEY": "a-real-secret",
        "SQLALCHEMY_DATABASE_URI": "sqlite://",
        "BACKGROUND_TASKS_ENABLED": False,
    }
    attrs.update(overrides)
    return type("Cfg", (Config,), attrs)


@pytest.mark.parametrize(
    "key", ["", "change-me", "change-me-in-production", "dev-secret-key-change-me"]
)
def test_refuses_to_start_without_real_secret_key(key):
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        create_app(_config(SECRET_KEY=key))


def test_starts_with_real_secret_key():
    assert create_app(_config()) is not None


def test_invalid_timezone_fails_loudly():
    with pytest.raises(RuntimeError, match="TIMEZONE"):
        create_app(_config(TIMEZONE="Not/AZone"))


def test_default_timezone_is_utc():
    assert Config.TIMEZONE == "UTC"
