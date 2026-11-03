import re

from app.services.app_settings import set_setting


def test_settings_page_has_one_title_and_no_skipped_headings(client):
    page = client.get("/settings/").get_data(as_text=True)
    assert len(re.findall(r"<h1\b", page)) == 1
    assert '<h1 class="h4 mb-3">' in page
    assert "<h4" not in page
    assert "<h3" not in page  # sections are h2 directly under the page title


def test_test_notification_is_its_own_form_and_posts_only_the_urls(client):
    page = client.get("/settings/").get_data(as_text=True)
    form = re.search(r'<form id="notify-test-form"[^>]*>(.*?)</form>', page, re.S)
    assert form and 'action="/settings/notifications/test"' in form.group(0)
    # Only the URL box is copied across; the other fields never ride along.
    assert re.findall(r'<input[^>]*name="([^"]+)"', form.group(1)) == ["notify_urls"]
    assert "formaction" not in page


def test_test_notification_without_typed_urls_uses_the_saved_ones(app, client):
    calls = []
    app.config["NOTIFIER"].sender = lambda *a: calls.append(a) or True
    with app.app_context():
        set_setting("notify_urls", ["ntfy://ntfy.sh/saved"])
    client.post("/settings/notifications/test", data={"notify_urls": ""}, follow_redirects=True)
    assert calls[0][0] == ["ntfy://ntfy.sh/saved"]


def test_test_notification_saves_nothing(app, client):
    app.config["NOTIFIER"].sender = lambda *a: True
    with app.app_context():
        set_setting("public_url", "http://nas.lan:8080")
    client.post("/settings/notifications/test",
                data={"notify_urls": "ntfy://ntfy.sh/a", "public_url": "http://other.lan"})
    with app.app_context():
        from app.services import notifier
        assert notifier.get_public_url(app) == "http://nas.lan:8080"


def test_navbar_marks_the_current_page_and_the_live_status(client):
    page = client.get("/settings/").get_data(as_text=True)
    assert re.search(r'<a class="nav-link active"[^>]*aria-current="page"[^>]*href="/settings/"', page)
    assert 'id="connection-status" role="status"' in page
    assert 'aria-controls="navbarNav" aria-expanded="false" aria-label="Toggle navigation"' in page
    assert 'id="running-count"' in page
    assert 'id="confirm-title"' in page


def test_desktop_mode_marks_the_html_element(app, client):
    assert "data-desktop" not in client.get("/").get_data(as_text=True).split("<head>")[0]
    app.config["DESKTOP_MODE"] = True
    head = client.get("/").get_data(as_text=True).split("<head>")[0]
    assert 'data-bs-theme="dark" data-desktop="1"' in head
