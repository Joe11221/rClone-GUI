"""Creating rclone remotes from the GUI.

rclone describes every storage type and its options through ``config/providers``
and creates remotes through ``config/create``. Some types (Google Drive,
Dropbox, ...) sign in with OAuth, which rclone runs as a series of questions, so
whether a sign-in is needed is left for rclone to say.

The sign-in itself runs the command rclone asks for, ``rclone authorize``, on
this machine, so no second copy of rclone is needed. It serves a redirect page
on loopback and waits for the provider to send the browser back there. In the
Windows app that just works. When the app runs elsewhere (Docker, a NAS) the
browser can't reach that loopback address: the user pastes the failed address
from their browser here, and it is passed on from inside.
"""

import atexit
import json
import logging
import re
import shlex
import subprocess
import threading
import uuid
from urllib.parse import parse_qs, urlsplit

import requests

logger = logging.getLogger(__name__)

# rclone's rule: letters, digits and _ . + @ - space; no leading '-' or space,
# no trailing space.
REMOTE_NAME = re.compile(r"^[A-Za-z0-9_.+@][A-Za-z0-9_.+@ -]*$")
MAX_STEPS = 12
AUTHORIZE_COMMAND = re.compile(r"rclone authorize\s+(.+)")
AUTH_LINK = re.compile(r"http://127\.0\.0\.1:\d+/auth\?state=\S+")
SIGN_IN_TIMEOUT_SECONDS = 15 * 60
CHECK_TIMEOUT_SECONDS = 20
# OAuth plumbing rclone manages itself; the user signs in instead.
OAUTH_PLUMBING = {"token", "auth_url", "token_url", "client_credentials"}
# Optional alternatives to the normal sign-in, kept out of the way.
OAUTH_ALTERNATIVES = {"client_id", "client_secret", "service_account_file", "box_config_file"}
POPULAR = ["drive", "onedrive", "dropbox", "s3", "b2", "sftp", "smb", "webdav", "crypt"]
# Names for types whose rclone description is long or unclear.
LABELS = {
    "alias": "Alias",
    "archive": "Archive reader",
    "cache": "Cache",
    "chunker": "Chunker",
    "combine": "Combine",
    "compress": "Compress",
    "crypt": "Encrypted (crypt)",
    "drive": "Google Drive",
    "google cloud storage": "Google Cloud Storage",
    "hasher": "Hasher",
    "koofr": "Koofr & Digi Storage",
    "memory": "In-memory",
    "onedrive": "OneDrive",
    "oracleobjectstorage": "Oracle Object Storage",
    "pcloud": "pCloud",
    "s3": "Amazon S3 & compatible",
    "seafile": "Seafile",
    "sftp": "SFTP",
    "swift": "OpenStack Swift",
    "union": "Union",
}
OBJECT_STORAGE = (
    "azureblob azurefiles b2 cloudinary filefabric google_cloud_storage imagekit "
    "internetarchive netstorage oracleobjectstorage qingstor s3 sia storj swift tardigrade"
)
SERVERS = "doi ftp hdfs http local seafile sftp smb webdav"
WRAPPERS = "alias archive cache chunker combine compress crypt hasher memory union"
CATEGORIES = {
    **{name.replace("_", " "): "Object storage" for name in OBJECT_STORAGE.split()},
    **{name: "Servers & protocols" for name in SERVERS.split()},
    **{name: "Built on another remote" for name in WRAPPERS.split()},
}
DEFAULT_CATEGORY = "Cloud drives"
CATEGORY_ORDER = [DEFAULT_CATEGORY, "Object storage", "Servers & protocols", "Built on another remote"]
MAX_LABEL = 60


class RemoteSetupError(Exception):
    """A problem the user can fix, with a message fit to show them."""


class SignInNeeded(Exception):
    """rclone is waiting for an OAuth token, answering ``state``."""

    def __init__(self, state, arguments, notices):
        super().__init__("Sign-in needed")
        self.state = state
        self.arguments = arguments
        self.notices = notices


def valid_name(name):
    return bool(REMOTE_NAME.match(name)) and not name.endswith(" ")


def is_oauth(provider):
    """Whether this type normally signs in with a browser.

    Some types have a ``token`` option that isn't OAuth, such as Linkbox's API
    token; those don't offer a client id.
    """
    names = {opt.get("Name") for opt in provider.get("Options") or []}
    return "token" in names and "client_id" in names


def _label(provider):
    """A short display name; some rclone descriptions list dozens of services."""
    if provider["Name"] in LABELS:
        return LABELS[provider["Name"]]
    text = provider.get("Description") or provider["Name"]
    return text if len(text) <= MAX_LABEL else text[: MAX_LABEL - 3].rstrip() + "..."


def provider_summaries(providers):
    """Short entries for the type picker, in display order."""
    result = [
        {
            "name": p["Name"],
            "label": _label(p),
            "description": p.get("Description") or "",
            "oauth": is_oauth(p),
            "popular": p["Name"] in POPULAR,
            "category": CATEGORIES.get(p["Name"], DEFAULT_CATEGORY),
        }
        for p in providers
        if not p.get("Hide")
    ]
    result.sort(key=lambda item: item["label"].lower())
    return result


def provider_detail(provider):
    """One type's options, trimmed to what the form needs."""
    oauth = is_oauth(provider)
    options = []
    for opt in provider.get("Options") or []:
        name = opt.get("Name")
        if opt.get("Hide") or (oauth and name in OAUTH_PLUMBING):
            continue
        options.append(
            {
                "name": name,
                "help": opt.get("Help") or "",
                "default": opt.get("DefaultStr") or "",
                "required": bool(opt.get("Required")),
                "password": bool(opt.get("IsPassword")),
                "advanced": bool(opt.get("Advanced")) or (oauth and name in OAUTH_ALTERNATIVES),
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
        "oauth": oauth,
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


def authorize_arguments(help_text, remote_type):
    """The arguments rclone asks to be given to ``rclone authorize``.

    They can include a blob of the remote's settings, such as a Drive scope.
    """
    match = AUTHORIZE_COMMAND.search(help_text or "")
    if not match:
        return [remote_type]
    try:
        return shlex.split(match.group(1))
    except ValueError:
        return [remote_type]


def _first_paragraph(text):
    return (text or "").strip().split("\n\n")[0].strip()


def _answer(option):
    name = option.get("Name")
    default = option.get("DefaultStr")
    if default not in (None, ""):
        return default
    if option.get("Type") == "bool":
        return "false"
    if option.get("Required"):
        help_line = (option.get("Help") or name or "").strip().splitlines()[0]
        raise RemoteSetupError(f"rclone needs more information: {help_line}")
    return ""


def _answer_questions(rclone, name, remote_type, out, token, notices):
    for _ in range(MAX_STEPS):
        if out.get("Error"):
            raise RemoteSetupError(out["Error"])
        state = out.get("State")
        if not state:
            return
        option = out.get("Option") or {}
        question = option.get("Name")
        if question == "config_is_local":
            # Decline, so that rclone asks for a token rather than opening a browser
            # wherever its daemon runs.
            answer = "false"
        elif question == "config_token":
            if not token:
                raise SignInNeeded(
                    state, authorize_arguments(option.get("Help"), remote_type), notices
                )
            answer = token
        elif question == "config_shared_client_id":
            # Continue with rclone's shared client id, passing on its warning.
            # Users can enter their own under the advanced options.
            notices.append(_first_paragraph(option.get("Help")))
            answer = "true"
        else:
            answer = _answer(option)
        out = rclone.update_remote(name, state, answer)
    raise RemoteSetupError("rclone kept asking questions; giving up.")


def _discard(rclone, name):
    try:
        rclone.delete_remote(name)
    except Exception:
        logger.debug("Could not remove half-created remote %r", name, exc_info=True)


def create_remote(rclone, name, remote_type, parameters, token=None):
    """Create a remote, answering rclone's questions. Removes it again on failure.

    Raises SignInNeeded, leaving the remote in place, if it needs a browser sign-in.
    """
    try:
        out = rclone.create_remote(name, remote_type, parameters)
        _answer_questions(rclone, name, remote_type, out, token, [])
    except SignInNeeded:
        raise
    except Exception:
        _discard(rclone, name)
        raise


def check_remote(rclone, name):
    """Try listing a new remote. Returns what went wrong, or None if it works.

    A timeout says nothing either way, so it isn't reported.
    """
    try:
        rclone.list_dirs(f"{name}:", "", timeout=CHECK_TIMEOUT_SECONDS)
    except requests.Timeout:
        return None
    except Exception as exc:
        return str(exc)
    return None


class SignIns:
    """Browser sign-ins in progress. One at a time: rclone's redirect port is fixed."""

    MAX_KEPT = 20

    def __init__(self):
        self._jobs = {}
        self._lock = threading.Lock()
        atexit.register(self.cancel_active)

    def start(self, rclone, binary, name, remote_type, pending, open_browser):
        """Run ``rclone authorize`` and carry on creating the remote after it."""
        self.cancel_active()
        command = [str(binary), "authorize", *pending.arguments]
        if not open_browser:
            command.append("--auth-no-open-browser")
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as exc:
            logger.warning("Could not run %s: %s", binary, exc)
            _discard(rclone, name)
            raise RemoteSetupError(
                "Could not start rclone here to sign in. Run rclone authorize "
                "on a computer with a browser and paste what it prints instead."
            )

        job_id = uuid.uuid4().hex
        job = {
            "state": "starting",
            "name": name,
            "error": "",
            "warning": "",
            "auth_url": "",
            "notices": pending.notices,
            # Never sent to the browser:
            "process": process,
            "link": "",
            "rclone": rclone,
            "cancelled": False,
        }
        with self._lock:
            while len(self._jobs) >= self.MAX_KEPT:
                self._jobs.pop(next(iter(self._jobs)))
            self._jobs[job_id] = job

        timer = threading.Timer(SIGN_IN_TIMEOUT_SECONDS, self.cancel, args=(job_id, "The sign-in timed out."))
        timer.daemon = True
        timer.start()
        thread = threading.Thread(
            target=self._run, args=(job, remote_type, pending.state, timer), daemon=True
        )
        thread.start()
        return job_id

    def _run(self, job, remote_type, state, timer):
        name, rclone, process = job["name"], job["rclone"], job["process"]
        lines = []
        try:
            for line in process.stdout:
                lines.append(line)
                if not job["link"]:
                    match = AUTH_LINK.search(line)
                    if match:
                        job["link"] = match.group(0)
                        job["auth_url"] = _provider_url(job["link"])
                        job["state"] = "signin"
            process.wait()
            if job["cancelled"]:
                return
            output = "".join(lines)
            if process.returncode != 0:
                raise RemoteSetupError(_failure_message(output))
            job["state"] = "finishing"
            token = extract_token(output[output.find("--->") + 4 :])
            out = rclone.update_remote(name, state, token)
            _answer_questions(rclone, name, remote_type, out, token, job["notices"])
            job["warning"] = check_remote(rclone, name) or ""
            job["state"] = "done"
        except Exception as exc:
            if not job["cancelled"]:
                _discard(rclone, name)
                logger.warning("Sign-in for remote %r failed: %s", name, exc)
                job["error"] = str(exc)
                job["state"] = "error"
        finally:
            timer.cancel()
            process.stdout.close()

    def status(self, job_id):
        job = self._jobs.get(job_id)
        if job is None:
            return None
        return {
            key: job[key] for key in ("state", "name", "error", "warning", "auth_url", "notices")
        }

    def finish(self, job_id, pasted_url):
        """Deliver the address the provider sent the browser to."""
        job = self._jobs.get(job_id)
        if job is None or not job["link"]:
            raise RemoteSetupError("This sign-in is no longer running. Start again.")
        expected = parse_qs(urlsplit(job["link"]).query).get("state", [""])[0]
        query = urlsplit((pasted_url or "").strip()).query
        received = parse_qs(query)
        if "state" not in received or ("code" not in received and "error" not in received):
            raise RemoteSetupError(
                "That isn't the right address. Copy the whole address from the "
                "browser tab that failed to load; it starts with http://localhost:"
            )
        if received["state"][0] != expected:
            raise RemoteSetupError(
                "That address is from a different sign-in. Open the sign-in link "
                "above again and copy the new address."
            )
        # Only the query is taken from the user; the address is always rclone's own.
        target = urlsplit(job["link"])
        try:
            requests.get(f"http://{target.netloc}/?{query}", timeout=30)
        except requests.RequestException as exc:
            raise RemoteSetupError(f"Could not reach rclone's sign-in page: {exc}")

    def cancel(self, job_id, reason=""):
        job = self._jobs.get(job_id)
        if job is None or job["state"] not in ("starting", "signin"):
            return
        job["cancelled"] = True
        job["error"] = reason or "Sign-in cancelled."
        job["state"] = "error"
        job["process"].kill()
        _discard(job["rclone"], job["name"])

    def cancel_active(self):
        for job_id in list(self._jobs):
            self.cancel(job_id)


def _provider_url(link):
    """Where rclone's loopback link leads, which any browser can open."""
    try:
        response = requests.get(link, allow_redirects=False, timeout=5)
    except requests.RequestException:
        return link
    return response.headers.get("Location") or link


def _failure_message(output):
    match = re.search(r"Description:\s*(.+)", output)
    if match:
        return f"Sign-in failed: {match.group(1).strip()}"
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return f"Sign-in failed: {lines[-1]}" if lines else "Sign-in failed."
