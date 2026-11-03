import json
import os
import re
from datetime import datetime

from flask import (
    Blueprint,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

from app.extensions import db
from app.models import Job, Schedule
from app.services import bandwidth, extra_flags
from app.services.cron_trigger import cron_trigger
from app.services.job_runner import JobAlreadyRunning

jobs_bp = Blueprint("jobs", __name__)

_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:(/|$)")


def _split_browse_path(path, windows=os.name == "nt"):
    """Split a browser path into the fs and remote rclone's list call expects.

    On Windows rclone reads "C:" as a drive, never as a remote called C, and
    "//server/share" as a network path.
    """
    if windows:
        normalized = path.replace("\\", "/")
        if _WINDOWS_DRIVE.match(normalized) or normalized.startswith("//"):
            return normalized.rstrip("/") + "/", ""

    colon_idx = path.find(":")
    if colon_idx != -1:
        # Remote path: "gdrive:" or "gdrive:Backups/photos"
        fs = path[: colon_idx + 1]  # "gdrive:"
        remote = path[colon_idx + 1 :].lstrip("/")  # "Backups/photos" or ""
        return fs, remote
    # Local path: "/mnt/storage" or "/"
    return path.rstrip("/") + "/", ""


def _browser_context():
    """Template values for the directory browser on the job form."""
    windows = os.name == "nt"
    if windows:
        local_roots = [drive.replace("\\", "/") for drive in os.listdrives()]
    else:
        local_roots = ["/"]
    return {"is_windows": windows, "local_roots": local_roots}


def _parse_order_by(form):
    field = form.get("order_by_field", "size").strip().lower()
    if field not in {"size", "name", "modtime"}:
        field = "size"

    direction = form.get("order_by_direction", "mixed").strip().lower()
    if direction not in {"asc", "desc", "mixed"}:
        direction = "mixed"

    try:
        mixed_window = int(form.get("order_by_mixed_window", 50))
    except (TypeError, ValueError):
        mixed_window = 50
    mixed_window = max(0, min(100, mixed_window))

    return field, direction, mixed_window


def _parse_compare_method(form):
    method = form.get("compare_method", "default").strip().lower()
    if method not in {"default", "size_only", "checksum", "ignore_existing"}:
        method = "default"
    return method


def _parse_max_delete(form):
    """Delete limit: an int >= -1 (-1 = no limit); 1000 if missing or invalid."""
    try:
        value = int(form.get("max_delete", 1000))
    except (TypeError, ValueError):
        return 1000
    return value if value >= -1 else 1000


def _parse_max_age_hours(form):
    """Hours a job may go without a success; None if blank, invalid or <= 0."""
    try:
        value = int(form.get("max_age_hours", "").strip())
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _parse_bandwidth_rules(form):
    """The job form's bandwidth rules: a list of rule values, None if the form
    didn't include any (leave them alone), or ValueError."""
    raw = form.get("bandwidth_rules")
    if raw is None:
        return None
    return bandwidth.parse_rules(raw, allow_remote_scope=False)


# Form fields that live inside the collapsed "Advanced rclone options" section,
# so a validation error on one of them opens it.
ADVANCED_FIELDS = {"extra_flags"}

_TEXT_FIELDS = (
    "name", "source", "destination", "sync_type", "transfers", "checkers",
    "retries", "order_by_field", "order_by_direction", "order_by_mixed_window",
    "compare_method", "max_delete", "max_age_hours", "max_retries",
    "retry_delay_seconds", "excludes", "extra_flags", "schedule_cron",
)


def _form_values(job=None, form=None):
    """What each field on the job form shows: the submitted text after a
    validation error, else the saved job, else the defaults for a new job."""
    if form is not None:
        return {
            **{k: form.get(k, "") for k in _TEXT_FIELDS},
            "fast_list": "fast_list" in form,
            "preview_deletes": "preview_deletes" in form,
            "auto_resume": "auto_resume" in form,
            "schedule_enabled": "schedule_enabled" in form,
        }
    schedule = job.schedule if job else None
    return {
        "name": job.name if job else "",
        "source": job.source if job else "",
        "destination": job.destination if job else "",
        # Copy never deletes anything, so it is the safe thing to start from.
        "sync_type": job.sync_type if job else "copy",
        "transfers": job.transfers if job else 4,
        "checkers": job.checkers if job else 8,
        "retries": job.retries if job else 3,
        "fast_list": bool(job.fast_list) if job else True,
        "order_by_field": (job.order_by_field if job else None) or "size",
        "order_by_direction": (job.order_by_direction if job else None) or "mixed",
        "order_by_mixed_window": (
            job.order_by_mixed_window
            if job and job.order_by_mixed_window is not None else 50
        ),
        "compare_method": (job.compare_method if job else None) or "default",
        "max_delete": job.max_delete if job and job.max_delete is not None else 1000,
        "max_age_hours": job.max_age_hours if job and job.max_age_hours else "",
        "preview_deletes": job.preview_deletes is not False if job else True,
        "auto_resume": bool(job.auto_resume) if job else False,
        "max_retries": job.max_retries if job else 3,
        "retry_delay_seconds": job.retry_delay_seconds if job else 60,
        "excludes": "\n".join(job.get_excludes()) if job else "",
        "extra_flags": _saved_flags_text(job),
        "schedule_cron": schedule.cron_expression if schedule else "",
        "schedule_enabled": schedule.enabled if schedule else True,
    }


def _saved_flags_text(job):
    """The job's saved extra options as flag lines."""
    saved = job.get_extra_flags() if job else {}
    if not saved:
        return ""
    try:
        options = extra_flags.load_options(current_app.config["RCLONE_CLIENT"])
    except extra_flags.OptionsUnavailable:
        options = None
    return extra_flags.format_flags(saved, options)


def _available_remotes():
    try:
        rclone = current_app.config["RCLONE_CLIENT"]
        return rclone.list_remotes().get("remotes", [])
    except Exception:
        return []


def _render_form(job, values, errors=None, rules=None, status=200):
    if rules is None:
        rules = (
            [bandwidth.rule_to_dict(r) for r in job.bandwidth_rules] if job else []
        )
    errors = errors or {}
    timezone = current_app.config["SCHEDULER_SERVICE"].timezone
    html = render_template(
        "jobs/form.html",
        job=job,
        f=values,
        errors=errors,
        advanced_open=bool(ADVANCED_FIELDS & set(errors)),
        remotes=_available_remotes(),
        bandwidth_rules=rules,
        schedule_tz=str(timezone) if timezone else "the server's local time",
        **_browser_context(),
    )
    return html, status


def _parse_extra_flags(form):
    """(config, errors); config is None to leave the saved flags alone. Fails
    closed: flags are only stored once rclone has confirmed it knows them."""
    if "extra_flags" not in form:
        return None, []
    text = form["extra_flags"]
    if not text.strip():
        return {}, []
    try:
        options = extra_flags.load_options(current_app.config["RCLONE_CLIENT"])
    except extra_flags.OptionsUnavailable:
        return None, [
            "rclone isn't reachable, so these options can't be checked and "
            "weren't saved. Try again once rclone is running, or clear the field."
        ]
    return extra_flags.parse(text, options)


def _parse_schedule(form):
    """(cron text, error); cron is None to leave the schedule alone and ""
    to remove it."""
    if "schedule_cron" not in form:
        return None, None
    cron = " ".join(form["schedule_cron"].split())
    if not cron:
        return "", None
    try:
        cron_trigger(
            cron, timezone=current_app.config["SCHEDULER_SERVICE"].timezone
        )
    except ValueError:
        return cron, (
            "Not a valid cron expression. Use five fields: minute hour "
            "day-of-month month day-of-week, for example 0 2 * * *."
        )
    return cron, None


def _apply_schedule(job, cron, enabled):
    """Create, update or remove the job's schedule the way the Schedules page
    does, so the APScheduler entry follows. The job is already committed."""
    if cron is None:
        return
    svc = current_app.config["SCHEDULER_SERVICE"]
    sched = job.schedule
    if not cron:
        if sched:
            svc.remove_schedule(sched.id)
            db.session.delete(sched)
            db.session.commit()
        return
    if sched is None:
        sched = Schedule(job_id=job.id, cron_expression=cron, enabled=enabled)
        db.session.add(sched)
    elif sched.cron_expression != cron or sched.enabled != enabled:
        sched.cron_expression = cron
        sched.enabled = enabled
        # Changing a schedule shouldn't replay what came due under the old one.
        sched.catch_up_after = datetime.utcnow()
    db.session.commit()
    if sched.enabled:
        svc.add_schedule(sched)
    else:
        svc.remove_schedule(sched.id)


def _save_job(job):
    """Validate the posted form and write it to ``job`` (a new, unsaved Job on
    create). Returns a response when the form needs fixing, else None."""
    form = request.form
    excludes = [p.strip() for p in form.get("excludes", "").split("\n") if p.strip()]
    order_by_field, order_by_direction, order_by_mixed_window = _parse_order_by(form)
    try:
        rules = _parse_bandwidth_rules(form)
    except ValueError as exc:
        flash(str(exc), "danger")
        if job.id is None:
            return redirect(url_for("jobs.create_job"))
        return redirect(url_for("jobs.edit_job", job_id=job.id))

    flags, flag_errors = _parse_extra_flags(form)
    cron, cron_error = _parse_schedule(form)
    errors = {}
    if flag_errors:
        errors["extra_flags"] = flag_errors
    if cron_error:
        errors["schedule_cron"] = cron_error
    if errors:
        try:
            editor_rules = json.loads(form.get("bandwidth_rules") or "")
        except ValueError:
            editor_rules = None
        return _render_form(
            job if job.id is not None else None,
            _form_values(form=form),
            errors,
            rules=editor_rules,
            status=400,
        )

    is_new = job.id is None
    job.name = form["name"]
    job.source = form["source"]
    job.destination = form["destination"]
    job.sync_type = form["sync_type"]
    job.transfers = int(form.get("transfers", 4))
    job.checkers = int(form.get("checkers", 8))
    job.fast_list = "fast_list" in form
    job.exclude_patterns = json.dumps(excludes)
    job.retries = int(form.get("retries", 3))
    job.order_by_field = order_by_field
    job.order_by_direction = order_by_direction
    job.order_by_mixed_window = order_by_mixed_window
    job.compare_method = _parse_compare_method(form)
    job.max_delete = _parse_max_delete(form)
    job.preview_deletes = "preview_deletes" in form
    job.max_age_hours = _parse_max_age_hours(form)
    job.auto_resume = "auto_resume" in form
    job.max_retries = int(form.get("max_retries", 3))
    job.retry_delay_seconds = int(form.get("retry_delay_seconds", 60))
    if flags is not None:
        job.extra_flags = json.dumps(flags)
    if rules is not None:
        bandwidth.replace_rules(rules, job=job)
    if is_new:
        db.session.add(job)
    db.session.commit()
    _apply_schedule(job, cron, "schedule_enabled" in form)
    if not is_new:
        current_app.config["BANDWIDTH"].reconcile()
    return None


@jobs_bp.route("/")
def list_jobs():
    jobs = Job.query.order_by(Job.created_at.desc()).all()
    return render_template("jobs/list.html", jobs=jobs)


@jobs_bp.route("/create", methods=["GET", "POST"])
def create_job():
    if request.method == "POST":
        job = Job()
        response = _save_job(job)
        if response is not None:
            return response
        flash(f"Job '{job.name}' created.", "success")
        return redirect(url_for("jobs.list_jobs"))
    return _render_form(None, _form_values())


@jobs_bp.route("/<int:job_id>/edit", methods=["GET", "POST"])
def edit_job(job_id):
    job = Job.query.get_or_404(job_id)
    if request.method == "POST":
        response = _save_job(job)
        if response is not None:
            return response
        flash(f"Job '{job.name}' updated.", "success")
        return redirect(url_for("jobs.list_jobs"))
    return _render_form(job, _form_values(job))


@jobs_bp.route("/<int:job_id>/dry-run", methods=["POST"])
def dry_run_job(job_id):
    """Run the job with rclone's --dry-run: shows what would change, changes nothing."""
    job = Job.query.get_or_404(job_id)
    runner = current_app.config["JOB_RUNNER"]
    try:
        runner.start_job(job, dry_run=True)
        flash(
            f"Dry run of '{job.name}' started. Nothing will be changed.", "info"
        )
    except JobAlreadyRunning as e:
        flash(str(e), "warning")
    except Exception as e:
        flash(f"Failed to start the dry run: {e}", "danger")
    return redirect(url_for("ops.running"))


@jobs_bp.route("/api/browse")
def browse_dirs():
    """Return subdirectories of a local or remote path via rclone."""
    path = request.args.get("path", "").strip()
    if not path:
        return jsonify({"error": "path is required"}), 400

    rclone = current_app.config.get("RCLONE_CLIENT")
    if not rclone:
        return jsonify({"error": "rclone not available"}), 503

    try:
        fs, remote = _split_browse_path(path)
        result = rclone.list_dirs(fs, remote)
        # dirsOnly=True already filters to directories, so just extract names
        dirs = sorted(
            item.get("Name") or item.get("Path", "")
            for item in result.get("list", [])
        )
        return jsonify({"dirs": dirs, "path": path})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@jobs_bp.route("/<int:job_id>/delete", methods=["POST"])
def delete_job(job_id):
    job = Job.query.get_or_404(job_id)
    name = job.name
    # Deleting the row doesn't touch the in-memory scheduler; without this the
    # cron entry would keep firing for a job that no longer exists.
    if job.schedule:
        current_app.config["SCHEDULER_SERVICE"].remove_schedule(job.schedule.id)
    db.session.delete(job)
    db.session.commit()
    flash(f"Job '{name}' deleted.", "success")
    return redirect(url_for("jobs.list_jobs"))
