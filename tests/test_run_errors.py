import csv
import io

import pytest

from app.extensions import db
from app.models import Job, RunError, RunHistory
from app.services.run_errors import MAX_ERRORS_PER_RUN, categorize


def failure(name, error="googleapi: Error 403: Rate Limit Exceeded", at="12:00:00"):
    return {
        "name": name,
        "size": 10,
        "error": error,
        "checked": False,
        "started_at": f"2025-01-01T{at}.000Z",
        "completed_at": f"2025-01-01T{at}.500Z",
    }


@pytest.fixture()
def run(app, fake_rclone):
    with app.app_context():
        job = Job(name="Photos", source="/src", destination="remote:dst")
        db.session.add(job)
        db.session.flush()
        run = RunHistory(job_id=job.id, stats_group="job_1", rclone_jobid=42)
        db.session.add(run)
        db.session.commit()
        fake_rclone.transferred = []
        fake_rclone.get_transferred = lambda group=None: {
            "transferred": fake_rclone.transferred
        }
        yield run


def collect(app):
    with app.app_context():
        runner = app.config["JOB_RUNNER"]
        return runner.collect_errors(db.session.get(RunHistory, 1))


def test_categorize_groups_causes_but_keeps_status_codes():
    a = categorize("open /mnt/photos/a/IMG_1.jpg: permission denied", "a/IMG_1.jpg")
    b = categorize("open /mnt/photos/b/IMG_2.jpg: permission denied", "b/IMG_2.jpg")
    assert a == b == "open <path>: permission denied"
    assert categorize("Error 403: rate limit") != categorize("Error 429: rate limit")
    assert categorize("dial tcp 10.0.0.5:443: i/o timeout") == "dial tcp <ip>: i/o timeout"
    assert categorize("failed for 'secret.txt' id 1a2b3c4d5e6f7a8b") == "failed for '…' id <id>"
    assert categorize("") == "Unknown error"


def test_only_failures_are_stored_and_repeat_polls_dont_duplicate(app, fake_rclone, run):
    fake_rclone.transferred = [
        failure("a.jpg"),
        {"name": "ok.jpg", "size": 1, "error": "", "completed_at": "2025-01-01T12:00:01.000Z"},
    ]
    assert collect(app) == 1
    assert collect(app) == 0
    fake_rclone.transferred.append(failure("b.jpg", at="12:00:02"))
    assert collect(app) == 1
    with app.app_context():
        assert sorted(e.path for e in RunError.query) == ["a.jpg", "b.jpg"]


def test_same_file_failing_again_is_a_new_entry(app, fake_rclone, run):
    fake_rclone.transferred = [failure("a.jpg", at="12:00:00"), failure("a.jpg", at="12:05:00")]
    assert collect(app) == 2


def test_failures_survive_rclone_forgetting_them(app, fake_rclone, run):
    fake_rclone.transferred = [failure("a.jpg")]
    collect(app)
    fake_rclone.transferred = []
    collect(app)
    with app.app_context():
        assert RunError.query.count() == 1


def test_collection_stops_at_the_cap(app, fake_rclone, run, monkeypatch):
    monkeypatch.setattr("app.services.run_errors.MAX_ERRORS_PER_RUN", 2)
    fake_rclone.transferred = [failure(f"{i}.jpg", at=f"12:00:0{i}") for i in range(4)]
    assert collect(app) == 2
    assert collect(app) == 0
    assert MAX_ERRORS_PER_RUN == 50_000


def test_rclone_errors_during_collection_are_swallowed(app, fake_rclone, run):
    def boom(group=None):
        raise RuntimeError("rclone is down")

    fake_rclone.get_transferred = boom
    assert collect(app) == 0


def seed(app, count, categories=("403", "denied")):
    with app.app_context():
        for i in range(count):
            kind = categories[i % len(categories)]
            db.session.add(
                RunError(
                    run_id=1,
                    path=f"dir/file{i}.bin",
                    message=f"{kind} for file{i}.bin",
                    category=kind,
                    fingerprint=f"fp{i}",
                )
            )
        db.session.commit()


def test_listing_is_newest_first_and_paged_by_cursor(app, client, run):
    seed(app, 25)
    page = client.get("/runs/1/errors?limit=10&summary=1").get_json()
    ids = [e["id"] for e in page["errors"]]
    assert ids == sorted(ids, reverse=True) and len(ids) == 10
    assert page["total"] == 25 and page["has_more"] and page["last_id"] == ids[0]
    assert {c["category"]: c["count"] for c in page["categories"]} == {"403": 13, "denied": 12}

    older = client.get(f"/runs/1/errors?limit=100&before={ids[-1]}").get_json()
    assert len(older["errors"]) == 15 and not older["has_more"]

    newer = client.get(f"/runs/1/errors?after={ids[2]}").get_json()
    assert [e["id"] for e in newer["errors"]] == ids[:2]


def test_listing_filters_by_category_and_text(app, client, run):
    seed(app, 20)
    by_category = client.get("/runs/1/errors?category=denied&count=1&limit=500").get_json()
    assert by_category["matched"] == 10
    assert all(e["category"] == "denied" for e in by_category["errors"])

    by_text = client.get("/runs/1/errors?q=file7.bin").get_json()
    assert [e["path"] for e in by_text["errors"]] == ["dir/file7.bin"]


def test_text_filter_treats_wildcards_literally(app, client, run):
    seed(app, 5)
    assert client.get("/runs/1/errors?q=%25").get_json()["errors"] == []
    assert client.get("/runs/1/errors?q=_").get_json()["errors"] == []


def test_limit_is_clamped_and_bad_numbers_ignored(app, client, run):
    seed(app, 3)
    assert len(client.get("/runs/1/errors?limit=999999").get_json()["errors"]) == 3
    assert len(client.get("/runs/1/errors?limit=abc&after=x").get_json()["errors"]) == 3
    assert client.get("/runs/99/errors").status_code == 404


def test_csv_export_neutralises_formulas(app, client, run):
    with app.app_context():
        db.session.add(
            RunError(
                run_id=1,
                path='=HYPERLINK("http://x")',
                message='boom, "quoted"',
                category="boom",
                fingerprint="x",
            )
        )
        db.session.commit()
    resp = client.get("/runs/1/errors.csv")
    assert resp.mimetype == "text/csv"
    rows = list(csv.reader(io.StringIO(resp.data.decode())))
    assert rows[0] == ["time_utc", "kind", "path", "size", "message"]
    assert rows[1][2].startswith("'=")
    assert rows[1][4] == 'boom, "quoted"'


def test_finishing_a_run_captures_late_failures_and_last_error(app, fake_rclone, run):
    fake_rclone.transferred = [failure("late.jpg")]
    fake_rclone.job_status = lambda jobid: {"finished": True, "success": False}
    fake_rclone.get_stats = lambda group=None: {
        "errors": 1,
        "bytes": 5,
        "transfers": 0,
        "lastError": "couldn't list directory",
    }
    with app.app_context():
        runner = app.config["JOB_RUNNER"]
        runner._active_runs[1] = 42
        runner.check_and_update_runs()
        finished = db.session.get(RunHistory, 1)
        assert finished.status == "failed"
        assert finished.error_message == "couldn't list directory"
        assert RunError.query.one().path == "late.jpg"


def test_deleting_a_job_removes_its_failures(app, client, run):
    seed(app, 3)
    client.post("/jobs/1/delete")
    with app.app_context():
        assert RunError.query.count() == 0


def test_run_page_shows_failure_browser_only_when_there_are_failures(app, client, run):
    assert b"run-errors" not in client.get("/history/1").data
    seed(app, 1)
    assert b"run-errors" in client.get("/history/1").data
