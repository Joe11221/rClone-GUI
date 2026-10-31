import pytest

from app.extensions import db
from app.models import Job, RunHistory
from app.routes.jobs import _split_browse_path

JOB_FORM = {
    "name": "Photos",
    "source": "/mnt/photos",
    "destination": "remote:photos",
    "sync_type": "copy",
    "excludes": "*.tmp\n\n.DS_Store",
}


def create_job(client, **overrides):
    return client.post("/jobs/create", data={**JOB_FORM, **overrides})


def test_pages_render_without_rclone(client):
    urls = ["/", "/jobs/", "/jobs/create", "/ops/", "/schedules/", "/history/", "/settings/"]
    for url in urls:
        assert client.get(url).status_code == 200, url


def test_create_job_parses_excludes(app, client):
    assert create_job(client).status_code == 302
    with app.app_context():
        job = Job.query.one()
        assert job.name == "Photos"
        assert job.get_excludes() == ["*.tmp", ".DS_Store"]


def test_unknown_compare_method_falls_back_to_default(app, client):
    create_job(client, compare_method="rm -rf")
    with app.app_context():
        assert Job.query.one().compare_method == "default"


def test_edit_and_delete_job(app, client):
    create_job(client)
    with app.app_context():
        job_id = Job.query.one().id
    client.post(
        f"/jobs/{job_id}/edit",
        data={**JOB_FORM, "name": "Renamed", "compare_method": "checksum"},
    )
    with app.app_context():
        job = db.session.get(Job, job_id)
        assert (job.name, job.compare_method) == ("Renamed", "checksum")
    client.post(f"/jobs/{job_id}/delete")
    with app.app_context():
        assert Job.query.count() == 0


def test_browse_requires_path(client, fake_rclone):
    assert client.get("/jobs/api/browse").status_code == 400


def test_browse_returns_sorted_dirs(client, fake_rclone):
    resp = client.get("/jobs/api/browse?path=gdrive:Backups")
    assert resp.get_json() == {"dirs": ["a", "b"], "path": "gdrive:Backups"}


def test_start_job_uses_per_run_stats_group(app, client, fake_rclone):
    create_job(client)
    with app.app_context():
        job_id = Job.query.one().id
    client.post(f"/ops/start/{job_id}")

    kind, kwargs = fake_rclone.calls[0]
    with app.app_context():
        run = RunHistory.query.one()
        assert kind == "copy"
        assert kwargs["group"] == f"job_{run.id}"
        assert run.rclone_jobid == 42
        assert run.status == "running"
        assert app.config["JOB_RUNNER"]._active_runs == {run.id: 42}


def test_flash_messages_work_with_secret_key(client):
    resp = create_job(client)
    page = client.get(resp.headers["Location"])
    assert b"created" in page.data


@pytest.mark.parametrize(
    "path, expected",
    [
        ("C:", ("C:/", "")),
        ("C:/", ("C:/", "")),
        ("C:\\Users\\joe", ("C:/Users/joe/", "")),
        ("d:/data/", ("d:/data/", "")),
        ("\\\\nas\\share\\x", ("//nas/share/x/", "")),
        ("gdrive:", ("gdrive:", "")),
        ("gdrive:Backups/photos", ("gdrive:", "Backups/photos")),
    ],
)
def test_browse_path_on_windows(path, expected):
    assert _split_browse_path(path, windows=True) == expected


@pytest.mark.parametrize(
    "path, expected",
    [
        ("/", ("/", "")),
        ("/mnt/storage", ("/mnt/storage/", "")),
        # Linux allows single-letter remotes.
        ("c:", ("c:", "")),
        ("c:Backups", ("c:", "Backups")),
    ],
)
def test_browse_path_elsewhere(path, expected):
    assert _split_browse_path(path, windows=False) == expected


def test_browse_passes_split_path_to_rclone(client, fake_rclone, monkeypatch):
    seen = []
    monkeypatch.setattr(
        fake_rclone, "list_dirs", lambda fs, remote: seen.append((fs, remote)) or {"list": []}
    )
    client.get("/jobs/api/browse?path=gdrive:Backups")
    assert seen == [("gdrive:", "Backups")]


def test_templates_load_nothing_from_the_internet(client):
    page = client.get("/").data.decode()
    assert "cdn." not in page
    assert client.get("/static/vendor/bootstrap/bootstrap.min.css").status_code == 200
    assert client.get("/static/vendor/bootstrap/bootstrap.bundle.min.js").status_code == 200
    css = client.get("/static/vendor/bootstrap-icons/bootstrap-icons.min.css")
    assert b"fonts/bootstrap-icons.woff2" in css.data
    assert client.get("/static/vendor/bootstrap-icons/fonts/bootstrap-icons.woff2").status_code == 200


def test_delete_confirmation_escapes_job_name(client):
    create_job(client, name="Joe's <b>photos</b>")
    page = client.get("/jobs/").data.decode()
    assert "data-confirm=\"Delete job 'Joe&#39;s &lt;b&gt;photos&lt;/b&gt;'?\"" in page
