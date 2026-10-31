import logging
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.base import STATE_PAUSED
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)


class SchedulerService:
    def __init__(self, timezone=None, misfire_grace_seconds=3600, catch_up=False):
        # Cron expressions are interpreted in this timezone (None = server local).
        self.timezone = timezone
        self.misfire_grace_seconds = misfire_grace_seconds
        self.catch_up = catch_up
        self.scheduler = BackgroundScheduler(timezone=timezone)
        self.job_runner = None
        self.app = None

    def init_app(self, app, job_runner):
        self.app = app
        self.job_runner = job_runner

    def start(self):
        self.scheduler.start()
        self._load_schedules_from_db(catch_up=self.catch_up)
        # The scheduler sleeps until its next job, and that timer can't be
        # relied on across system sleep. Waking it regularly means runs that
        # came due overnight start soon after the machine resumes.
        self.scheduler.add_job(
            lambda: None, "interval", minutes=1, id="wake_after_sleep"
        )

    def _load_schedules_from_db(self, catch_up=False):
        from app.models import Schedule

        with self.app.app_context():
            schedules = Schedule.query.filter_by(enabled=True).all()
            for sched in schedules:
                self.add_schedule(sched)
                if catch_up and self._missed_a_run(sched):
                    logger.info("Catching up missed run of schedule %d", sched.id)
                    self.scheduler.add_job(
                        func=self._run_scheduled_job,
                        id=f"catch_up_{sched.id}",
                        replace_existing=True,
                        args=[sched.job_id],
                        misfire_grace_time=None,
                    )

    def _missed_a_run(self, schedule):
        """True if a cron occurrence came due after the schedule's baseline."""
        baseline = schedule.catch_up_after or schedule.created_at
        if baseline is None:
            return False
        trigger = CronTrigger.from_crontab(
            schedule.cron_expression, timezone=self.timezone
        )
        next_fire = trigger.get_next_fire_time(
            None, baseline.replace(tzinfo=dt_timezone.utc) + timedelta(seconds=1)
        )
        return next_fire is not None and next_fire <= datetime.now(dt_timezone.utc)

    def add_schedule(self, schedule):
        job_id = f"schedule_{schedule.id}"
        trigger = CronTrigger.from_crontab(
            schedule.cron_expression, timezone=self.timezone
        )
        self.scheduler.add_job(
            func=self._run_scheduled_job,
            trigger=trigger,
            id=job_id,
            replace_existing=True,
            args=[schedule.job_id],
            misfire_grace_time=self.misfire_grace_seconds,
            coalesce=True,
        )

    def remove_schedule(self, schedule_id):
        job_id = f"schedule_{schedule_id}"
        try:
            self.scheduler.remove_job(job_id)
        except Exception:
            pass

    def _run_scheduled_job(self, job_id):
        from app.extensions import db
        from app.models import Job
        from app.services.job_runner import JobAlreadyRunning

        with self.app.app_context():
            job = db.session.get(Job, job_id)
            if job:
                if job.schedule:
                    job.schedule.catch_up_after = datetime.utcnow()
                    db.session.commit()
                try:
                    self.job_runner.start_job(job, triggered_by="schedule")
                except JobAlreadyRunning as exc:
                    logger.info("Skipping scheduled run: %s", exc)

    def get_next_run(self, schedule_id):
        job_id = f"schedule_{schedule_id}"
        try:
            job = self.scheduler.get_job(job_id)
            if job:
                return job.next_run_time
        except Exception:
            pass
        return None

    @property
    def paused(self):
        return self.scheduler.state == STATE_PAUSED

    def pause(self):
        self.scheduler.pause()

    def resume(self):
        """Resume without running what came due while paused."""
        from app.extensions import db
        from app.models import Schedule

        with self.app.app_context():
            now = datetime.utcnow()
            for sched in Schedule.query.filter_by(enabled=True):
                sched.catch_up_after = now
            db.session.commit()
        # Re-adding recalculates next_run_time from now.
        self._load_schedules_from_db()
        self.scheduler.resume()

    def shutdown(self):
        self.scheduler.shutdown(wait=False)
