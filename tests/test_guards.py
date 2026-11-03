import json
from datetime import datetime

import pytest

from app.extensions import db
from app.models import Job, RunHistory
from app.services.job_runner import JobAlreadyRunning


@pytest.fixture()
def events(app):
    seen = []
    app.config["EVENTS"].subscribe(lambda name, data: seen.append((name, data)))
    return seen


def make_job(sync_type="sync", **kwargs):
    job = Job(
        name="Photos",
        source="/mnt/photos",
        destination="b2:bucket",
        sync_type=sync_type,
        **kwargs,
    )
    db.session.add(job)
    db.session.commit()
    return job


def starts(fake):
    return [c for c in fake.calls if c[0] in ("sync", "copy", "move")]


def runner_of(app):
    return app.config["JOB_RUNNER"]


def test_empty_source_holds_sync(app, fake_rclone, events):
    fake_rclone.top_entries = []
    with app.app_context():
        job = make_job()
        run = runner_of(app).start_job(job)
        assert run.status == "held"
        assert run.guard == "source_empty"
        assert run.finished_at is not None
        assert "empty" in run.error_message
        assert starts(fake_rclone) == []
        assert run.id not in runner_of(app)._active_runs
    held = [d for n, d in events if n == "run_held"]
    assert len(held) == 1
    assert held[0]["guard"] == "source_empty"
    assert held[0]["job_name"] == "Photos"


def test_unreachable_source_holds_sync(app, fake_rclone, events):
    fake_rclone.top_error = RuntimeError("directory not found")
    with app.app_context():
        run = runner_of(app).start_job(make_job())
        assert run.status == "held"
        assert run.guard == "source_unreachable"
        assert "directory not found" in run.error_message
        assert starts(fake_rclone) == []


def test_copy_skips_preflight(app, fake_rclone):
    fake_rclone.top_entries = []
    with app.app_context():
        run = runner_of(app).start_job(make_job("copy"))
        assert run.status == "running"
        assert len(starts(fake_rclone)) == 1
        assert fake_rclone.listed == []


def test_max_delete_in_config(app):
    with app.app_context():
        assert make_job().to_rclone_config()["MaxDelete"] == 1000
        assert "MaxDelete" not in make_job("copy").to_rclone_config()
        assert "MaxDelete" not in make_job(max_delete=-1).to_rclone_config()
        assert make_job(max_delete=5).to_rclone_config()["MaxDelete"] == 5
        job = make_job(extra_flags=json.dumps({"MaxDelete": 7}))
        assert job.to_rclone_config()["MaxDelete"] == 7


def test_override_route_skips_checks_and_limit(app, client, fake_rclone):
    fake_rclone.top_entries = []
    with app.app_context():
        job_id = make_job().id
    resp = client.post(f"/ops/start/{job_id}", data={"override": "1"})
    assert resp.status_code == 302
    (kind, kwargs), = starts(fake_rclone)
    assert "MaxDelete" not in kwargs["_config"]
    with app.app_context():
        run = RunHistory.query.one()
        assert run.status == "running"
        assert run.triggered_by == "manual_override"
        assert "MaxDelete" not in run.get_config_snapshot()["config"]


def test_plain_route_flashes_when_held(app, client, fake_rclone):
    fake_rclone.top_entries = []
    with app.app_context():
        job_id = make_job().id
    resp = client.post(f"/ops/start/{job_id}", follow_redirects=True)
    assert b"was not started" in resp.data
    assert starts(fake_rclone) == []


def finish_with_error(app, fake_rclone, error, **job_kwargs):
    with app.app_context():
        job = make_job(**job_kwargs)
        runner = runner_of(app)
        run = runner.start_job(job)
        fake_rclone.status = {"finished": True, "success": False, "error": error}
        runner.check_and_update_runs()
        return run.id


def test_delete_limit_detected_and_not_retried(app, fake_rclone, events, monkeypatch):
    retries = []
    monkeypatch.setattr(
        runner_of(app), "_schedule_retry", lambda *a: retries.append(a)
    )
    run_id = finish_with_error(
        app,
        fake_rclone,
        "sync failed: --max-delete threshold reached",
        auto_resume=True,
    )
    with app.app_context():
        run = db.session.get(RunHistory, run_id)
        assert run.status == "failed"
        assert run.guard == "delete_limit"
        assert run.error_message.startswith(
            "Stopped after reaching the delete limit of 1000 files."
        )
    assert retries == []
    finished = [d for n, d in events if n == "run_finished"]
    assert finished[0]["guard"] == "delete_limit"
    assert finished[0]["will_retry"] is False


def test_run_finished_will_retry(app, fake_rclone, events, monkeypatch):
    retries = []
    monkeypatch.setattr(
        runner_of(app), "_schedule_retry", lambda *a: retries.append(a)
    )
    run_id = finish_with_error(app, fake_rclone, "boom", auto_resume=True)
    assert len(retries) == 1
    finished = [d for n, d in events if n == "run_finished"]
    assert finished[0]["run_id"] == run_id
    assert finished[0]["status"] == "failed"
    assert finished[0]["will_retry"] is True
    assert finished[0]["guard"] is None


def test_run_finished_without_retry_and_completed(app, fake_rclone, events):
    with app.app_context():
        runner = runner_of(app)
        runner.start_job(make_job())
        fake_rclone.status = {"finished": True, "success": True}
        runner.check_and_update_runs()
    finished = [d for n, d in events if n == "run_finished"]
    assert finished[0]["status"] == "completed"
    assert finished[0]["will_retry"] is False


def test_stop_job_emits_finished(app, fake_rclone, events):
    with app.app_context():
        runner = runner_of(app)
        run = runner.start_job(make_job())
        runner.stop_job(run)
    finished = [d for n, d in events if n == "run_finished"]
    assert finished[0]["status"] == "stopped"
    assert finished[0]["will_retry"] is False


def test_stats_and_snapshot_persisted(app, fake_rclone):
    fake_rclone.stats = {"bytes": 10, "transfers": 2, "deletes": 3, "checks": 9}
    with app.app_context():
        runner = runner_of(app)
        run = runner.start_job(make_job())
        snapshot = run.get_config_snapshot()
        assert snapshot["source"] == "/mnt/photos"
        assert snapshot["destination"] == "b2:bucket"
        assert snapshot["sync_type"] == "sync"
        assert snapshot["config"]["MaxDelete"] == 1000
        assert snapshot["filter"] is None
        fake_rclone.status = {"finished": True, "success": True}
        runner.check_and_update_runs()
        run = db.session.get(RunHistory, run.id)
        assert (run.deletes, run.checks) == (3, 9)


def test_scheduled_run_skipped_while_running(app, fake_rclone, events):
    with app.app_context():
        job = make_job()
        first = runner_of(app).start_job(job)
        first_id = first.id
        job_id = job.id
        app.config["SCHEDULER_SERVICE"]._run_scheduled_job(job_id)
        skipped = RunHistory.query.filter_by(status="skipped").one()
        assert skipped.triggered_by == "schedule"
        assert skipped.error_message == f"Skipped: Run #{first_id} was still running."
        assert skipped.started_at == skipped.finished_at
    assert [d["run_id"] for n, d in events if n == "run_skipped"] == [skipped.id]


def test_already_running_carries_run_id(app, fake_rclone):
    with app.app_context():
        job = make_job()
        first = runner_of(app).start_job(job)
        with pytest.raises(JobAlreadyRunning) as exc:
            runner_of(app).start_job(job)
        assert exc.value.existing_run_id == first.id


def test_history_pages_render_held_and_skipped(app, client, fake_rclone):
    with app.app_context():
        job = make_job()
        now = datetime.utcnow()
        held = RunHistory(
            job_id=job.id, status="held", guard="source_empty",
            started_at=now, finished_at=now, error_message="empty",
        )
        skipped = RunHistory(
            job_id=job.id, status="skipped", triggered_by="schedule",
            started_at=now, finished_at=now, error_message="Skipped",
        )
        db.session.add_all([held, skipped])
        db.session.commit()
        held_id, skipped_id = held.id, skipped.id
    page = client.get("/history/")
    assert page.status_code == 200
    assert b"Held" in page.data and b"Skipped" in page.data
    assert b"Run anyway" in page.data
    assert b"/ops/resume/" not in page.data
    for run_id in (held_id, skipped_id):
        assert client.get(f"/history/{run_id}").status_code == 200
    assert client.get("/").status_code == 200
    assert b"Run anyway" in client.get(f"/history/{held_id}").data


def test_job_form_parses_max_delete(app, client, fake_rclone):
    base = {"name": "J", "source": "/a", "destination": "/b", "sync_type": "sync"}
    client.post("/jobs/create", data={**base, "max_delete": "-1"})
    client.post("/jobs/create", data={**base, "name": "K", "max_delete": "junk"})
    client.post("/jobs/create", data={**base, "name": "L", "max_delete": "-5"})
    with app.app_context():
        values = {j.name: j.max_delete for j in Job.query}
    assert values == {"J": -1, "K": 1000, "L": 1000}
