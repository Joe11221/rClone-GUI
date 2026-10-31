from datetime import datetime
from zoneinfo import ZoneInfo

from app.models import Job, Schedule
from app.services.scheduler_service import SchedulerService


def _next_fire(timezone_name, now):
    svc = SchedulerService(timezone=ZoneInfo(timezone_name))
    svc.add_schedule(Schedule(id=1, job_id=1, cron_expression="0 2 * * *"))
    trigger = svc.scheduler.get_job("schedule_1").trigger
    return trigger.get_next_fire_time(None, now)


def test_cron_is_interpreted_in_configured_timezone():
    tz = ZoneInfo("America/New_York")
    fire = _next_fire("America/New_York", datetime(2026, 1, 1, 12, 0, tzinfo=tz))
    assert fire.hour == 2
    assert fire.utcoffset().total_seconds() == -5 * 3600  # EST, not UTC


def test_same_cron_differs_between_timezones():
    now = datetime(2026, 1, 1, 0, 0, tzinfo=ZoneInfo("UTC"))
    assert _next_fire("UTC", now) != _next_fire("Asia/Tokyo", now)


def test_schedule_crud(app, client):
    client.post(
        "/jobs/create",
        data={"name": "j", "source": "/a", "destination": "b:", "sync_type": "sync"},
    )
    with app.app_context():
        job_id = Job.query.one().id

    client.post(
        "/schedules/create",
        data={"job_id": job_id, "cron_expression": "0 3 * * *", "enabled": "on"},
    )
    with app.app_context():
        sched = Schedule.query.one()
        assert sched.enabled
        sched_id = sched.id
        scheduler = app.config["SCHEDULER_SERVICE"].scheduler
        assert scheduler.get_job(f"schedule_{sched_id}")

    client.post(f"/schedules/{sched_id}/toggle")
    with app.app_context():
        assert not Schedule.query.one().enabled
        scheduler = app.config["SCHEDULER_SERVICE"].scheduler
        assert scheduler.get_job(f"schedule_{sched_id}") is None

    client.post(f"/schedules/{sched_id}/delete")
    with app.app_context():
        assert Schedule.query.count() == 0
