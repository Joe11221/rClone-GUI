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
            return {"State": "local", "Option": {"Name": "config_is_local", "DefaultStr": "true"}}
        return {}

    def update_remote(self, name, state, result):
        self.answers.append((state, result))
        team = {"State": "team", "Option": {"Name": "config_change_team_drive", "Type": "bool", "DefaultStr": "false"}}
        if state == "local":
            # Declining the browser means asking for a token; accepting it, rclone signs in itself.
            return {"State": "token", "Option": {"Name": "config_token"}} if result == "false" else team
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
    assert [s["name"] for s in summaries] == ["drive", "sftp"]
    assert [s["oauth"] for s in summaries] == [True, False]


def test_provider_detail_drops_hidden_and_oauth_plumbing_options():
    detail = remote_setup.provider_detail(PROVIDERS[1])
    assert [o["name"] for o in detail["options"]] == ["client_id", "scope"]
    assert detail["oauth"] is True
    assert remote_setup.provider_detail(PROVIDERS[0])["options"][0]["required"] is True


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
    assert client.get("/settings/remotes/new").status_code == 200
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


def test_oauth_remote_needs_a_token_when_not_on_desktop(client, rclone):
    response = create(client, name="gd", type="drive", browser=True)
    assert response.status_code == 400
    assert "rclone authorize" in response.get_json()["error"]
    assert "gd" not in rclone.params


def test_oauth_remote_with_pasted_token_answers_rclones_questions(client, rclone):
    response = create(client, name="gd", type="drive", token=f"junk {TOKEN} junk")
    assert response.status_code == 200
    # Headless: decline the local browser, supply the token, keep the default for the rest.
    assert rclone.answers == [("local", "false"), ("token", TOKEN), ("team", "false")]
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


def test_desktop_browser_sign_in_runs_in_background(app, client, rclone):
    app.config["DESKTOP_MODE"] = True
    response = create(client, name="gd", type="drive", browser=True)
    assert response.status_code == 202
    job_id = response.get_json()["pending"]
    for _ in range(50):
        status = client.get(f"/settings/remotes/status/{job_id}").get_json()
        if status["state"] != "running":
            break
        time.sleep(0.05)
    assert status["state"] == "done"
    assert rclone.answers[0] == ("local", "true")  # let rclone open the browser
    assert client.get("/settings/remotes/status/unknown").status_code == 404


def test_favicon_is_served_and_linked(client):
    assert client.get("/favicon.ico").status_code == 200
    page = client.get("/").data
    assert b"favicon.svg" in page and b"favicon.ico" in page
