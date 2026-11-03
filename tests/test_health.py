from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.extensions import db
from app.models import Job, RunHistory, Schedule
from app.services import health

TZ = ZoneInfo("America/New_York")
GRACE = timedelta(minutes=60)
# 2026-03-10 12:00 UTC = 08:00 New York (EDT)
NOW = datetime(2026, 3, 10, 12, 0)


def make_job(name="Photos", cron=None, enabled=True, created=None, **kw):
    created = created or NOW - timedelta(days=30)
    job = Job(name=name, source="/src", destination="b2:dst", created_at=created, **kw)
    if cron:
        job.schedule = Schedule(
            cron_expression=cron, enabled=enabled, created_at=created
        )
    return job


_ids = iter(range(1, 100000))


def run(status="completed", ago=timedelta(hours=1), **kw):
    started = NOW - ago
    kw.setdefault("finished_at", started + timedelta(minutes=5))
    return RunHistory(id=next(_ids), status=status, started_at=started, **kw)


def ev(job, runs, now=NOW):
    return health.evaluate_job(job, runs, now, TZ, GRACE)


def test_healthy():
    h = ev(make_job(cron="0 2 * * *"), [run(ago=timedelta(hours=2))])
    assert h.state == "healthy"
    assert h.detail.startswith("Last success 1 hour ago") or "ago" in h.detail
    assert h.consecutive_failures == 0
    assert h.next_run == datetime(2026, 3, 11, 6, 0)  # 02:00 EDT


def test_failing_after_failed_run_and_count():
    runs = [
        run(ago=timedelta(days=3)),
        run("failed", ago=timedelta(days=2), error_message="boom"),
        run("failed", ago=timedelta(days=1), error_message="x" * 300),
    ]
    h = ev(make_job(), runs)
    assert h.state == "failing"
    assert h.consecutive_failures == 2
    assert h.detail == "Last run failed: " + "x" * 120


def test_failed_without_message_uses_error_count():
    h = ev(make_job(), [run("failed", errors=7)])
    assert h.detail == "Last run failed: 7 errors"


def test_failing_after_held_run_uses_reason():
    runs = [
        run(ago=timedelta(days=2)),
        run("held", ago=timedelta(days=1), error_message="Source is empty"),
    ]
    h = ev(make_job(), runs)
    assert h.state == "failing"
    assert h.detail == "Source is empty"


def test_running_failure_says_retrying():
    runs = [
        run("failed", ago=timedelta(hours=2), error_message="boom"),
        run("running", ago=timedelta(minutes=1), finished_at=None),
    ]
    h = ev(make_job(), runs)
    assert h.state == "failing" and h.running
    assert h.detail.endswith("(retrying now)")


@pytest.mark.parametrize("status", ["stopped", "skipped", "interrupted", "running"])
def test_ignored_statuses_do_not_count(status):
    runs = [
        run(ago=timedelta(days=2)),
        run(status, ago=timedelta(days=1)),
    ]
    h = ev(make_job(), runs)
    assert h.state == "healthy"
    assert h.consecutive_failures == 0


def test_ignored_status_between_failures():
    runs = [
        run("failed", ago=timedelta(days=3)),
        run("stopped", ago=timedelta(days=2)),
        run("failed", ago=timedelta(days=1)),
    ]
    h = ev(make_job(), runs)
    assert h.consecutive_failures == 2


def test_overdue_from_cron():
    # Last success two days ago; the 02:00 run since then never succeeded.
    runs = [run(ago=timedelta(days=2))]
    h = ev(make_job(cron="0 2 * * *"), runs)
    assert h.state == "overdue"
    assert "No successful run since" in h.detail
    assert "one was due by" in h.detail


def test_within_grace_not_overdue():
    # Success started 2026-03-09 06:00Z (02:00 EDT); next due 03-10 06:00Z.
    started_ago = NOW - datetime(2026, 3, 9, 6, 0)
    runs = [run(ago=started_ago)]
    job = make_job(cron="0 2 * * *")
    assert ev(job, runs, now=datetime(2026, 3, 10, 6, 59)).state == "healthy"
    assert ev(job, runs, now=datetime(2026, 3, 10, 7, 1)).state == "overdue"


def test_running_job_not_overdue():
    runs = [
        run(ago=timedelta(days=2)),
        run("running", ago=timedelta(minutes=30), finished_at=None),
    ]
    h = ev(make_job(cron="0 2 * * *"), runs)
    assert h.state == "healthy" and h.running


def test_disabled_schedule_not_overdue():
    h = ev(make_job(cron="0 2 * * *", enabled=False), [run(ago=timedelta(days=9))])
    assert h.state == "healthy"
    assert h.next_run is None


def test_max_age_hours():
    job = make_job(max_age_hours=24)
    assert ev(job, [run(ago=timedelta(hours=30))]).state == "overdue"
    assert "every 24 hours" in ev(job, [run(ago=timedelta(hours=30))]).detail
    assert ev(job, [run(ago=timedelta(hours=10))]).state == "healthy"


def test_max_age_never_succeeded_uses_created_at():
    job = make_job(max_age_hours=24, created=NOW - timedelta(hours=30))
    h = ev(job, [])
    assert h.state == "overdue"
    assert "since it was created" in h.detail
    job = make_job(max_age_hours=24, created=NOW - timedelta(hours=3))
    assert ev(job, []).state == "pending"


def test_max_age_overrides_schedule():
    job = make_job(cron="0 2 * * *", max_age_hours=100)
    assert ev(job, [run(ago=timedelta(days=2))]).state == "healthy"


def test_never_run_scheduled_job_overdue():
    job = make_job(cron="0 2 * * *", created=NOW - timedelta(days=2))
    h = ev(job, [])
    assert h.state == "overdue"
    assert "since it was created" in h.detail


def test_never_run_before_first_due_is_pending():
    job = make_job(cron="0 2 * * *", created=NOW - timedelta(hours=1))
    h = ev(job, [])
    assert h.state == "pending"
    assert h.detail == "Waiting for its first run"


def test_manual_job_never_run():
    h = ev(make_job(), [])
    assert h.state == "manual"
    assert h.detail == "Manual only, never run"


def test_disabled_schedule_with_only_a_stopped_run_says_so():
    job = make_job(cron="0 2 * * *", enabled=False)
    h = ev(job, [run("stopped", ago=timedelta(days=8, hours=12))])
    assert h.state == "manual"
    assert h.detail == "Last run was stopped 8 days ago · never succeeded"


def test_scheduled_job_with_only_a_stopped_run_is_pending_with_truth():
    job = make_job(cron="0 2 * * *", created=NOW - timedelta(hours=3))
    h = ev(job, [run("stopped", ago=timedelta(hours=1))])
    assert h.state == "pending"
    assert "was stopped" in h.detail and "never succeeded" in h.detail


def test_first_run_in_progress_detail():
    job = make_job(cron="0 2 * * *", created=NOW - timedelta(hours=3))
    h = ev(job, [run("running", ago=timedelta(minutes=5))])
    assert h.detail == "First run in progress"


def test_verdict_levels():
    tz = TZ
    def report(*specs):
        jobs, by = [], {}
        for i, (name, runs, kw) in enumerate(specs, start=1):
            j = make_job(name=name, **kw)
            j.id = i
            jobs.append(j)
            by[i] = runs
        return health.build_report(jobs, by, NOW, tz, GRACE)

    assert health.verdict(report(), NOW, tz)["level"] == "empty"
    ok = health.verdict(report(("A", [run()], {}), ("B", [run()], {})), NOW, tz)
    assert ok["level"] == "ok" and ok["headline"] == "All 2 backups are healthy"
    assert "2 of 2 runs succeeded this week" in ok["detail"]
    bad = health.verdict(
        report(("A", [run()], {}), ("Docs", [run("failed")], {})), NOW, tz
    )
    assert bad["level"] == "attention"
    assert bad["headline"] == "1 of 2 backups needs attention"
    assert "Check Docs." in bad["detail"]
    mixed = health.verdict(report(("A", [run()], {}), ("M", [], {})), NOW, tz)
    assert mixed["level"] == "neutral"


def test_verdict_names_next_scheduled_run():
    job = make_job(name="Photos", cron="0 2 * * *")
    job.id = 1
    rep = health.build_report([job], {1: [run()]}, NOW, TZ, GRACE)
    assert "Next: Photos " in health.verdict(rep, NOW, TZ)["detail"]


def test_when_text():
    now = datetime(2025, 6, 1, 12, 0)
    tz = ZoneInfo("UTC")
    assert health.when_text(datetime(2025, 6, 1, 20, 0), now, tz) == "today at 20:00"
    assert health.when_text(datetime(2025, 6, 2, 2, 0), now, tz) == "tomorrow at 02:00"
    assert health.when_text(datetime(2025, 6, 5, 2, 0), now, tz) == "Thu 5 Jun at 02:00"


def test_manual_job_never_overdue():
    assert ev(make_job(), [run(ago=timedelta(days=300))]).state == "healthy"


def history(deletes_latest, previous):
    runs = [run(ago=timedelta(hours=1), deletes=deletes_latest)]
    for i, d in enumerate(previous):
        runs.append(run(ago=timedelta(days=i + 1), deletes=d))
    return runs


def test_delete_anomaly_warning():
    h = ev(make_job(), history(5000, [10, 20, 30]))
    assert h.state == "warning"
    assert h.detail == "Last run deleted 5000 files (usually about 20)."


def test_delete_anomaly_needs_history_and_threshold():
    assert ev(make_job(), history(5000, [10, 20])).state == "healthy"
    assert ev(make_job(), history(100, [0, 0, 0])).state == "healthy"
    assert ev(make_job(), history(900, [100, 100, 100])).state == "healthy"
    assert ev(make_job(), history(1500, [100, 100, 100])).state == "warning"


def test_warning_clears_with_normal_success():
    runs = history(5000, [10, 20, 30])
    runs.append(run(ago=timedelta(minutes=10), deletes=5))
    assert ev(make_job(), runs).state == "healthy"


def test_failing_beats_overdue_and_warning():
    runs = history(5000, [10, 20, 30]) + [run("failed", ago=timedelta(minutes=5))]
    assert ev(make_job(), runs).state == "failing"


def test_success_rate_7d():
    runs = [
        run(ago=timedelta(days=1)),
        run(ago=timedelta(days=2)),
        run(ago=timedelta(days=3)),
        run("failed", ago=timedelta(days=4)),
        run("failed", ago=timedelta(days=20)),  # outside the window
        run("stopped", ago=timedelta(days=1)),  # not counted
    ]
    assert ev(make_job(), runs).success_rate_7d == 0.75
    assert ev(make_job(), []).success_rate_7d is None


def test_report_ordering_and_summary():
    jobs = []
    runs_by_job = {}
    specs = {
        "zeta-healthy": [run()],
        "alpha-healthy": [run()],
        "manual": [],
        "bfail": [run("failed")],
        "afail": [run("failed")],
        "overdue": [run(ago=timedelta(hours=100))],
    }
    for i, (name, runs) in enumerate(specs.items(), start=1):
        job = make_job(name=name, max_age_hours=24 if name == "overdue" else None)
        job.id = i
        jobs.append(job)
        runs_by_job[i] = runs
    report = health.build_report(jobs, runs_by_job, NOW, TZ, GRACE)
    assert [e.name for e in report["jobs"]] == [
        "afail",
        "bfail",
        "overdue",
        "alpha-healthy",
        "zeta-healthy",
        "manual",
    ]
    assert report["total"] == 6
    assert report["healthy"] == 2
    assert report["attention"] == 3
    assert report["success_rate_7d"] == pytest.approx(3 / 5)


def test_time_ago():
    assert health.time_ago(NOW - timedelta(seconds=5), NOW) == "just now"
    assert health.time_ago(NOW - timedelta(minutes=5), NOW) == "5 minutes ago"
    assert health.time_ago(NOW - timedelta(hours=1), NOW) == "1 hour ago"
    assert health.time_ago(NOW - timedelta(hours=2), NOW) == "2 hours ago"
    assert health.time_ago(NOW - timedelta(days=3), NOW) == "3 days ago"
    assert health.time_ago(NOW + timedelta(hours=3), NOW) == "in 3 hours"


# --- transitions, with the database ---


@pytest.fixture()
def events(app):
    seen = []
    app.config["EVENTS"].subscribe(lambda name, data: seen.append((name, data)))
    return seen


def add_job(**kw):
    job = Job(name="Photos", source="/s", destination="d:", **kw)
    db.session.add(job)
    db.session.commit()
    return job


def add_run(job, status, started, **kw):
    r = RunHistory(
        job_id=job.id,
        status=status,
        started_at=started,
        finished_at=started + timedelta(minutes=1),
        **kw,
    )
    db.session.add(r)
    db.session.commit()
    return r


def test_first_evaluation_is_silent(app, events):
    with app.app_context():
        job = add_job()
        health.evaluate_and_record(app, now=NOW)
        assert db.session.get(Job, job.id).health_state == "manual"
    assert events == []


def test_change_emits_event_then_quiet(app, events):
    with app.app_context():
        job = add_job()
        health.evaluate_and_record(app, now=NOW)
        add_run(job, "failed", NOW - timedelta(minutes=30), error_message="boom")
        health.evaluate_and_record(app, now=NOW)
        health.evaluate_and_record(app, now=NOW)
        stored = db.session.get(Job, job.id)
        assert stored.health_state == "failing"
        assert stored.health_changed_at == NOW
        job_id = job.id
    assert events == [
        (
            "health_changed",
            {
                "job_id": job_id,
                "job_name": "Photos",
                "old_state": "manual",
                "new_state": "failing",
                "detail": "Last run failed: boom",
            },
        )
    ]


# --- routes ---


def test_dashboard_with_no_jobs(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"No backups set up yet" in resp.data
    assert b"Create your first job" in resp.data


def test_dashboard_and_api_with_each_state(app, client):
    now = datetime.utcnow()
    with app.app_context():
        healthy = add_job()
        healthy.name = "ok"
        add_run(healthy, "completed", now - timedelta(hours=2))
        failing = Job(name="bad", source="/s", destination="d:")
        db.session.add(failing)
        stale = Job(name="stale", source="/s", destination="d:", max_age_hours=1)
        stale.created_at = now - timedelta(days=2)
        db.session.add(stale)
        db.session.add(Job(name="new", source="/s", destination="d:"))
        warn = Job(name="warn", source="/s", destination="d:")
        db.session.add(warn)
        db.session.commit()
        add_run(failing, "failed", now - timedelta(hours=1), error_message="nope")
        for i in range(4):
            add_run(
                warn,
                "completed",
                now - timedelta(days=4 - i, hours=3),
                deletes=5000 if i == 3 else 1,
            )
    resp = client.get("/")
    assert resp.status_code == 200
    for text in (b"Healthy", b"Failing", b"Overdue", b"Warning", b"Not scheduled"):
        assert text in resp.data

    data = client.get("/api/health").get_json()
    assert data["total"] == 5
    assert data["healthy"] == 1
    assert data["attention"] == 3
    assert data["pending"] == 0
    assert data["manual"] == 1
    assert data["generated_at"].endswith("Z")
    states = {j["name"]: j["state"] for j in data["jobs"]}
    assert states == {
        "ok": "healthy",
        "bad": "failing",
        "stale": "overdue",
        "new": "manual",
        "warn": "warning",
    }
    first = data["jobs"][0]
    for key in (
        "job_id",
        "name",
        "state",
        "detail",
        "running",
        "consecutive_failures",
        "last_success",
        "next_run",
        "success_rate_7d",
    ):
        assert key in first
    ok = next(j for j in data["jobs"] if j["name"] == "ok")
    assert ok["last_success"].endswith("Z")


def form(**extra):
    data = {
        "name": "J",
        "source": "/s",
        "destination": "d:",
        "sync_type": "copy",
    }
    data.update(extra)
    return data


def test_job_form_saves_max_age_hours(app, client):
    client.post("/jobs/create", data=form(max_age_hours="36"))
    with app.app_context():
        job = Job.query.one()
        assert job.max_age_hours == 36
        job_id = job.id
    assert b'value="36"' in client.get(f"/jobs/{job_id}/edit").data
    for blank in ("", "abc", "0", "-5"):
        client.post(f"/jobs/{job_id}/edit", data=form(max_age_hours=blank))
        with app.app_context():
            assert db.session.get(Job, job_id).max_age_hours is None
        client.post(f"/jobs/{job_id}/edit", data=form(max_age_hours="12"))
