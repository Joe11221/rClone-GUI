"""Creating rclone remotes from the GUI.

rclone describes every storage type and its options through ``config/providers``
and creates remotes through ``config/create``. Some types (Google Drive,
Dropbox, ...) sign in with OAuth, which rclone runs as a series of questions.
They are answered here: either with a token the user pasted (made by
``rclone authorize`` on a machine with a browser), or, in the Windows app where
rclone runs on the user's own PC, by letting rclone open the browser itself.
"""

import json
import logging
import re
import threading
import uuid

logger = logging.getLogger(__name__)

# rclone's rule: letters, digits and _ . + @ - space; no leading '-' or space,
# no trailing space.
REMOTE_NAME = re.compile(r"^[A-Za-z0-9_.+@][A-Za-z0-9_.+@ -]*$")
MAX_STEPS = 12
# OAuth plumbing rclone manages itself; the user only ever supplies a token.
HIDDEN_OPTIONS = {"token", "auth_url", "token_url", "client_credentials"}
# Types offered as one-click buttons, with names short enough for a button.
POPULAR = {
    "drive": "Google Drive",
    "dropbox": "Dropbox",
    "onedrive": "OneDrive",
    "pcloud": "pCloud",
    "s3": "Amazon S3 & compatible",
    "b2": "Backblaze B2",
    "sftp": "SFTP",
    "webdav": "WebDAV",
    "mega": "Mega",
    "crypt": "Encrypted (crypt)",
}
MAX_LABEL = 60


class RemoteSetupError(Exception):
    """A problem the user can fix, with a message fit to show them."""


def valid_name(name):
    return bool(REMOTE_NAME.match(name)) and not name.endswith(" ")


def is_oauth(provider):
    return any(opt.get("Name") == "token" for opt in provider.get("Options") or [])


def _label(provider):
    """A short display name; some rclone descriptions list dozens of services."""
    if provider["Name"] in POPULAR:
        return POPULAR[provider["Name"]]
    text = provider.get("Description") or provider["Name"]
    return text if len(text) <= MAX_LABEL else text[: MAX_LABEL - 3].rstrip() + "..."


def provider_summaries(providers):
    """Short entries for the type picker."""
    result = [
        {
            "name": p["Name"],
            "label": _label(p),
            "oauth": is_oauth(p),
            "popular": p["Name"] in POPULAR,
        }
        for p in providers
        if not p.get("Hide")
    ]
    result.sort(key=lambda item: item["label"].lower())
    return result


def provider_detail(provider):
    """One type's options, trimmed to what the form needs."""
    options = []
    for opt in provider.get("Options") or []:
        if opt.get("Hide") or opt.get("Name") in HIDDEN_OPTIONS:
            continue
        options.append(
            {
                "name": opt["Name"],
                "help": opt.get("Help") or "",
                "default": opt.get("DefaultStr") or "",
                "required": bool(opt.get("Required")),
                "password": bool(opt.get("IsPassword")),
                "advanced": bool(opt.get("Advanced")),
                "type": opt.get("Type") or "string",
                "exclusive": bool(opt.get("Exclusive")),
                "examples": [
                    {"value": ex.get("Value", ""), "help": ex.get("Help", "")}
                    for ex in opt.get("Examples") or []
                ],
                # Some options only apply to certain values of the "provider" option.
                "provider": opt.get("Provider") or "",
            }
        )
    return {
        "name": provider["Name"],
        "description": _label(provider),
        "oauth": is_oauth(provider),
        "options": options,
    }


def extract_token(text):
    """The JSON token from what ``rclone authorize`` prints (or the bare JSON)."""
    text = (text or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise RemoteSetupError(
            "That doesn't look like a token. Paste the JSON that `rclone authorize` prints."
        )
    candidate = text[start : end + 1]
    try:
        parsed = json.loads(candidate)
    except ValueError:
        raise RemoteSetupError("The token isn't valid JSON. Paste it exactly as printed.")
    if not isinstance(parsed, dict) or not parsed.get("access_token"):
        raise RemoteSetupError("The token is missing its access_token. Paste the full output.")
    return candidate


def _answer(option, token, browser):
    name = option.get("Name")
    if name == "config_is_local":
        # "true" makes rclone open a browser on the machine it runs on.
        return "true" if browser else "false"
    if name == "config_token":
        if not token:
            raise RemoteSetupError("This remote needs a sign-in token.")
        return token
    if name == "config_shared_client_id":
        # Continue with rclone's shared client id; users can enter their own in the form.
        return "true"
    default = option.get("DefaultStr")
    if default not in (None, ""):
        return default
    if option.get("Type") == "bool":
        return "false"
    if option.get("Required"):
        help_line = (option.get("Help") or name or "").strip().splitlines()[0]
        raise RemoteSetupError(f"rclone needs more information: {help_line}")
    return ""


def create_remote(rclone, name, remote_type, parameters, token=None, browser=False):
    """Create a remote, answering rclone's questions. Removes it again on failure."""
    try:
        out = rclone.create_remote(name, remote_type, parameters)
        for _ in range(MAX_STEPS):
            if out.get("Error"):
                raise RemoteSetupError(out["Error"])
            state = out.get("State")
            if not state:
                return
            out = rclone.update_remote(
                name, state, _answer(out.get("Option") or {}, token, browser)
            )
        raise RemoteSetupError("rclone kept asking questions; giving up.")
    except Exception:
        try:
            rclone.delete_remote(name)
        except Exception:
            logger.debug("Could not remove half-created remote %r", name, exc_info=True)
        raise


class SetupJobs:
    """Background remote setups, for sign-ins that wait on a browser."""

    MAX_KEPT = 20

    def __init__(self):
        self._jobs = {}
        self._lock = threading.Lock()

    def start(self, rclone, name, remote_type, parameters):
        job_id = uuid.uuid4().hex
        with self._lock:
            while len(self._jobs) >= self.MAX_KEPT:
                self._jobs.pop(next(iter(self._jobs)))
            self._jobs[job_id] = {"state": "running", "name": name, "error": ""}

        def run():
            try:
                create_remote(rclone, name, remote_type, parameters, browser=True)
                result = {"state": "done"}
            except RemoteSetupError as exc:
                result = {"state": "error", "error": str(exc)}
            except Exception as exc:
                logger.warning("Remote setup for %r failed: %s", name, exc)
                result = {"state": "error", "error": f"rclone reported an error: {exc}"}
            with self._lock:
                self._jobs[job_id].update(result)

        threading.Thread(target=run, daemon=True).start()
        return job_id

    def status(self, job_id):
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None
