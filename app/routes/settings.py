from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, url_for

from app.extensions import db
from app.models import BandwidthRule
from app.services import bandwidth
from app.services import remotes as remote_setup

settings_bp = Blueprint("settings", __name__)

_sign_ins = remote_setup.SignIns()


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
    global_rules = (
        BandwidthRule.query.filter_by(job_id=None).order_by(BandwidthRule.id).all()
    )
    limit = current_app.config["BANDWIDTH"].current()
    return render_template(
        "settings.html",
        remotes=remotes,
        rclone_connected=rclone_connected,
        bandwidth_rules=[bandwidth.rule_to_dict(r) for r in global_rules],
        bandwidth_now=bandwidth.describe_rule(limit.rule) if limit else None,
    )


@settings_bp.route("/bandwidth", methods=["POST"])
def save_bandwidth():
    try:
        rules = bandwidth.parse_rules(
            request.form.get("bandwidth_rules"), allow_remote_scope=True
        )
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("settings.index"))
    bandwidth.replace_rules(rules)
    db.session.commit()
    current_app.config["BANDWIDTH"].reconcile()
    flash(
        f"Bandwidth limits saved ({len(rules)} rule{'s' if len(rules) != 1 else ''}).",
        "success",
    )
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
    categories = [
        (category, [t for t in types if t["category"] == category])
        for category in remote_setup.CATEGORY_ORDER
    ]
    return render_template(
        "remotes/new.html",
        types=types,
        categories=categories,
        existing=existing,
        error=error,
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

    # A sign-in left waiting holds its remote's name and rclone's redirect port.
    _sign_ins.cancel_active()

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
    pasted = str(data.get("token") or "").strip()
    if pasted:
        try:
            token = remote_setup.extract_token(pasted)
        except remote_setup.RemoteSetupError as exc:
            return _error(str(exc))

    try:
        remote_setup.create_remote(rclone, name, detail["name"], parameters, token=token)
    except remote_setup.SignInNeeded as pending:
        try:
            job_id = _sign_ins.start(
                rclone,
                current_app.config["RCLONE_BINARY"],
                name,
                detail["name"],
                pending,
                open_browser=current_app.config["DESKTOP_MODE"],
            )
        except remote_setup.RemoteSetupError as exc:
            return _error(str(exc))
        return jsonify(pending=job_id), 202
    except remote_setup.RemoteSetupError as exc:
        return _error(str(exc))
    except Exception as exc:
        return _error(f"rclone reported an error: {exc}", 502)

    _flash_added(name, remote_setup.check_remote(rclone, name))
    return jsonify(name=name)


def _flash_added(name, problem):
    if problem:
        flash(f"Remote '{name}' added, but listing it failed: {problem}", "warning")
    else:
        flash(f"Remote '{name}' added and working.", "success")


@settings_bp.route("/remotes/status/<job_id>")
def remote_status(job_id):
    job = _sign_ins.status(job_id)
    if job is None:
        return _error("Unknown setup.", 404)
    if job["state"] == "done":
        _flash_added(job["name"], job["warning"])
    return jsonify(job)


@settings_bp.route("/remotes/finish/<job_id>", methods=["POST"])
def remote_finish(job_id):
    data = request.get_json(silent=True) or {}
    try:
        _sign_ins.finish(job_id, str(data.get("url") or ""))
    except remote_setup.RemoteSetupError as exc:
        return _error(str(exc))
    return jsonify(ok=True)


@settings_bp.route("/remotes/cancel/<job_id>", methods=["POST"])
def remote_cancel(job_id):
    _sign_ins.cancel(job_id)
    return jsonify(ok=True)
