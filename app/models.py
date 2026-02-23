import json
from datetime import datetime

from app.extensions import db


class Job(db.Model):
    """A reusable rclone job definition."""

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    source = db.Column(db.String(500), nullable=False)
    destination = db.Column(db.String(500), nullable=False)
    sync_type = db.Column(db.String(20), nullable=False, default="sync")
    transfers = db.Column(db.Integer, default=4)
    checkers = db.Column(db.Integer, default=8)
    fast_list = db.Column(db.Boolean, default=True)
    bwlimit = db.Column(db.String(50), default="")
    exclude_patterns = db.Column(db.Text, default="[]")
    retries = db.Column(db.Integer, default=3)
    order_by_field = db.Column(db.String(20), default="size")
    order_by_direction = db.Column(db.String(20), default="mixed")
    order_by_mixed_window = db.Column(db.Integer, default=50)
    extra_flags = db.Column(db.Text, default="{}")
    auto_resume = db.Column(db.Boolean, default=False)
    max_retries = db.Column(db.Integer, default=3)
    retry_delay_seconds = db.Column(db.Integer, default=60)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    schedule = db.relationship(
        "Schedule", backref="job", uselist=False, cascade="all,delete-orphan"
    )
    runs = db.relationship(
        "RunHistory", backref="job", lazy="dynamic", cascade="all,delete-orphan"
    )

    def get_excludes(self):
        return json.loads(self.exclude_patterns)

    def get_extra_flags(self):
        return json.loads(self.extra_flags)

    def to_rclone_config(self):
        config = {
            "Transfers": self.transfers,
            "Checkers": self.checkers,
            "Retries": self.retries,
        }
        if self.order_by_field:
            order_by = self.order_by_field
            direction = (self.order_by_direction or "").lower().strip()
            if direction == "mixed":
                try:
                    mixed_window = int(self.order_by_mixed_window or 50)
                except (TypeError, ValueError):
                    mixed_window = 50
                mixed_window = max(0, min(100, mixed_window))
                order_by = f"{order_by},mixed,{mixed_window}"
            elif direction in ("asc", "desc"):
                order_by = f"{order_by},{direction}"
            config["OrderBy"] = order_by
        if self.fast_list:
            config["FastList"] = True
        if self.bwlimit:
            config["BwLimit"] = self.bwlimit
        config.update(self.get_extra_flags())
        return config

    def to_rclone_filter(self):
        excludes = self.get_excludes()
        if excludes:
            return {"ExcludeRule": excludes}
        return None


class Schedule(db.Model):
    """Cron-like schedule attached to a Job."""

    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(
        db.Integer, db.ForeignKey("job.id"), nullable=False, unique=True
    )
    enabled = db.Column(db.Boolean, default=True)
    cron_expression = db.Column(db.String(100), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class RunHistory(db.Model):
    """One record per job execution."""

    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.Integer, db.ForeignKey("job.id"), nullable=False)
    rclone_jobid = db.Column(db.Integer, nullable=True)
    stats_group = db.Column(db.String(50), nullable=True)
    started_at = db.Column(db.DateTime, default=datetime.utcnow)
    finished_at = db.Column(db.DateTime, nullable=True)
    status = db.Column(db.String(20), default="running")
    bytes_transferred = db.Column(db.BigInteger, default=0)
    files_transferred = db.Column(db.Integer, default=0)
    errors = db.Column(db.Integer, default=0)
    error_message = db.Column(db.Text, nullable=True)
    triggered_by = db.Column(db.String(20), default="manual")
    retry_of_run_id = db.Column(
        db.Integer, db.ForeignKey("run_history.id"), nullable=True
    )
    retry_count = db.Column(db.Integer, default=0)

    retry_of = db.relationship(
        "RunHistory", remote_side="RunHistory.id", uselist=False
    )
