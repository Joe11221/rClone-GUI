import os


class Config:
    # No default on purpose: create_app() refuses to start without a real key.
    SECRET_KEY = os.environ.get("SECRET_KEY", "")
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", "sqlite:///rclone_gui.db"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    RCLONE_RC_URL = os.environ.get("RCLONE_RC_URL", "http://localhost:5572")
    RCLONE_RC_USER = os.environ.get("RCLONE_RC_USER", "")
    RCLONE_RC_PASS = os.environ.get("RCLONE_RC_PASS", "")
    # IANA name (e.g. "America/New_York"). Used both for displaying times and
    # for interpreting cron schedules.
    TIMEZONE = os.environ.get("TIMEZONE", "UTC")
    # Tests turn this off so no scheduler threads are started.
    BACKGROUND_TASKS_ENABLED = True
