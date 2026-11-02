from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, url_for

from app.services import remotes as remote_setup

settings_bp = Blueprint("settings", __name__)

_setup_jobs = remote_setup.SetupJobs()


@settings_bp.route("/")
def index():
    rclone = current_app.config["RCLONE_CLIENT"]
    remotes = []
    rclone_connected = False
    try:
        result = rclone.list_remotes()
        remotes = result.get("remotes", [])
        rclone_connected = True
    except Exception:
        pass
    return render_template(
        "settings.html", remotes=remotes, rclone_connected=rclone_connected
    )


@settings_bp.route("/bwlimit", methods=["POST"])
def set_bwlimit():
    rclone = current_app.config["RCLONE_CLIENT"]
    rate = request.form.get("rate", "off")
    try:
        result = rclone.set_bwlimit(rate)
        flash(f"Bandwidth limit set to: {result.get('rate', rate)}", "success")
    except Exception as e:
        flash(f"Failed to set bandwidth limit: {e}", "danger")
    return redirect(url_for("settings.index"))


# --- Adding remotes ---


def _providers():
    """rclone's storage types, fetched once: they only change with rclone."""
    cached = current_app.config.get("RCLONE_PROVIDERS")
    if cached is None:
        cached = current_app.config["RCLONE_CLIENT"].list_providers().get("providers", [])
        current_app.config["RCLONE_PROVIDERS"] = cached
    return cached


def _error(message, status=400):
    return jsonify(error=message), status


@settings_bp.route("/remotes/new")
def new_remote():
    rclone = current_app.config["RCLONE_CLIENT"]
    try:
        types = remote_setup.provider_summaries(_providers())
        existing = rclone.list_remotes().get("remotes", [])
        error = None
    except Exception as exc:
        types, existing, error = [], [], f"Could not reach rclone: {exc}"
    return render_template(
        "remotes/new.html", types=types, existing=existing, error=error
    )


@settings_bp.route("/remotes/types/<remote_type>")
def remote_type_detail(remote_type):
    try:
        providers = _providers()
    except Exception as exc:
        return _error(f"Could not reach rclone: {exc}", 502)
    for provider in providers:
        if provider["Name"] == remote_type:
            return jsonify(remote_setup.provider_detail(provider))
    return _error("Unknown storage type.", 404)


@settings_bp.route("/remotes/create", methods=["POST"])
def create_remote():
    data = request.get_json(silent=True) or {}
    rclone = current_app.config["RCLONE_CLIENT"]

    name = str(data.get("name") or "").strip()
    if not remote_setup.valid_name(name):
        return _error(
            "Remote names can use letters, numbers, spaces and _ . + @ - "
            "(not starting with - or a space)."
        )

    try:
        provider = next(
            (p for p in _providers() if p["Name"] == data.get("type")), None
        )
        existing = rclone.list_remotes().get("remotes", [])
    except Exception as exc:
        return _error(f"Could not reach rclone: {exc}", 502)
    if provider is None:
        return _error("Choose a storage type.")
    if name in existing:
        return _error(f"A remote named '{name}' already exists.")

    detail = remote_setup.provider_detail(provider)
    known = {opt["name"]: opt for opt in detail["options"]}
    raw = data.get("options") if isinstance(data.get("options"), dict) else {}
    parameters = {
        key: str(value)
        for key, value in raw.items()
        if key in known and str(value).strip() != ""
    }

    missing = [
        opt["name"]
        for opt in detail["options"]
        if opt["required"]
        and not opt["default"]
        and not opt["provider"]
        and opt["name"] not in parameters
    ]
    if missing:
        return _error("Fill in: " + ", ".join(missing))

    token = None
    browser = False
    if detail["oauth"]:
        pasted = str(data.get("token") or "").strip()
        browser = bool(data.get("browser")) and current_app.config["DESKTOP_MODE"]
        if pasted:
            try:
                token = remote_setup.extract_token(pasted)
            except remote_setup.RemoteSetupError as exc:
                return _error(str(exc))
        elif not browser:
            return _error(
                f"{detail['description']} needs a sign-in. Run "
                f'rclone authorize "{detail["name"]}" on a computer with a browser '
                "and paste what it prints."
            )

    if browser and not token:
        job_id = _setup_jobs.start(rclone, name, detail["name"], parameters)
        return jsonify(pending=job_id), 202

    try:
        remote_setup.create_remote(rclone, name, detail["name"], parameters, token=token)
    except remote_setup.RemoteSetupError as exc:
        return _error(str(exc))
    except Exception as exc:
        return _error(f"rclone reported an error: {exc}", 502)

    flash(f"Remote '{name}' added.", "success")
    return jsonify(name=name)


@settings_bp.route("/remotes/status/<job_id>")
def remote_status(job_id):
    job = _setup_jobs.status(job_id)
    if job is None:
        return _error("Unknown setup.", 404)
    if job["state"] == "done":
        flash(f"Remote '{job['name']}' added.", "success")
    return jsonify(job)
