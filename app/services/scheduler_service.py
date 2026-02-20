from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger


class SchedulerService:
    def __init__(self):
        self.scheduler = BackgroundScheduler()
        self.job_runner = None
        self.app = None

    def init_app(self, app, job_runner):
        self.app = app
        self.job_runner = job_runner

    def start(self):
        self.scheduler.start()
        self._load_schedules_from_db()

    def _load_schedules_from_db(self):
        from app.models import Schedule

        with self.app.app_context():
            schedules = Schedule.query.filter_by(enabled=True).all()
            for sched in schedules:
                self.add_schedule(sched)

    def add_schedule(self, schedule):
        job_id = f"schedule_{schedule.id}"
        trigger = CronTrigger.from_crontab(schedule.cron_expression)
        self.scheduler.add_job(
            func=self._run_scheduled_job,
            trigger=trigger,
            id=job_id,
            replace_existing=True,
            args=[schedule.job_id],
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

        with self.app.app_context():
            job = db.session.get(Job, job_id)
            if job:
                self.job_runner.start_job(job, triggered_by="schedule")

    def get_next_run(self, schedule_id):
        job_id = f"schedule_{schedule_id}"
        try:
            job = self.scheduler.get_job(job_id)
            if job:
                return job.next_run_time
        except Exception:
            pass
        return None

    def shutdown(self):
        self.scheduler.shutdown(wait=False)
