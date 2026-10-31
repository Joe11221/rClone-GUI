import json
import logging
import os
import secrets

from desktop.paths import data_dir

logger = logging.getLogger(__name__)

DEFAULT_PORT = 8765


def _write_json(path, data):
    temp_path = path.with_suffix(".tmp")
    temp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(temp_path, path)


def load_settings():
    """Read settings.json, filling in and saving anything missing.

    ``timezone`` is optional and defaults to the system's timezone.
    """
    path = data_dir() / "settings.json"
    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        settings = {}
    except ValueError:
        logger.warning("%s is corrupt; replacing it", path)
        settings = {}

    defaults = {"secret_key": secrets.token_hex(32), "port": DEFAULT_PORT}
    if not defaults.keys() <= settings.keys():
        settings = {**defaults, **settings}
        _write_json(path, settings)
    return settings


def system_timezone():
    """The system's IANA timezone name (Windows has its own naming scheme)."""
    import tzlocal

    try:
        return tzlocal.get_localzone_name()
    except Exception:
        logger.warning("Could not detect the system timezone; using UTC")
        return "UTC"


def write_server_state(state):
    _write_json(data_dir() / "server.json", state)


def read_server_state():
    """The running server's pid, port and control token, or None."""
    try:
        return json.loads((data_dir() / "server.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return None


def clear_server_state():
    (data_dir() / "server.json").unlink(missing_ok=True)
