from datetime import timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Flask

from app.config import Config
from app.extensions import db
from app.rclone_client import RcloneClient
from app.security import init_request_guards
from app.services.job_runner import JobRunner
from app.services.scheduler_service import SchedulerService


# Values that must never be used to sign sessions (placeholders that ship in
# example files or older versions of this project).
_INSECURE_SECRET_KEYS = {
    "",
    "change-me",
    "change-me-in-production",
    "dev-secret-key-change-me",
}


def _require_secret_key(app):
    if app.config.get("SECRET_KEY") in _INSECURE_SECRET_KEYS:
        raise RuntimeError(
            "SECRET_KEY is not set (or is a placeholder). Generate one with "
            "`python -c \"import secrets; print(secrets.token_hex(32))\"` "
            "and set it in your .env file."
        )


def _load_timezone(name):
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise RuntimeError(
            f"Invalid TIMEZONE {name!r}; use an IANA name such as "
            "'UTC' or 'America/New_York'."
        ) from exc


def _ensure_columns():
    """Add new columns to existing SQLite tables (create_all won't do this).

    This is the app's only schema migration mechanism: ``db.create_all()``
    creates missing tables and each ALTER TABLE below adds a column that older
    databases lack. Statements are tried individually and duplicate-column
    errors are ignored, so it is safe to run on every startup.
    """
    from sqlalchemy import text

    migrations = [
        "ALTER TABLE job ADD COLUMN auto_resume BOOLEAN DEFAULT 0",
        "ALTER TABLE job ADD COLUMN max_retries INTEGER DEFAULT 3",
        "ALTER TABLE job ADD COLUMN retry_delay_seconds INTEGER DEFAULT 60",
        "ALTER TABLE job ADD COLUMN order_by_field VARCHAR(20) DEFAULT 'size'",
        "ALTER TABLE job ADD COLUMN order_by_direction VARCHAR(20) DEFAULT 'mixed'",
        "ALTER TABLE job ADD COLUMN order_by_mixed_window INTEGER DEFAULT 50",
        "ALTER TABLE job ADD COLUMN compare_method VARCHAR(20) DEFAULT 'default'",
        "ALTER TABLE run_history ADD COLUMN retry_of_run_id INTEGER",
        "ALTER TABLE run_history ADD COLUMN retry_count INTEGER DEFAULT 0",
        "ALTER TABLE schedule ADD COLUMN catch_up_after DATETIME",
    ]
    for sql in migrations:
        try:
            db.session.execute(text(sql))
            db.session.commit()
        except Exception:
            db.session.rollback()


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)
    _require_secret_key(app)
    tz = _load_timezone(app.config["TIMEZONE"])

    init_request_guards(app)

    # Initialize extensions
    db.init_app(app)

    # Initialize rclone client
    rclone = RcloneClient(
        base_url=app.config["RCLONE_RC_URL"],
        user=app.config.get("RCLONE_RC_USER") or None,
        password=app.config.get("RCLONE_RC_PASS") or None,
    )
    app.config["RCLONE_CLIENT"] = rclone

    # Initialize job runner
    job_runner = JobRunner(rclone)
    app.config["JOB_RUNNER"] = job_runner

    # Initialize scheduler
    scheduler_service = SchedulerService(
        timezone=tz,
        misfire_grace_seconds=app.config["SCHEDULE_MISFIRE_GRACE_SECONDS"],
        catch_up=app.config["SCHEDULE_CATCH_UP"],
    )
    scheduler_service.init_app(app, job_runner)
    app.config["SCHEDULER_SERVICE"] = scheduler_service

    # Periodic task to finalize completed runs
    from apscheduler.schedulers.background import BackgroundScheduler

    monitor = BackgroundScheduler(timezone=tz)

    def _check_runs():
        with app.app_context():
            job_runner.check_and_update_runs()

    def _collect_errors():
        with app.app_context():
            job_runner.collect_active_errors()

    monitor.add_job(_check_runs, "interval", seconds=10)
    # rclone only remembers its last ~100 completed transfers, so failures
    # must be picked up quickly. Overlap is harmless: entries are de-duplicated.
    monitor.add_job(
        _collect_errors, "interval", seconds=3, max_instances=1, coalesce=True
    )
    if app.config["BACKGROUND_TASKS_ENABLED"]:
        monitor.start()

    # Register blueprints
    from app.routes.history import history_bp
    from app.routes.jobs import jobs_bp
    from app.routes.operations import ops_bp
    from app.routes.run_errors import run_errors_bp
    from app.routes.schedules import schedules_bp
    from app.routes.settings import settings_bp
    from app.routes.sse import sse_bp

    app.register_blueprint(jobs_bp, url_prefix="/jobs")
    app.register_blueprint(ops_bp, url_prefix="/ops")
    app.register_blueprint(schedules_bp, url_prefix="/schedules")
    app.register_blueprint(history_bp, url_prefix="/history")
    app.register_blueprint(run_errors_bp, url_prefix="/runs")
    app.register_blueprint(sse_bp)
    app.register_blueprint(settings_bp, url_prefix="/settings")

    @app.context_processor
    def _inject_desktop_mode():
        return {"desktop_mode": app.config["DESKTOP_MODE"]}

    # --- Jinja2 template filters ---

    @app.template_filter("local_time")
    def local_time_filter(dt, fmt="%Y-%m-%d %H:%M"):
        """Convert a naive-UTC datetime to the configured local timezone."""
        if dt is None:
            return "--"
        utc_dt = dt.replace(tzinfo=timezone.utc)
        return utc_dt.astimezone(tz).strftime(fmt)

    @app.template_filter("local_time_sec")
    def local_time_sec_filter(dt):
        """Like local_time but includes seconds."""
        return local_time_filter(dt, fmt="%Y-%m-%d %H:%M:%S")

    @app.template_filter("fmt_duration")
    def fmt_duration_filter(td):
        """Format a timedelta as a human-readable string (e.g. '1h 23m 45s')."""
        if td is None:
            return "--"
        total = int(td.total_seconds())
        if total < 0:
            return "--"
        hours, remainder = divmod(total, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours > 0:
            return f"{hours}h {minutes}m {seconds}s"
        if minutes > 0:
            return f"{minutes}m {seconds}s"
        return f"{seconds}s"

    @app.template_filter("fmt_bytes")
    def fmt_bytes_filter(b):
        """Format bytes as a human-readable string (e.g. '1.2 GB')."""
        if b is None:
            return "--"
        b = float(b)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if abs(b) < 1024:
                return f"{b:.1f} {unit}"
            b /= 1024
        return f"{b:.1f} PB"

    # Dashboard route
    from flask import redirect, render_template, url_for

    from app.models import Job, RunHistory

    @app.route("/")
    def dashboard():
        jobs = Job.query.order_by(Job.name).all()
        active_runs = RunHistory.query.filter_by(status="running").all()
        recent_runs = (
            RunHistory.query.order_by(RunHistory.started_at.desc()).limit(10).all()
        )
        rclone_connected = False
        try:
            rclone.list_remotes()
            rclone_connected = True
        except Exception:
            pass
        return render_template(
            "dashboard.html",
            jobs=jobs,
            active_runs=active_runs,
            recent_runs=recent_runs,
            rclone_connected=rclone_connected,
        )

    # Create tables, add any missing columns, and start the scheduler
    with app.app_context():
        db.create_all()
        _ensure_columns()
        job_runner.restore_active_runs()
        if app.config["BACKGROUND_TASKS_ENABLED"]:
            scheduler_service.start()

    return app
