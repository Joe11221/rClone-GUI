import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import inspect, text

from app.extensions import db
from app.models import Job, RunHistory
from app.services import health
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
        destination="b2:photos",
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


def finish_preview(fake, deletes, success=True, error=None, names=()):
    fake.status = {"finished": True, "success": success, "error": error}
    fake.stats = {"deletes": deletes}
    fake.get_transferred = lambda group=None: {
        "transferred": [{"name": n, "what": "deleting"} for n in names]
        + [{"name": "kept.txt", "what": "checking"}]
    }


def test_preview_under_limit_then_real_sync_starts(app, fake_rclone, events):
    with app.app_context():
        runner = runner_of(app)
        run = runner.start_job(make_job(max_delete=100))
        run_id = run.id
        # Only the dry run is started, and it has no delete limit.
        (kind, kwargs), = starts(fake_rclone)
        assert kwargs["_config"]["DryRun"] is True
        assert "MaxDelete" not in kwargs["_config"]
        assert kwargs["group"] == f"job_{run_id}_preview"
        assert run.phase == "preview" and run.status == "running"
        assert runner.is_previewing(run_id)

        finish_preview(fake_rclone, deletes=3)
        runner.check_and_update_runs()
        run = db.session.get(RunHistory, run_id)
        assert run.phase is None and run.status == "running"
        assert run.would_delete == 3
        assert run.stats_group == f"job_{run_id}"
        assert not runner.is_previewing(run_id)
        assert run_id in runner._active_runs
        real = starts(fake_rclone)[1][1]
        assert "DryRun" not in real["_config"]
        assert real["_config"]["MaxDelete"] == 100  # the backstop stays
        assert real["group"] == f"job_{run_id}"
        assert run.get_config_snapshot()["config"]["MaxDelete"] == 100


def test_preview_over_limit_holds_and_never_starts_sync(app, fake_rclone, events):
    with app.app_context():
        runner = runner_of(app)
        run = runner.start_job(make_job(max_delete=1000))
        run_id = run.id
        started = run.started_at
        finish_preview(fake_rclone, deletes=4210, names=["a.txt", "b/c.txt"])
        runner.check_and_update_runs()
        run = db.session.get(RunHistory, run_id)
        assert run.status == "held" and run.guard == "delete_preview"
        assert run.phase is None
        assert run.would_delete == 4210
        assert run.get_would_delete_paths() == ["a.txt", "b/c.txt"]
        assert run.started_at == started
        assert run.error_message == (
            "This sync would delete 4,210 files from b2:photos, more than the "
            "limit of 1,000. It was not started."
        )
        assert run_id not in runner._active_runs
        assert len(starts(fake_rclone)) == 1  # the dry run only
    held = [d for n, d in events if n == "run_held"]
    assert [d["guard"] for d in held] == ["delete_preview"]


def test_preview_at_the_limit_passes(app, fake_rclone):
    with app.app_context():
        runner = runner_of(app)
        run = runner.start_job(make_job(max_delete=5))
        finish_preview(fake_rclone, deletes=5)
        runner.check_and_update_runs()
        assert db.session.get(RunHistory, run.id).status == "running"
        assert len(starts(fake_rclone)) == 2


def test_preview_paths_are_capped_at_fifty(app, fake_rclone):
    with app.app_context():
        runner = runner_of(app)
        run = runner.start_job(make_job(max_delete=1))
        finish_preview(fake_rclone, deletes=90, names=[f"f{i}" for i in range(90)])
        runner.check_and_update_runs()
        assert len(db.session.get(RunHistory, run.id).get_would_delete_paths()) == 50


def test_preview_failure_holds(app, fake_rclone, events):
    with app.app_context():
        runner = runner_of(app)
        run = runner.start_job(make_job())
        finish_preview(fake_rclone, deletes=0, success=False, error="boom")
        runner.check_and_update_runs()
        run = db.session.get(RunHistory, run.id)
        assert run.status == "held" and run.guard == "preview_failed"
        assert run.phase is None
        assert "boom" in run.error_message
        assert len(starts(fake_rclone)) == 1
    assert [d["guard"] for n, d in events if n == "run_held"] == ["preview_failed"]


def test_stop_during_preview_stops_the_dry_run(app, fake_rclone, events):
    fake_rclone.stats = {"deletes": 999}
    with app.app_context():
        runner = runner_of(app)
        run = runner.start_job(make_job())
        runner.stop_job(run)
        run = db.session.get(RunHistory, run.id)
        assert ("job_stop", 42) in fake_rclone.calls
        assert run.status == "stopped" and run.phase is None
        assert run.deletes == 0  # the dry run's counters aren't this run's
        assert run.id not in runner._active_runs
        assert not runner.is_previewing(run.id)
        runner.check_and_update_runs()
        assert len(starts(fake_rclone)) == 1


def test_preview_phase_blocks_a_second_start(app, fake_rclone):
    with app.app_context():
        job = make_job()
        runner_of(app).start_job(job)
        with pytest.raises(JobAlreadyRunning):
            runner_of(app).start_job(job)


def test_preview_off_starts_directly(app, fake_rclone):
    with app.app_context():
        run = runner_of(app).start_job(make_job(preview_deletes=False))
        (kind, kwargs), = starts(fake_rclone)
        assert "DryRun" not in kwargs["_config"]
        assert kwargs["_config"]["MaxDelete"] == 1000
        assert run.phase is None


def test_no_limit_or_copy_skips_the_preview(app, fake_rclone):
    with app.app_context():
        runner_of(app).start_job(make_job(max_delete=-1))
        assert "DryRun" not in starts(fake_rclone)[0][1]["_config"]
        fake_rclone.calls.clear()
        runner_of(app).start_job(make_job("copy"))
        assert "DryRun" not in starts(fake_rclone)[0][1]["_config"]


def test_override_skips_the_preview(app, fake_rclone):
    with app.app_context():
        runner_of(app).start_job(make_job(), allow_deletes=True)
        (kind, kwargs), = starts(fake_rclone)
        assert "DryRun" not in kwargs["_config"]


def test_restart_during_preview_retracks_or_fails(app, fake_rclone):
    with app.app_context():
        runner = runner_of(app)
        run = runner.start_job(make_job())
        run_id = run.id
        runner._active_runs.clear()
        runner._previewing.clear()
        runner.restore_active_runs()  # rclone still knows the job
        assert runner.is_previewing(run_id) and run_id in runner._active_runs

        runner._active_runs.clear()
        runner._previewing.clear()

        def gone(jobid):
            raise RuntimeError("job not found")

        fake_rclone.job_status = gone
        runner.restore_active_runs()
        run = db.session.get(RunHistory, run_id)
        assert run.status == "failed" and run.phase is None


# --- dry-run button ---


@pytest.mark.parametrize("sync_type", ["copy", "sync", "move"])
def test_dry_run_route(app, client, fake_rclone, events, sync_type):
    fake_rclone.top_entries = []  # a sync would be held by the preflight
    with app.app_context():
        job_id = make_job(sync_type).id
    resp = client.post(f"/jobs/{job_id}/dry-run")
    assert resp.status_code == 302
    (kind, kwargs), = starts(fake_rclone)
    assert kwargs["_config"]["DryRun"] is True
    assert "MaxDelete" not in kwargs["_config"]
    assert fake_rclone.listed == []
    with app.app_context():
        run = RunHistory.query.one()
        assert run.triggered_by == "dry_run" and run.status == "running"
        assert run.phase is None
        assert run.get_config_snapshot()["config"]["DryRun"] is True


def test_dry_run_never_retries_or_notifies(app, fake_rclone, events, monkeypatch):
    retries = []
    monkeypatch.setattr(runner_of(app), "_schedule_retry", lambda *a: retries.append(a))
    with app.app_context():
        runner = runner_of(app)
        run = runner.start_job(make_job(auto_resume=True), dry_run=True)
        fake_rclone.status = {"finished": True, "success": False, "error": "boom"}
        runner.check_and_update_runs()
        assert db.session.get(RunHistory, run.id).status == "failed"
    assert retries == []
    assert [e for e in events if e[0] in ("run_finished", "run_held")] == []


def test_dry_run_route_when_already_running(app, client, fake_rclone):
    with app.app_context():
        job = make_job("copy")
        runner_of(app).start_job(job)
        job_id = job.id
    resp = client.post(f"/jobs/{job_id}/dry-run", follow_redirects=True)
    assert b"already running" in resp.data
    assert len(starts(fake_rclone)) == 1


def test_dry_run_excluded_from_health(app, fake_rclone):
    now = datetime(2026, 1, 10, 12, 0)
    with app.app_context():
        job = make_job("copy")

        def add(status, minutes, trigger):
            started = now - timedelta(minutes=minutes)
            db.session.add(RunHistory(
                job_id=job.id, status=status, triggered_by=trigger,
                started_at=started, finished_at=started,
            ))

        add("failed", 90, "manual")
        add("completed", 30, "dry_run")  # must not count as a success
        add("failed", 10, "dry_run")  # nor as a failure streak
        db.session.commit()
        runs = health.load_runs([job], now)[job.id]
        assert {r.triggered_by for r in runs} == {"manual"}
        entry = health.evaluate_job(job, runs, now, None, timedelta(0))
        assert entry.last_success is None
        assert entry.consecutive_failures == 1
        assert entry.success_rate_7d == 0.0

        # The pure functions skip them even when handed the rows.
        everything = RunHistory.query.all()
        entry = health.evaluate_job(job, everything, now, None, timedelta(0))
        assert entry.last_success is None and entry.consecutive_failures == 1
        report = health.build_report(
            [job], {job.id: everything}, now, None, timedelta(0)
        )
        assert report["recent_runs"] == 1 and report["recent_ok"] == 0


def test_history_and_detail_show_dry_runs(app, client, fake_rclone):
    with app.app_context():
        sync = make_job("sync")
        copy = make_job("copy")
        now = datetime.utcnow()
        a = RunHistory(
            job_id=sync.id, status="completed", triggered_by="dry_run",
            started_at=now, finished_at=now, files_transferred=12,
            bytes_transferred=3 * 1024**3, deletes=0,
        )
        b = RunHistory(
            job_id=copy.id, status="completed", triggered_by="dry_run",
            started_at=now, finished_at=now, files_transferred=1,
            bytes_transferred=1024, deletes=0,
        )
        held = RunHistory(
            job_id=sync.id, status="held", guard="delete_preview",
            started_at=now, finished_at=now, would_delete=200,
            would_delete_paths=json.dumps(["<b>x</b>.txt"]),
            error_message="would delete",
        )
        db.session.add_all([a, b, held])
        db.session.commit()
        ids = (a.id, b.id, held.id)
    assert b"Dry run" in client.get("/history/").data
    page = client.get(f"/history/{ids[0]}").data.decode()
    assert "Dry run. Nothing was changed." in page
    assert "It would copy 12 files (3.0 GB) and delete 0 files." in page
    page = client.get(f"/history/{ids[1]}").data.decode()
    assert "It would copy 1 file (1.0 KB)." in page
    page = client.get(f"/history/{ids[2]}").data.decode()
    assert "Preview this sync" in page and "Run anyway" in page
    assert "&lt;b&gt;x&lt;/b&gt;.txt" in page and "<b>x</b>.txt" not in page
    assert b"/ops/resume/" not in client.get(f"/history/{ids[0]}").data


def test_running_page_shows_counting_deletions(app, client, fake_rclone):
    with app.app_context():
        runner_of(app).start_job(make_job())
    page = client.get("/ops/").data.decode()
    assert "Counting deletions" in page
    assert 'data-phase="preview"' in page
    assert 'class="progress' not in page


def test_job_form_saves_preview_switch(app, client, fake_rclone):
    base = {"name": "J", "source": "/a", "destination": "/b", "sync_type": "sync"}
    client.post("/jobs/create", data={**base, "preview_deletes": "on"})
    client.post("/jobs/create", data={**base, "name": "K"})
    with app.app_context():
        values = {j.name: j.preview_deletes for j in Job.query}
        job_id = Job.query.filter_by(name="J").one().id
    assert values == {"J": True, "K": False}
    page = client.get(f"/jobs/{job_id}/edit").data.decode()
    assert "Count deletions before each run" in page
    assert "Dry run" in page


def test_migration_adds_columns_to_an_old_database(app):
    from app import _ensure_columns

    with app.app_context():
        db.session.execute(text("DROP TABLE run_history"))
        db.session.execute(text(
            "CREATE TABLE run_history (id INTEGER PRIMARY KEY, job_id INTEGER)"
        ))
        db.session.commit()
        _ensure_columns()
        cols = {c["name"] for c in inspect(db.engine).get_columns("run_history")}
        assert {"phase", "would_delete", "would_delete_paths"} <= cols
        job_cols = {c["name"] for c in inspect(db.engine).get_columns("job")}
        assert "preview_deletes" in job_cols
