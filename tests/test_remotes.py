import queue
import time

import pytest

from app.services import remotes as remote_setup
from tests.conftest import FakeRclone

TOKEN = '{"access_token":"ya29.x","token_type":"Bearer","refresh_token":"r"}'


def option(name, **extra):
    return {"Name": name, "Help": f"{name} help", "DefaultStr": "", **extra}


PROVIDERS = [
    {
        "Name": "sftp",
        "Description": "SSH/SFTP",
        "Options": [
            option("host", Required=True),
            option("pass", IsPassword=True),
            option("hidden_thing", Hide=1),
        ],
    },
    {
        "Name": "drive",
        "Description": "Google Drive",
        "Options": [
            option("client_id"),
            option("token", Advanced=True),
            option("scope", Exclusive=True, Examples=[{"Value": "drive", "Help": "Full"}]),
        ],
    },
    {
        "Name": "linkbox",
        "Description": "Linkbox",
        "Options": [option("token", Required=True)],
    },
    {"Name": "secret", "Description": "Internal", "Hide": True, "Options": []},
]


class RemoteFake(FakeRclone):
    """Plays out rclone's question sequence for OAuth remotes."""

    def __init__(self):
        super().__init__()
        self.remotes = ["existing"]
        self.answers = []
        self.params = {}
        self.fail_with = None

    def list_providers(self):
        return {"providers": PROVIDERS}

    def list_remotes(self):
        return {"remotes": list(self.remotes)}

    def create_remote(self, name, remote_type, parameters):
        self.params[name] = (remote_type, parameters)
        self.remotes.append(name)
        if self.fail_with:
            return {"Error": self.fail_with}
        if remote_type == "drive":
            return {
                "State": "shared",
                "Option": {"Name": "config_shared_client_id", "Help": "Shared id is retiring.\n\nGo on?", "Type": "bool"},
            }
        return {}

    def update_remote(self, name, state, result):
        self.answers.append((state, result))
        team = {"State": "team", "Option": {"Name": "config_change_team_drive", "Type": "bool", "DefaultStr": "false"}}
        if state == "shared":
            return {"State": "local", "Option": {"Name": "config_is_local", "DefaultStr": "true"}}
        if state == "local":
            help_text = 'Execute this:\n\n\trclone authorize "drive" "eyJibG9iIjoxfQ"\n\nThen paste.'
            return {"State": "token", "Option": {"Name": "config_token", "Help": help_text, "Required": True}}
        if state == "token":
            return team
        return {}

    def delete_remote(self, name):
        self.remotes.remove(name)


@pytest.fixture()
def rclone(app):
    fake = RemoteFake()
    app.config["RCLONE_CLIENT"] = fake
    return fake


def create(client, **body):
    return client.post("/settings/remotes/create", json=body)


def test_provider_summaries_hide_internal_types_and_flag_oauth():
    summaries = remote_setup.provider_summaries(PROVIDERS)
    assert [s["name"] for s in summaries] == ["drive", "linkbox", "sftp"]
    assert [s["oauth"] for s in summaries] == [True, False, False]
    assert [s["category"] for s in summaries] == ["Cloud drives", "Cloud drives", "Servers & protocols"]


def test_provider_detail_drops_hidden_and_oauth_plumbing_options():
    detail = remote_setup.provider_detail(PROVIDERS[1])
    assert [o["name"] for o in detail["options"]] == ["client_id", "scope"]
    assert detail["oauth"] is True
    assert detail["options"][0]["advanced"] is True  # your own client id is optional
    assert remote_setup.provider_detail(PROVIDERS[0])["options"][0]["required"] is True


def test_token_option_of_non_oauth_type_is_kept():
    detail = remote_setup.provider_detail(PROVIDERS[2])
    assert detail["oauth"] is False
    assert [o["name"] for o in detail["options"]] == ["token"]


def test_authorize_arguments_come_from_rclones_help():
    text = 'Run:\n\n\trclone authorize "drive" "eyJz"\n\nThen paste the result.'
    assert remote_setup.authorize_arguments(text, "drive") == ["drive", "eyJz"]
    assert remote_setup.authorize_arguments("no command", "box") == ["box"]


def test_extract_token_accepts_rclone_authorize_output():
    pasted = f"Paste the following into your remote machine --->\n{TOKEN}\n<---End paste"
    assert remote_setup.extract_token(pasted) == TOKEN


@pytest.mark.parametrize("text", ["", "no braces", "{not json}", '{"a": 1}'])
def test_extract_token_rejects_bad_input(text):
    with pytest.raises(remote_setup.RemoteSetupError):
        remote_setup.extract_token(text)


@pytest.mark.parametrize("name", ["", "-x", " x", "x ", "a/b", "a:b"])
def test_invalid_remote_names(name):
    assert not remote_setup.valid_name(name)


def test_valid_remote_names():
    assert all(remote_setup.valid_name(n) for n in ["gdrive", "my nas-2", "a.b_c+d@e"])


def test_pages_render(client, rclone):
    page = client.get("/settings/remotes/new")
    assert page.status_code == 200
    # Every type is on the page, not just suggested on demand.
    for name in (b'data-type="sftp"', b'data-type="drive"', b'data-type="linkbox"'):
        assert name in page.data
    assert b"Add remote" in client.get("/settings/").data


def test_new_remote_page_survives_unreachable_rclone(client, rclone):
    rclone.list_providers = lambda: (_ for _ in ()).throw(RuntimeError("down"))
    page = client.get("/settings/remotes/new")
    assert page.status_code == 200
    assert b"Could not reach rclone" in page.data


def test_type_detail_endpoint(client, rclone):
    assert client.get("/settings/remotes/types/sftp").get_json()["description"] == "SFTP"
    assert client.get("/settings/remotes/types/nope").status_code == 404


def test_creates_simple_remote_and_ignores_unknown_options(client, rclone):
    response = create(
        client,
        name="nas",
        type="sftp",
        options={"host": "h", "pass": "pw", "hidden_thing": "x", "bogus": "y", "empty": ""},
    )
    assert response.status_code == 200
    assert rclone.params["nas"] == ("sftp", {"host": "h", "pass": "pw"})


@pytest.mark.parametrize(
    "body, message",
    [
        ({"name": "bad/name", "type": "sftp"}, "Remote names"),
        ({"name": "existing", "type": "sftp", "options": {"host": "h"}}, "already exists"),
        ({"name": "x", "type": "nope"}, "Choose a storage type"),
        ({"name": "x", "type": "sftp", "options": {}}, "host"),
    ],
)
def test_create_validation(client, rclone, body, message):
    response = create(client, **body)
    assert response.status_code == 400
    assert message in response.get_json()["error"]
    assert "x" not in rclone.params


def test_oauth_remote_with_pasted_token_answers_rclones_questions(client, rclone):
    response = create(client, name="gd", type="drive", token=f"junk {TOKEN} junk")
    assert response.status_code == 200
    # Decline the local browser, supply the token, keep the default for the rest.
    assert rclone.answers == [("shared", "true"), ("local", "false"), ("token", TOKEN), ("team", "false")]
    assert "gd" in rclone.remotes


def test_bad_token_is_rejected_before_touching_rclone(client, rclone):
    assert create(client, name="gd", type="drive", token="nonsense").status_code == 400
    assert "gd" not in rclone.params


def test_failed_creation_is_rolled_back(client, rclone):
    rclone.fail_with = "boom"
    response = create(client, name="nas", type="sftp", options={"host": "h"})
    assert response.status_code == 400
    assert "boom" in response.get_json()["error"]
    assert "nas" not in rclone.remotes


def test_listing_failure_is_reported_when_the_remote_is_added(client, rclone):
    def fail(fs, remote="", timeout=None):
        raise RuntimeError("wrong password")

    rclone.list_dirs = fail
    assert create(client, name="nas", type="sftp", options={"host": "h"}).status_code == 200
    with client.session_transaction() as session:
        category, message = session["_flashes"][-1]
    assert category == "warning" and "wrong password" in message


AUTH_LINK = "http://127.0.0.1:53682/auth?state=abc"


class FakeAuthorize:
    """Stands in for the ``rclone authorize`` process."""

    def __init__(self, command, **kwargs):
        self.command = command
        self.returncode = None
        self._lines = queue.Queue()
        self._lines.put(f"NOTICE: Please go to the following link: {AUTH_LINK}\n")
        self.stdout = self

    def __iter__(self):
        return iter(self._lines.get, None)

    def close(self):
        pass

    def wait(self):
        return self.returncode

    def kill(self):
        self.end(1)

    def end(self, code, *lines):
        self.returncode = code
        for line in lines:
            self._lines.put(line)
        self._lines.put(None)


class FakeResponse:
    headers = {"Location": "https://accounts.example.com/o/oauth2?x=1"}


@pytest.fixture()
def sign_in(monkeypatch):
    """Patches the process and HTTP calls; yields what they received."""
    seen = {"process": None, "gets": []}

    def popen(command, **kwargs):
        seen["process"] = FakeAuthorize(command, **kwargs)
        return seen["process"]

    def get(url, **kwargs):
        seen["gets"].append(url)
        if "code=" in url:
            seen["process"].end(0, f"Paste the following --->\n{TOKEN}\n<---End paste\n")
        elif "error=" in url:
            seen["process"].end(1, "Description: access_denied\n")
        return FakeResponse()

    monkeypatch.setattr(remote_setup.subprocess, "Popen", popen)
    monkeypatch.setattr(remote_setup.requests, "get", get)
    return seen


def wait_for(client, job_id, *states):
    for _ in range(100):
        status = client.get(f"/settings/remotes/status/{job_id}").get_json()
        if status["state"] in states:
            return status
        time.sleep(0.02)
    raise AssertionError(f"stuck in {status}")


def start_sign_in(client):
    response = create(client, name="gd", type="drive")
    assert response.status_code == 202
    return response.get_json()["pending"]


def test_sign_in_hands_browser_the_provider_page_and_finishes_with_pasted_address(client, rclone, sign_in):
    job_id = start_sign_in(client)
    status = wait_for(client, job_id, "signin")
    assert status["auth_url"] == FakeResponse.headers["Location"]
    assert status["notices"] == ["Shared id is retiring."]
    assert "token" not in status and "process" not in status
    # rclone's own command, with its settings blob; no browser to open on a server.
    assert sign_in["process"].command == ["rclone", "authorize", "drive", "eyJibG9iIjoxfQ", "--auth-no-open-browser"]

    pasted = "http://localhost:53682/?state=abc&code=xyz&scope=drive"
    assert client.post(f"/settings/remotes/finish/{job_id}", json={"url": pasted}).status_code == 200
    assert wait_for(client, job_id, "done")
    assert sign_in["gets"][-1] == "http://127.0.0.1:53682/?state=abc&code=xyz&scope=drive"
    assert ("token", TOKEN) in rclone.answers
    assert "gd" in rclone.remotes


def test_desktop_sign_in_lets_rclone_open_the_browser(app, client, rclone, sign_in):
    app.config["DESKTOP_MODE"] = True
    job_id = start_sign_in(client)
    wait_for(client, job_id, "signin")
    assert "--auth-no-open-browser" not in sign_in["process"].command
    client.post(f"/settings/remotes/cancel/{job_id}")


@pytest.mark.parametrize(
    "pasted, message",
    [
        ("nonsense", "right address"),
        ("http://localhost:53682/?code=xyz", "right address"),
        ("http://localhost:53682/?state=other&code=xyz", "different sign-in"),
    ],
)
def test_bad_pasted_address_is_rejected_and_sign_in_keeps_waiting(client, rclone, sign_in, pasted, message):
    job_id = start_sign_in(client)
    wait_for(client, job_id, "signin")
    response = client.post(f"/settings/remotes/finish/{job_id}", json={"url": pasted})
    assert response.status_code == 400 and message in response.get_json()["error"]
    assert not any("code=" in url for url in sign_in["gets"])
    assert client.get(f"/settings/remotes/status/{job_id}").get_json()["state"] == "signin"
    client.post(f"/settings/remotes/cancel/{job_id}")


def test_pasted_address_cannot_redirect_the_request_elsewhere(client, rclone, sign_in):
    job_id = start_sign_in(client)
    wait_for(client, job_id, "signin")
    pasted = "http://evil.example/steal?state=abc&code=xyz"
    client.post(f"/settings/remotes/finish/{job_id}", json={"url": pasted})
    assert sign_in["gets"][-1] == "http://127.0.0.1:53682/?state=abc&code=xyz"


def test_declined_sign_in_reports_why_and_removes_the_remote(client, rclone, sign_in):
    job_id = start_sign_in(client)
    wait_for(client, job_id, "signin")
    client.post(f"/settings/remotes/finish/{job_id}", json={"url": "http://localhost:53682/?state=abc&error=access_denied"})
    status = wait_for(client, job_id, "error")
    assert "access_denied" in status["error"]
    assert "gd" not in rclone.remotes


def test_cancel_stops_rclone_and_frees_the_name(client, rclone, sign_in):
    job_id = start_sign_in(client)
    wait_for(client, job_id, "signin")
    client.post(f"/settings/remotes/cancel/{job_id}")
    assert sign_in["process"].returncode == 1
    assert "gd" not in rclone.remotes
    assert wait_for(client, job_id, "error")


def test_retrying_replaces_a_sign_in_left_waiting(client, rclone, sign_in):
    first = start_sign_in(client)
    wait_for(client, first, "signin")
    second = start_sign_in(client)
    assert second != first
    assert wait_for(client, second, "signin")
    assert client.get(f"/settings/remotes/status/{first}").get_json()["state"] == "error"
    client.post(f"/settings/remotes/cancel/{second}")


def test_missing_rclone_binary_points_to_the_token_route(client, rclone, monkeypatch):
    def popen(*args, **kwargs):
        raise FileNotFoundError("rclone")

    monkeypatch.setattr(remote_setup.subprocess, "Popen", popen)
    response = create(client, name="gd", type="drive")
    assert response.status_code == 400
    assert "rclone authorize" in response.get_json()["error"]
    assert "gd" not in rclone.remotes


def test_favicon_is_served_and_linked(client):
    assert client.get("/favicon.ico").status_code == 200
    page = client.get("/").data
    assert b"favicon.svg" in page and b"favicon.ico" in page


# --- deleting remotes ---


def test_settings_lists_a_delete_button_per_remote(client, rclone):
    page = client.get("/settings/").get_data(as_text=True)
    assert 'aria-label="Delete remote existing"' in page
    assert 'data-confirm-label="Delete remote"' in page
    assert 'action="/settings/remotes/delete"' in page


def test_delete_removes_the_remote_and_says_so(client, rclone):
    response = client.post("/settings/remotes/delete", data={"name": "existing"})
    assert response.status_code == 302
    assert "existing" not in rclone.remotes
    with client.session_transaction() as session:
        category, message = session["_flashes"][-1]
    assert category == "success" and "deleted" in message


def test_delete_refuses_a_name_rclone_does_not_have(client, rclone):
    calls = []
    rclone.delete_remote = lambda name: calls.append(name)
    client.post("/settings/remotes/delete", data={"name": "nope"})
    assert calls == []
    with client.session_transaction() as session:
        category, message = session["_flashes"][-1]
    assert category == "warning" and "nope" in message


def test_delete_reports_an_rclone_failure_and_keeps_the_remote(client, rclone):
    def fail(name):
        raise RuntimeError("config is locked")

    rclone.delete_remote = fail
    client.post("/settings/remotes/delete", data={"name": "existing"})
    assert "existing" in rclone.remotes
    with client.session_transaction() as session:
        category, message = session["_flashes"][-1]
    assert category == "danger" and "config is locked" in message


def test_remote_names_are_monospace(client, rclone):
    page = client.get("/settings/").get_data(as_text=True)
    assert '<span class="font-monospace">existing</span>' in page
