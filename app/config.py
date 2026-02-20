import os


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key-change-me")
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", "sqlite:///rclone_gui.db"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    RCLONE_RC_URL = os.environ.get("RCLONE_RC_URL", "http://localhost:5572")
    RCLONE_RC_USER = os.environ.get("RCLONE_RC_USER", "")
    RCLONE_RC_PASS = os.environ.get("RCLONE_RC_PASS", "")
