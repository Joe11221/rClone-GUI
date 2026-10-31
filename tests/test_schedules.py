from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.extensions import db
from app.models import Job, RunHistory, Schedule
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


def _service(catch_up=True):
    return SchedulerService(timezone=ZoneInfo("UTC"), catch_up=catch_up)


def _daily_schedule(**kwargs):
    return Schedule(id=1, job_id=1, cron_expression="0 2 * * *", **kwargs)


def test_missed_run_is_detected_from_baseline():
    two_days_ago = datetime.utcnow() - timedelta(days=2)
    assert _service()._missed_a_run(_daily_schedule(catch_up_after=two_days_ago))


def test_recent_baseline_has_nothing_to_catch_up():
    assert not _service()._missed_a_run(
        _daily_schedule(catch_up_after=datetime.utcnow())
    )


def test_baseline_falls_back_to_creation_time():
    two_days_ago = datetime.utcnow() - timedelta(days=2)
    assert _service()._missed_a_run(_daily_schedule(created_at=two_days_ago))
    assert not _service()._missed_a_run(_daily_schedule())


def test_late_runs_get_the_configured_grace_and_coalesce():
    svc = SchedulerService(timezone=ZoneInfo("UTC"), misfire_grace_seconds=None)
    svc.add_schedule(_daily_schedule())
    job = svc.scheduler.get_job("schedule_1")
    assert job.misfire_grace_time is None
    assert job.coalesce is True


def _job_with_schedule(app, client, **schedule_kwargs):
    client.post(
        "/jobs/create",
        data={"name": "j", "source": "/a", "destination": "b:", "sync_type": "sync"},
    )
    with app.app_context():
        job = Job.query.one()
        db.session.add(
            Schedule(job_id=job.id, cron_expression="0 2 * * *", **schedule_kwargs)
        )
        db.session.commit()
        return job.id


def test_scheduled_run_records_baseline_and_skips_overlap(app, client, fake_rclone):
    job_id = _job_with_schedule(app, client)
    svc = app.config["SCHEDULER_SERVICE"]

    svc._run_scheduled_job(job_id)
    svc._run_scheduled_job(job_id)  # still running: must not start a second copy

    assert len(fake_rclone.calls) == 1
    with app.app_context():
        assert RunHistory.query.count() == 1
        assert Schedule.query.one().catch_up_after is not None


def test_manual_start_of_running_job_is_refused(app, client, fake_rclone):
    job_id = _job_with_schedule(app, client)
    client.post(f"/ops/start/{job_id}")
    resp = client.post(f"/ops/start/{job_id}", follow_redirects=True)
    assert b"already running" in resp.data
    assert len(fake_rclone.calls) == 1


def test_catch_up_is_queued_only_when_enabled(app, client, fake_rclone):
    two_days_ago = datetime.utcnow() - timedelta(days=2)
    _job_with_schedule(app, client, catch_up_after=two_days_ago)
    svc = app.config["SCHEDULER_SERVICE"]

    svc._load_schedules_from_db(catch_up=False)
    with app.app_context():
        sched_id = Schedule.query.one().id
    assert svc.scheduler.get_job(f"catch_up_{sched_id}") is None

    svc._load_schedules_from_db(catch_up=True)
    assert svc.scheduler.get_job(f"catch_up_{sched_id}") is not None


def test_resume_skips_what_came_due_while_paused(app, client, fake_rclone):
    two_days_ago = datetime.utcnow() - timedelta(days=2)
    _job_with_schedule(app, client, catch_up_after=two_days_ago)
    svc = app.config["SCHEDULER_SERVICE"]
    svc.scheduler.start(paused=True)
    try:
        assert svc.paused
        svc.resume()
        assert not svc.paused
        with app.app_context():
            assert not svc._missed_a_run(Schedule.query.one())
    finally:
        svc.shutdown()


def test_enabling_a_schedule_resets_the_baseline(app, client, fake_rclone):
    two_days_ago = datetime.utcnow() - timedelta(days=2)
    _job_with_schedule(app, client, enabled=False, catch_up_after=two_days_ago)
    with app.app_context():
        sched_id = Schedule.query.one().id
    client.post(f"/schedules/{sched_id}/toggle")
    with app.app_context():
        assert Schedule.query.one().catch_up_after > two_days_ago
