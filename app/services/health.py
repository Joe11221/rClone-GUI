"""Backup health: is each job succeeding on time?

``evaluate_job`` and ``build_report`` are pure given explicit ``now`` (naive
UTC, like every DB datetime), the display timezone and the grace period.
``load_runs`` fetches only the runs the rules need, and
``evaluate_and_record`` stores state changes and emits ``health_changed``.
"""

import logging
import statistics
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import and_, or_

logger = logging.getLogger(__name__)

FAILURE_STATUSES = ("failed", "held")
ATTENTION_STATES = ("failing", "overdue", "warning")
_STATE_ORDER = {
    "failing": 0,
    "overdue": 1,
    "warning": 2,
    "pending": 3,
    "healthy": 4,
}
_TIME_FMT = "%Y-%m-%d %H:%M"


@dataclass
class JobHealth:
    job_id: int
    name: str
    source: str
    destination: str
    state: str
    detail: str
    running: bool
    consecutive_failures: int
    last_success: datetime = None
    last_run: datetime = None
    last_run_status: str = None
    next_run: datetime = None
    success_rate_7d: float = None

    def to_dict(self):
        data = asdict(self)
        for key in ("last_success", "last_run", "next_run"):
            data[key] = iso_z(data[key])
        return data


def iso_z(dt):
    return dt.isoformat() + "Z" if dt else None


def time_ago(dt, now):
    """'just now', '5 minutes ago', 'in 3 hours' for a naive-UTC datetime."""
    if dt is None:
        return "never"
    seconds = int((now - dt).total_seconds())
    future = seconds < 0
    seconds = abs(seconds)
    if seconds < 60:
        return "in a moment" if future and seconds >= 10 else "just now"
    for size, unit in ((86400, "day"), (3600, "hour"), (60, "minute")):
        if seconds >= size:
            n = seconds // size
            text = f"{n} {unit}{'' if n == 1 else 's'}"
            return f"in {text}" if future else f"{text} ago"


def _local_text(dt, tz):
    return dt.replace(tzinfo=dt_timezone.utc).astimezone(tz).strftime(_TIME_FMT)


def next_fire(cron_expression, after, tz):
    """First cron fire time strictly after ``after`` (naive UTC), as naive UTC."""
    try:
        trigger = CronTrigger.from_crontab(cron_expression, timezone=tz)
        fire = trigger.get_next_fire_time(
            None, after.replace(tzinfo=dt_timezone.utc) + timedelta(microseconds=1)
        )
    except Exception:
        logger.warning("Cannot evaluate cron %r", cron_expression)
        return None
    if fire is None:
        return None
    return fire.astimezone(dt_timezone.utc).replace(tzinfo=None)


def _run_key(run):
    return (run.started_at or datetime.min, run.id or 0)


def _finished(run):
    return run.finished_at or run.started_at


def evaluate_job(job, runs, now, tz, grace):
    """Health of one job from its relevant ``runs`` (any order)."""
    runs = sorted(runs, key=_run_key, reverse=True)
    schedule = job.schedule
    enabled_schedule = schedule if schedule is not None and schedule.enabled else None

    completed = [r for r in runs if r.status == "completed"]
    last_success = completed[0] if completed else None
    if last_success is None:
        newer = runs
    else:
        newer = [r for r in runs if _run_key(r) > _run_key(last_success)]
    failures = [r for r in newer if r.status in FAILURE_STATUSES]
    running = any(r.status == "running" for r in runs)
    last_run = runs[0] if runs else None

    week_ago = now - timedelta(days=7)
    recent = [r for r in runs if r.started_at and r.started_at >= week_ago]
    ok = sum(1 for r in recent if r.status == "completed")
    bad = sum(1 for r in recent if r.status in FAILURE_STATUSES)
    rate = ok / (ok + bad) if ok + bad else None

    next_run = None
    if enabled_schedule:
        next_run = next_fire(enabled_schedule.cron_expression, now, tz)

    state, detail = None, ""
    if failures:
        newest = failures[0]
        msg = (newest.error_message or "").strip()
        if newest.status == "held":
            detail = msg or "Held by a safety check."
        elif msg:
            detail = "Last run failed: " + msg[:120]
        elif newest.errors:
            detail = f"Last run failed: {newest.errors} errors"
        else:
            detail = "Last run failed"
        if running:
            detail += " (retrying now)"
        state = "failing"

    if state is None:
        overdue = _overdue_detail(
            job, enabled_schedule, last_success, running, now, tz, grace
        )
        if overdue:
            state, detail = "overdue", overdue

    if state is None and last_success is not None:
        previous = [r.deletes or 0 for r in completed[1:11]]
        if len(previous) >= 3:
            usual = statistics.median(previous)
            if (last_success.deletes or 0) > max(100, 10 * usual):
                state = "warning"
                detail = (
                    f"Last run deleted {last_success.deletes} files "
                    f"(usually about {int(usual)})."
                )

    if state is None and last_success is not None:
        state = "healthy"
        detail = "Last success " + time_ago(_finished(last_success), now)
    if state is None:
        state = "pending"
        detail = (
            "Waiting for its first run"
            if enabled_schedule or runs
            else "Not scheduled"
        )

    return JobHealth(
        job_id=job.id,
        name=job.name,
        source=job.source,
        destination=job.destination,
        state=state,
        detail=detail,
        running=running,
        consecutive_failures=len(failures),
        last_success=_finished(last_success) if last_success else None,
        last_run=last_run.started_at if last_run else None,
        last_run_status=last_run.status if last_run else None,
        next_run=next_run,
        success_rate_7d=rate,
    )


def _overdue_detail(job, schedule, last_success, running, now, tz, grace):
    since = (
        f"since {_local_text(_finished(last_success), tz)}"
        if last_success
        else "since it was created"
    )
    if job.max_age_hours:
        reference = _finished(last_success) if last_success else job.created_at
        if reference and now - reference > timedelta(hours=job.max_age_hours):
            return (
                f"No successful run {since}; one was due at least every "
                f"{job.max_age_hours} hours."
            )
        return None
    if schedule is None or running:
        return None
    if last_success:
        baseline = last_success.started_at
    else:
        stamps = [d for d in (job.created_at, schedule.created_at) if d]
        baseline = max(stamps) if stamps else None
    if baseline is None:
        return None
    first_due = next_fire(schedule.cron_expression, baseline, tz)
    if first_due is not None and now > first_due + grace:
        return (
            f"No successful run {since}; one was due by "
            f"{_local_text(first_due, tz)}."
        )
    return None


def sort_entries(entries):
    return sorted(entries, key=lambda e: (_STATE_ORDER[e.state], e.name.lower()))


def build_report(jobs, runs_by_job, now, tz, grace):
    """Per-job entries (attention first) plus summary counts."""
    entries = sort_entries(
        [
            evaluate_job(job, runs_by_job.get(job.id, []), now, tz, grace)
            for job in jobs
        ]
    )
    ok = bad = 0
    week_ago = now - timedelta(days=7)
    for runs in runs_by_job.values():
        for r in runs:
            if r.started_at and r.started_at >= week_ago:
                if r.status == "completed":
                    ok += 1
                elif r.status in FAILURE_STATUSES:
                    bad += 1
    return {
        "generated_at": now,
        "total": len(entries),
        "healthy": sum(1 for e in entries if e.state == "healthy"),
        "attention": sum(1 for e in entries if e.state in ATTENTION_STATES),
        "pending": sum(1 for e in entries if e.state == "pending"),
        "success_rate_7d": ok / (ok + bad) if ok + bad else None,
        "jobs": entries,
    }


def report_to_dict(report):
    return {
        "generated_at": iso_z(report["generated_at"]),
        "total": report["total"],
        "healthy": report["healthy"],
        "attention": report["attention"],
        "pending": report["pending"],
        "success_rate_7d": report["success_rate_7d"],
        "jobs": [e.to_dict() for e in report["jobs"]],
    }


def load_runs(jobs, now):
    """Only the runs the rules need, per job id: failures since the last
    success, the last 11 completed runs, running runs, the last 7 days and the
    newest run."""
    from app.models import RunHistory

    result = {}
    week_ago = now - timedelta(days=7)
    newest_first = (RunHistory.started_at.desc(), RunHistory.id.desc())
    for job in jobs:
        base = RunHistory.query.filter_by(job_id=job.id)
        completed = base.filter_by(status="completed").order_by(*newest_first)
        last_ok = completed.first()
        since = base.filter(RunHistory.status.in_(FAILURE_STATUSES))
        if last_ok is not None:
            since = since.filter(
                or_(
                    RunHistory.started_at > last_ok.started_at,
                    and_(
                        RunHistory.started_at == last_ok.started_at,
                        RunHistory.id > last_ok.id,
                    ),
                )
            )
        queries = [
            completed.limit(11),
            base.filter(
                or_(
                    RunHistory.status == "running",
                    RunHistory.started_at >= week_ago,
                )
            ),
            base.order_by(*newest_first).limit(1),
            since.limit(1000),
        ]
        found = {}
        for query in queries:
            for run in query.all():
                found[run.id] = run
        result[job.id] = list(found.values())
    return result


def current_report(tz_name, grace_minutes, now=None):
    """(report, jobs) for the current database contents."""
    from zoneinfo import ZoneInfo

    from app.models import Job

    now = now or datetime.utcnow()
    jobs = Job.query.order_by(Job.name).all()
    report = build_report(
        jobs,
        load_runs(jobs, now),
        now,
        ZoneInfo(tz_name),
        timedelta(minutes=grace_minutes),
    )
    return report, jobs


def evaluate_and_record(app, events=None, now=None):
    """Re-evaluate every job; store and announce state changes.

    The first evaluation of a job (stored state None) is recorded silently.
    """
    from app.extensions import db

    with app.app_context():
        events = events or app.config.get("EVENTS")
        now = now or datetime.utcnow()
        report, jobs = current_report(
            app.config["TIMEZONE"], app.config["HEALTH_GRACE_MINUTES"], now
        )
        by_id = {j.id: j for j in jobs}
        changes = []
        for entry in report["jobs"]:
            job = by_id[entry.job_id]
            if job.health_state != entry.state:
                old = job.health_state
                job.health_state = entry.state
                job.health_changed_at = now
                if old is not None:
                    changes.append((entry, old))
        db.session.commit()
        if events is not None:
            for entry, old in changes:
                events.emit(
                    "health_changed",
                    job_id=entry.job_id,
                    job_name=entry.name,
                    old_state=old,
                    new_state=entry.state,
                    detail=entry.detail,
                )
        return report
