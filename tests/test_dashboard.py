import re
from datetime import datetime, timedelta

import pytest

from app.extensions import db
from app.models import Job, RunHistory, Schedule


def add_job(name, cron=None, sync_type="sync"):
    job = Job(name=name, source="/src", destination="b2:dst", sync_type=sync_type)
    if cron:
        job.schedule = Schedule(cron_expression=cron, enabled=True)
    db.session.add(job)
    db.session.commit()
    return job


def add_run(job, status="completed", hours_ago=1, size=0, triggered_by="schedule", **kw):
    started = datetime.utcnow() - timedelta(hours=hours_ago)
    r = RunHistory(
        job_id=job.id,
        status=status,
        started_at=started,
        finished_at=None if status == "running" else started + timedelta(minutes=5),
        bytes_transferred=size,
        triggered_by=triggered_by,
        **kw,
    )
    db.session.add(r)
    db.session.commit()
    return r


def kpi(html, element_id):
    m = re.search(rf'id="{element_id}"[^>]*>\s*(.*?)\s*</p>', html, re.S)
    assert m, element_id
    return m.group(1)


@pytest.fixture()
def connected(app, monkeypatch):
    # The dashboard view holds the client it was created with, not the config entry.
    client = app.config["RCLONE_CLIENT"]
    monkeypatch.setattr(client, "list_remotes", lambda: [])
    monkeypatch.setattr(client, "version", lambda: {"version": "v1.68.2"})


def test_kpis_render(app, client, connected):
    with app.app_context():
        job = add_job("Photos", cron="0 6 * * *")
        add_run(job, size=2048)
        add_run(job, status="failed", hours_ago=3)
    html = client.get("/").get_data(as_text=True)
    assert 'class="row row-cols-2 row-cols-lg-5' in html
    assert "stat-card" in html
    assert "Connected" in html and "1.68.2" in html
    assert kpi(html, "active-count") == "0"
    assert kpi(html, "global-bytes") == "2.0 KB"
    assert kpi(html, "week-success") == "1 / 2"


def test_disconnected_is_stated_in_words(client):
    html = client.get("/").get_data(as_text=True)
    assert "Disconnected" in html


def test_dry_runs_do_not_count(app, client, connected):
    with app.app_context():
        job = add_job("Photos")
        add_run(job, size=1000)
        add_run(job, size=5000000, triggered_by="dry_run")
        add_run(job, status="failed", triggered_by="dry_run")
        add_run(job, size=0, triggered_by=None)
    html = client.get("/").get_data(as_text=True)
    assert kpi(html, "global-bytes") == "1000.0 B"
    assert kpi(html, "week-success") == "2 / 2"


def test_old_and_running_runs_stay_out_of_transferred(app, client, connected):
    with app.app_context():
        job = add_job("Photos")
        add_run(job, size=1000)
        add_run(job, size=900000, hours_ago=30)
        add_run(job, status="running", size=700000, hours_ago=0)
    html = client.get("/").get_data(as_text=True)
    # Running bytes arrive live over the event stream instead.
    assert 'data-base="1000"' in html


def test_empty_state_names_the_next_run(app, client, connected):
    with app.app_context():
        add_job("Server configs", cron="15 6 * * *")
    html = client.get("/").get_data(as_text=True)
    m = re.search(r'id="active-empty"[^>]*>(.*?)</p>', html, re.S)
    assert m and "d-none" not in m.group(0)
    assert re.search(r"Nothing running\.\s+Next: Server configs ", m.group(1))


def test_empty_state_hidden_while_running(app, client, connected):
    with app.app_context():
        job = add_job("Photos")
        add_run(job, status="running", hours_ago=0)
    html = client.get("/").get_data(as_text=True)
    assert re.search(r'id="active-empty" class="[^"]*d-none', html)
    assert 'class="transfer-item' in html


def test_up_next_lists_scheduled_jobs_soonest_first(app, client, connected):
    with app.app_context():
        for i in range(7):
            add_job(f"Job {i}", cron=f"{i * 5} 3 * * *")
        add_job("Manual only")
    html = client.get("/").get_data(as_text=True)
    up_next = html.split('id="upnext-heading"')[1].split('id="quick-heading"')[0]
    assert up_next.count("<li>") == 5
    assert "Manual only" not in up_next


def test_quick_start_is_manual_jobs_only(app, client, connected):
    with app.app_context():
        add_job("Nightly", cron="0 3 * * *")
        add_job("Archive", sync_type="copy")
    html = client.get("/").get_data(as_text=True)
    quick = html.split('id="quick-heading"')[1].split("</aside>")[0]
    assert 'aria-label="Run Archive"' in quick
    assert "copy" in quick
    assert "Nightly" not in quick.split("Manual-only")[0]
    assert 'aria-label="Run Nightly now"' in html  # still in the Backups list


def test_no_quick_start_without_manual_jobs(app, client, connected):
    with app.app_context():
        add_job("Nightly", cron="0 3 * * *")
    assert "quick-heading" not in client.get("/").get_data(as_text=True)


def test_counting_deletions_is_indeterminate(app, client, connected):
    with app.app_context():
        job = add_job("Photos")
        add_run(job, status="running", hours_ago=0, phase="preview")
    html = client.get("/").get_data(as_text=True)
    assert "Counting deletions" in html
    assert "is-preview" in html
    assert "aria-valuenow" not in html.split('id="active-transfers"')[1].split("</section>")[0]


def test_recent_runs_label_dry_runs(app, client, connected):
    with app.app_context():
        job = add_job("Photos")
        add_run(job, triggered_by="dry_run")
    html = client.get("/").get_data(as_text=True)
    assert ">Dry run</td>" in html
