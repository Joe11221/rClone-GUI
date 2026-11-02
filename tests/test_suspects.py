import json

from app.extensions import db
from app.models import Job, RunHistory
from app.services.suspects import SuspectTracker, exclude_pattern


def entry(name, size, second, **extra):
    return {
        "name": name,
        "size": size,
        "completed_at": f"2026-01-01T00:00:{second:02d}Z",
        **extra,
    }


def tiny_dir(path, count, start=0):
    return [entry(f"{path}/f{i}.js", 900, (start + i) % 60) for i in range(count)]


def test_flags_directory_full_of_tiny_files():
    tracker = SuspectTracker()
    tracker.observe(1, tiny_dir("proj/node_modules/pkg", 60))
    [suspect] = tracker.top(1)
    assert suspect["path"] == "proj"
    assert suspect["files"] == 60
    assert suspect["pattern"] == "/proj/**"


def test_reports_the_top_folder_not_every_subfolder():
    tracker = SuspectTracker()
    tracker.observe(1, tiny_dir("a/b/c", 50) + tiny_dir("a/b/d", 50, start=7))
    assert [s["path"] for s in tracker.top(1)] == ["a"]


def test_big_files_and_small_counts_are_not_flagged():
    tracker = SuspectTracker()
    tracker.observe(
        1,
        [entry(f"photos/{i}.jpg", 5_000_000, i % 60) for i in range(80)]
        + tiny_dir("few", 10),
    )
    assert tracker.top(1) == []


def test_ignores_checks_errors_and_root_files():
    tracker = SuspectTracker()
    tracker.observe(
        1,
        [entry(f"d/{i}", 10, i % 60, checked=True) for i in range(60)]
        + [entry(f"e/{i}", 10, i % 60, error="boom") for i in range(60)]
        + [entry(f"loose{i}", 10, i % 60) for i in range(60)],
    )
    assert tracker.top(1) == []


def test_overlapping_polls_do_not_double_count():
    tracker = SuspectTracker()
    batch = tiny_dir("x", 45)
    tracker.observe(1, batch)
    tracker.observe(1, batch)
    tracker.observe(1, batch + tiny_dir("x", 3, start=50))
    assert tracker.top(1)[0]["files"] == 48


def test_prune_forgets_finished_runs():
    tracker = SuspectTracker()
    tracker.observe(1, tiny_dir("x", 45))
    tracker.observe(2, tiny_dir("y", 45))
    tracker.prune([2])
    assert tracker.top(1) == []
    assert tracker.top(2)


def test_exclude_pattern_escapes_glob_characters():
    assert exclude_pattern("a/[b]*/c d") == "/a/\[b\]\*/c d/**"


def test_exclude_endpoint_appends_rule_once(app, client):
    with app.app_context():
        job = Job(name="J", source="/s", destination="r:d", exclude_patterns='["*.tmp"]')
        db.session.add(job)
        db.session.flush()
        run = RunHistory(job_id=job.id, status="running")
        db.session.add(run)
        db.session.commit()
        run_id, job_id = run.id, job.id

    first = client.post(f"/ops/exclude/{run_id}", json={"path": "proj/cache"})
    again = client.post(f"/ops/exclude/{run_id}", json={"path": "/proj/cache/"})
    assert first.get_json() == {"pattern": "/proj/cache/**", "added": True}
    assert again.get_json()["added"] is False
    with app.app_context():
        assert json.loads(db.session.get(Job, job_id).exclude_patterns) == [
            "*.tmp",
            "/proj/cache/**",
        ]


def test_exclude_endpoint_rejects_empty_path(app, client):
    with app.app_context():
        job = Job(name="J", source="/s", destination="r:d")
        db.session.add(job)
        db.session.flush()
        run = RunHistory(job_id=job.id, status="running")
        db.session.add(run)
        db.session.commit()
        run_id = run.id
    assert client.post(f"/ops/exclude/{run_id}", json={}).status_code == 400
