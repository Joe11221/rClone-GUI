import logging
import logging.handlers
import os
import shutil
import sys
from pathlib import Path

APP_NAME = "rClone GUI"


def is_frozen():
    return getattr(sys, "frozen", False)


def resource_dir():
    """Where bundled files live: PyInstaller's folder, or the repo in dev."""
    if is_frozen():
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent.parent


def data_dir():
    """The user's writable data folder, created if needed."""
    override = os.environ.get("RCLONE_GUI_DATA_DIR")
    path = Path(override) if override else Path(os.environ["LOCALAPPDATA"]) / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def log_dir():
    path = data_dir() / "logs"
    path.mkdir(exist_ok=True)
    return path


def icon_path():
    return resource_dir() / "desktop" / "assets" / "icon.ico"


def rclone_binary():
    candidates = [
        resource_dir() / "bin" / "rclone.exe",
        resource_dir() / "packaging" / "bin" / "rclone.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    on_path = shutil.which("rclone")
    if on_path:
        return Path(on_path)
    raise FileNotFoundError("rclone.exe not found")


def app_command(*args):
    """The command line that re-launches this app with the given arguments."""
    if is_frozen():
        return [sys.executable, *args]
    return [sys.executable, "-m", "desktop", *args]


def configure_logging(filename):
    handler = logging.handlers.RotatingFileHandler(
        log_dir() / filename, maxBytes=2_000_000, backupCount=2, encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    # Otherwise it logs every run of the 10-second monitor.
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
