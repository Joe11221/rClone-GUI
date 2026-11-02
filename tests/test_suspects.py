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


def big(path, second=0):
    return entry(path, 20_000_000, second)


def paths(tracker):
    return [s["path"] for s in tracker.top(1)]


def test_flags_known_folder_at_its_own_level():
    tracker = SuspectTracker()
    tracker.observe(1, tiny_dir("proj/node_modules/pkg", 30) + tiny_dir("photos", 90))
    [suspect] = tracker.top(1)
    assert suspect["path"] == "proj/node_modules"
    assert suspect["known"] is True
    assert suspect["files"] == 30
    assert suspect["pattern"] == "/proj/node_modules/**"


def test_does_not_suggest_folders_holding_much_of_the_run():
    tracker = SuspectTracker()
    # The reported case: everything is under BigDrive and only tiny files finished.
    tracker.observe(1, tiny_dir("BigDrive/a", 300) + tiny_dir("BigDrive/b", 300, start=5))
    assert paths(tracker) == []


def test_big_file_in_flight_rules_out_its_folders_but_not_their_siblings():
    tracker = SuspectTracker()
    tracker.observe(
        1,
        tiny_dir("Drive/Videos/clips", 250)
        + tiny_dir("Drive/Other/piles", 250, start=3)
        + tiny_dir("Elsewhere", 1500, start=9),
    )
    assert paths(tracker) == ["Drive"]
    tracker.observe_active(1, [{"name": "Drive/Videos/clips/film.mkv", "size": 9_000_000_000}])
    assert paths(tracker) == ["Drive/Other/piles"]


def test_unchanged_files_count_as_evidence():
    tracker = SuspectTracker()
    tracker.observe(
        1,
        [entry(f"d/.git/o{i}", 10, i % 60, checked=True) for i in range(30)]
        + [big(f"Movies/{i}.mkv", i) | {"checked": True} for i in range(40)],
    )
    assert paths(tracker) == ["d/.git"]
    assert tracker._runs[1].dirs["Movies"].large == 40


def test_narrows_to_the_folder_that_actually_holds_the_files():
    tracker = SuspectTracker()
    tracker.observe(
        1,
        tiny_dir("a/b/c", 250) + tiny_dir("a/b/d", 2, start=4) + tiny_dir("z", 1000, start=7),
    )
    assert paths(tracker) == ["a/b/c"]


def test_stops_narrowing_when_files_are_spread_out():
    tracker = SuspectTracker()
    tracker.observe(
        1, tiny_dir("a/b/c", 200) + tiny_dir("a/b/d", 200, start=7) + tiny_dir("z", 1400, start=9)
    )
    assert paths(tracker) == ["a/b"]


def test_known_folders_do_not_get_their_parent_suggested():
    tracker = SuspectTracker()
    tracker.observe(
        1,
        tiny_dir("Docs/site/node_modules/x", 500)
        + tiny_dir("Docs/notes", 20, start=3)
        + tiny_dir("Other", 900, start=5),
    )
    assert paths(tracker) == ["Docs/site/node_modules"]


def test_known_folder_is_suggested_despite_large_files_inside():
    tracker = SuspectTracker()
    tracker.observe(1, tiny_dir("app/node_modules/x", 30) + [big("app/node_modules/x/electron")])
    assert paths(tracker) == ["app/node_modules"]


def test_large_file_in_a_known_folder_does_not_rule_out_its_parent():
    tracker = SuspectTracker()
    tracker.observe(
        1,
        tiny_dir("p/src", 250)
        + tiny_dir("p/node_modules/x", 30, start=2)
        + [big("p/node_modules/x/electron")]
        + tiny_dir("z", 1000, start=4),
    )
    assert "p/src" in paths(tracker)


def test_iphone_backup_is_recognised():
    tracker = SuspectTracker()
    tracker.observe(1, tiny_dir("E/Apple Computer/MobileSync/Backup/26cb", 30))
    assert paths(tracker) == ["E/Apple Computer/MobileSync/Backup"]


def test_big_files_and_small_counts_are_not_flagged():
    tracker = SuspectTracker()
    tracker.observe(
        1,
        [entry(f"photos/{i}.jpg", 5_000_000, i % 60) for i in range(250)]
        + tiny_dir("few", 60)
        + tiny_dir("pad", 2000, start=3),
    )
    assert paths(tracker) == []


def test_ignores_errors_and_root_files():
    tracker = SuspectTracker()
    tracker.observe(
        1,
        [entry(f"e/.git/{i}", 10, i % 60, error="boom") for i in range(60)]
        + [entry(f"loose{i}", 10, i % 60) for i in range(60)],
    )
    assert tracker.top(1) == []


def test_overlapping_polls_do_not_double_count():
    tracker = SuspectTracker()
    batch = tiny_dir("x/.cache", 45)
    tracker.observe(1, batch)
    tracker.observe(1, batch)
    tracker.observe(1, batch + tiny_dir("x/.cache", 3, start=50))
    assert tracker.top(1)[0]["files"] == 48


def test_prune_forgets_finished_runs():
    tracker = SuspectTracker()
    tracker.observe(1, tiny_dir("x/.git", 45))
    tracker.observe(2, tiny_dir("y/.git", 45))
    tracker.prune([2])
    assert tracker.top(1) == []
    assert tracker.top(2)


def test_collector_feeds_in_flight_files_to_the_tracker(app):
    from app.services.job_runner import JobRunner

    class Client:
        def get_transferred(self, group=None):
            return {"transferred": []}

        def get_stats(self, group=None):
            return {"transferring": [{"name": "m/film.mkv", "size": 20_000_000}]}

    with app.app_context():
        job = Job(name="J", source="/s", destination="r:d")
        db.session.add(job)
        db.session.flush()
        run = RunHistory(job_id=job.id, status="running", stats_group="job_1")
        db.session.add(run)
        db.session.commit()
        runner = JobRunner(Client())
        runner._active_runs[run.id] = 1
        runner.collect_active_errors()
        assert runner.suspects._runs[run.id].dirs["m"].large == 1


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
