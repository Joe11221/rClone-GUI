import os
from datetime import timedelta


class Config:
    # Empty = create_app() generates one and keeps it in the instance folder.
    # Placeholder values are refused.
    SECRET_KEY = os.environ.get("SECRET_KEY", "")
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", "sqlite:///rclone_gui.db"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # Localhost cookies are shared across ports, so the name must be our own.
    SESSION_COOKIE_NAME = "rclone_gui_session"
    SESSION_COOKIE_SAMESITE = "Lax"
    # Browser sessions last 30 days after logging in.
    PERMANENT_SESSION_LIFETIME = timedelta(days=30)
    SESSION_COOKIE_HTTPONLY = True
    # Leave off for plain-http LAN use; turn on when served over https.
    SESSION_COOKIE_SECURE = os.environ.get("SECURE_COOKIES", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    # "password" (single-user login), "proxy" (a reverse proxy logs users in and
    # sends AUTH_PROXY_HEADER) or "none" (no login at all).
    AUTH_MODE = os.environ.get("AUTH_MODE", "password").strip().lower() or "password"
    AUTH_PROXY_HEADER = os.environ.get("AUTH_PROXY_HEADER", "Remote-User").strip()
    # Host headers to accept, e.g. "nas.lan:8080,127.0.0.1:8080". Empty = any.
    ALLOWED_HOSTS = [
        host.strip()
        for host in os.environ.get("ALLOWED_HOSTS", "").split(",")
        if host.strip()
    ]
    RCLONE_RC_URL = os.environ.get("RCLONE_RC_URL", "http://localhost:5572")
    RCLONE_RC_USER = os.environ.get("RCLONE_RC_USER", "")
    RCLONE_RC_PASS = os.environ.get("RCLONE_RC_PASS", "")
    # Run directly for browser sign-ins, which rclone's API doesn't offer.
    RCLONE_BINARY = os.environ.get("RCLONE_BINARY", "rclone")
    # IANA name (e.g. "America/New_York"). Used both for displaying times and
    # for interpreting cron schedules.
    TIMEZONE = os.environ.get("TIMEZONE", "UTC")
    # How late a cron run may start after coming due (e.g. the machine was
    # asleep) before it is skipped. None = no limit.
    SCHEDULE_MISFIRE_GRACE_SECONDS = 3600
    # Run schedules once at startup if they came due while the app was closed.
    SCHEDULE_CATCH_UP = False
    # True when running as the tray app rather than in Docker.
    DESKTOP_MODE = False
    # Tests turn this off so no scheduler threads are started.
    BACKGROUND_TASKS_ENABLED = True
