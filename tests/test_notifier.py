import pytest

from app.extensions import db
from app.models import Job, RunError, RunHistory
from app.services import notifier as notifier_mod
from app.services.app_settings import set_setting


@pytest.fixture()
def sent(app):
    """Notifier with a recording sender and a synchronous spawner."""
    calls = []
    n = app.config["NOTIFIER"]
    n.sender = lambda urls, title, body, kind: calls.append((urls, title, body, kind)) or True
    n.spawner = lambda fn, *a: fn(*a)
    with app.app_context():
        set_setting("notify_urls", ["ntfy://ntfy.sh/x"])
    return calls


@pytest.fixture()
def job(app):
    with app.app_context():
        j = Job(name="Photos", source="/data/photos", destination="b2:photos")
        db.session.add(j)
        db.session.commit()
        return j.id


def emit(app, event, **data):
    app.config["EVENTS"].emit(event, **data)


def finished(job_id, status="failed", **kw):
    base = dict(run_id=7, job_id=job_id, job_name="Photos", status=status,
                errors=0, error_message=None, guard=None, will_retry=False)
    base.update(kw)
    return base


def test_failed(app, sent, job):
    emit(app, "run_finished", **finished(job, error_message="boom"))
    (urls, title, body, kind), = sent
    assert title == "Backup failed: Photos"
    assert kind == "failure"
    assert "/data/photos → b2:photos" in body
    assert "boom" in body


def test_interrupted_counts_as_failed(app, sent, job):
    emit(app, "run_finished", **finished(job, status="interrupted"))
    assert sent[0][1] == "Backup failed: Photos"


def test_will_retry_and_stopped_ignored(app, sent, job):
    emit(app, "run_finished", **finished(job, will_retry=True))
    emit(app, "run_finished", **finished(job, status="stopped"))
    assert sent == []


def test_delete_limit_is_held(app, sent, job):
    emit(app, "run_finished", **finished(job, guard="delete_limit"))
    assert sent[0][1] == "Backup stopped at delete limit: Photos"
    assert sent[0][3] == "failure"


def test_succeeded_off_by_default_then_on(app, sent, job):
    emit(app, "run_finished", **finished(job, status="completed"))
    assert sent == []
    with app.app_context():
        set_setting("notify_on", {"succeeded": True})
    emit(app, "run_finished", **finished(job, status="completed"))
    assert sent[0][1] == "Backup succeeded: Photos"
    assert sent[0][3] == "success"


def test_held_and_skipped(app, sent, job):
    emit(app, "run_held", run_id=1, job_id=job, job_name="Photos",
         guard="source_empty", reason="Source is empty")
    emit(app, "run_skipped", run_id=2, job_id=job, job_name="Photos", reason="Still running")
    assert sent[0][1] == "Backup held: Photos"
    assert sent[1][1] == "Scheduled run skipped: Photos"
    assert sent[1][3] == "warning"
    assert "Still running" in sent[1][2]


@pytest.mark.parametrize(
    "old,new,title,kind",
    [
        ("healthy", "overdue", "Backup overdue: Photos", "failure"),
        ("healthy", "warning", "Backup warning: Photos", "warning"),
        ("failing", "healthy", "Backup recovered: Photos", "success"),
        ("overdue", "healthy", "Backup recovered: Photos", "success"),
        ("warning", "healthy", "Backup recovered: Photos", "success"),
    ],
)
def test_health_transitions(app, sent, job, old, new, title, kind):
    emit(app, "health_changed", job_id=job, job_name="Photos",
         old_state=old, new_state=new, detail="because")
    (_, t, body, k), = sent
    assert (t, k) == (title, kind)
    assert "because" in body


@pytest.mark.parametrize(
    "old,new",
    [("healthy", "failing"), ("overdue", "failing"), ("pending", "healthy"),
     ("healthy", "pending"), ("healthy", "healthy")],
)
def test_health_ignored(app, sent, job, old, new):
    emit(app, "health_changed", job_id=job, job_name="Photos",
         old_state=old, new_state=new, detail="")
    assert sent == []


def test_disabled_category(app, sent, job):
    with app.app_context():
        set_setting("notify_on", {"failed": False})
    emit(app, "run_finished", **finished(job))
    assert sent == []


def test_no_urls(app, sent, job):
    with app.app_context():
        set_setting("notify_urls", [])
    emit(app, "run_finished", **finished(job))
    assert sent == []


def test_failure_body_causes_and_link(app, sent, job):
    with app.app_context():
        run = RunHistory(job_id=job, status="failed")
        db.session.add(run)
        db.session.commit()
        run_id = run.id
        for i in range(3):
            db.session.add(RunError(run_id=run_id, message="quota exceeded",
                                    category="quota exceeded", fingerprint=f"a{i}"))
        db.session.add(RunError(run_id=run_id, message="denied",
                                category="permission denied", fingerprint="b"))
        db.session.commit()
        set_setting("public_url", "http://nas.lan:8080/")
    emit(app, "run_finished", **finished(job, run_id=run_id, error_message="x" * 500))
    body = sent[0][2]
    assert "3 × quota exceeded" in body
    assert "1 × permission denied" in body
    assert body.index("quota") < body.index("denied")
    assert f"http://nas.lan:8080/history/{run_id}" in body
    assert "x" * 400 not in body


def test_health_link_is_dashboard(app, sent, job):
    with app.app_context():
        set_setting("public_url", "http://nas.lan:8080")
    emit(app, "health_changed", job_id=job, job_name="Photos",
         old_state="healthy", new_state="overdue", detail="late")
    assert sent[0][2].splitlines()[-1] == "http://nas.lan:8080/"


def test_public_url_falls_back_to_config(app, sent, job):
    app.config["PUBLIC_URL"] = "http://cfg:1"
    emit(app, "run_finished", **finished(job))
    assert "http://cfg:1/history/7" in sent[0][2]


def test_deleted_job(app, sent):
    emit(app, "run_finished", **finished(9999, error_message="gone"))
    assert sent[0][1] == "Backup failed: Photos"
    assert "→" not in sent[0][2]


def test_sender_exception_is_contained(app, job):
    n = app.config["NOTIFIER"]
    n.spawner = lambda fn, *a: fn(*a)

    def boom(*a):
        raise RuntimeError("down")

    n.sender = boom
    with app.app_context():
        set_setting("notify_urls", ["ntfy://ntfy.sh/x"])
    emit(app, "run_finished", **finished(job))


# --- settings routes ---


def test_save_settings(app, client):
    r = client.post("/settings/notifications", data={
        "notify_urls": "ntfy://ntfy.sh/a\n\n  discord://1/2  ",
        "notify_on": ["failed", "succeeded"],
        "public_url": "http://nas.lan:8080/",
        "heartbeat_url": "https://hc-ping.com/abc",
        "heartbeat_minutes": "10",
    })
    assert r.status_code == 302
    with app.app_context():
        assert notifier_mod.get_urls() == ["ntfy://ntfy.sh/a", "discord://1/2"]
        on = notifier_mod.get_notify_on()
        assert on["succeeded"] and on["failed"] and not on["held"]
        assert notifier_mod.get_public_url(app) == "http://nas.lan:8080"
        assert notifier_mod.get_heartbeat() == ("https://hc-ping.com/abc", 10)
    page = client.get("/settings/").get_data(as_text=True)
    assert "ntfy://ntfy.sh/a" in page


def test_bad_url_rejected_by_line_number(app, client):
    r = client.post("/settings/notifications", data={
        "notify_urls": "ntfy://ntfy.sh/a\nnot a url SECRETTOKEN",
    }, follow_redirects=True)
    text = r.get_data(as_text=True)
    assert "line 2" in text
    assert "SECRETTOKEN" not in text
    with app.app_context():
        assert notifier_mod.get_urls() == []


def test_heartbeat_must_be_http(app, client):
    r = client.post("/settings/notifications", data={
        "heartbeat_url": "ftp://x", "heartbeat_minutes": "5"}, follow_redirects=True)
    assert "must start with http" in r.get_data(as_text=True)
    with app.app_context():
        assert notifier_mod.get_heartbeat()[0] == ""


def test_test_notification(app, client):
    calls = []
    app.config["NOTIFIER"].sender = lambda *a: calls.append(a) or True
    r = client.post("/settings/notifications/test",
                    data={"notify_urls": "ntfy://ntfy.sh/a"}, follow_redirects=True)
    assert "Test notification sent" in r.get_data(as_text=True)
    assert calls[0][0] == ["ntfy://ntfy.sh/a"]

    app.config["NOTIFIER"].sender = lambda *a: False
    r = client.post("/settings/notifications/test",
                    data={"notify_urls": "ntfy://ntfy.sh/a"}, follow_redirects=True)
    assert "could not be delivered" in r.get_data(as_text=True)


def test_test_notification_without_urls(client):
    r = client.post("/settings/notifications/test", follow_redirects=True)
    assert "Add at least one" in r.get_data(as_text=True)


# --- heartbeat ---


@pytest.fixture()
def pings(app, monkeypatch, fake_rclone):
    calls = []

    class Resp:
        def raise_for_status(self):
            pass

    monkeypatch.setattr(notifier_mod.requests, "get",
                        lambda url, timeout: calls.append(url) or Resp())
    with app.app_context():
        set_setting("heartbeat_url", "https://hc-ping.com/abc")
        set_setting("heartbeat_minutes", 5)
    return calls


def test_heartbeat_due_and_interval(app, pings):
    n = app.config["NOTIFIER"]
    assert n.heartbeat_tick(now=1000)
    assert not n.heartbeat_tick(now=1000 + 299)
    assert n.heartbeat_tick(now=1000 + 300)
    assert len(pings) == 2


def test_heartbeat_not_sent_when_rclone_down(app, pings, fake_rclone):
    def down():
        raise ConnectionError("no rclone")

    fake_rclone.noop = down
    assert not app.config["NOTIFIER"].heartbeat_tick(now=1000)
    assert pings == []


def test_heartbeat_no_url(app, monkeypatch, fake_rclone):
    monkeypatch.setattr(notifier_mod.requests, "get",
                        lambda *a, **k: pytest.fail("should not ping"))
    assert not app.config["NOTIFIER"].heartbeat_tick(now=1000)


def test_heartbeat_failure_retries_next_tick(app, pings, monkeypatch):
    def fail(url, timeout):
        raise OSError("net")

    monkeypatch.setattr(notifier_mod.requests, "get", fail)
    n = app.config["NOTIFIER"]
    assert not n.heartbeat_tick(now=1000)
    assert n._last_ping is None
