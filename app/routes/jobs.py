import json

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
from app.models import Job

jobs_bp = Blueprint("jobs", __name__)


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


@jobs_bp.route("/")
def list_jobs():
    jobs = Job.query.order_by(Job.created_at.desc()).all()
    return render_template("jobs/list.html", jobs=jobs)


@jobs_bp.route("/create", methods=["GET", "POST"])
def create_job():
    if request.method == "POST":
        excludes = [
            p.strip()
            for p in request.form.get("excludes", "").split("\n")
            if p.strip()
        ]
        order_by_field, order_by_direction, order_by_mixed_window = (
            _parse_order_by(request.form)
        )
        job = Job(
            name=request.form["name"],
            source=request.form["source"],
            destination=request.form["destination"],
            sync_type=request.form["sync_type"],
            transfers=int(request.form.get("transfers", 4)),
            checkers=int(request.form.get("checkers", 8)),
            fast_list="fast_list" in request.form,
            bwlimit=request.form.get("bwlimit", ""),
            exclude_patterns=json.dumps(excludes),
            retries=int(request.form.get("retries", 3)),
            order_by_field=order_by_field,
            order_by_direction=order_by_direction,
            order_by_mixed_window=order_by_mixed_window,
            compare_method=_parse_compare_method(request.form),
            auto_resume="auto_resume" in request.form,
            max_retries=int(request.form.get("max_retries", 3)),
            retry_delay_seconds=int(
                request.form.get("retry_delay_seconds", 60)
            ),
        )
        db.session.add(job)
        db.session.commit()
        flash(f"Job '{job.name}' created.", "success")
        return redirect(url_for("jobs.list_jobs"))

    # Try to get available remotes for the form
    remotes = []
    try:
        rclone = current_app.config["RCLONE_CLIENT"]
        result = rclone.list_remotes()
        remotes = result.get("remotes", [])
    except Exception:
        pass
    return render_template("jobs/form.html", job=None, remotes=remotes)


@jobs_bp.route("/<int:job_id>/edit", methods=["GET", "POST"])
def edit_job(job_id):
    job = Job.query.get_or_404(job_id)
    if request.method == "POST":
        excludes = [
            p.strip()
            for p in request.form.get("excludes", "").split("\n")
            if p.strip()
        ]
        order_by_field, order_by_direction, order_by_mixed_window = (
            _parse_order_by(request.form)
        )
        job.name = request.form["name"]
        job.source = request.form["source"]
        job.destination = request.form["destination"]
        job.sync_type = request.form["sync_type"]
        job.transfers = int(request.form.get("transfers", 4))
        job.checkers = int(request.form.get("checkers", 8))
        job.fast_list = "fast_list" in request.form
        job.bwlimit = request.form.get("bwlimit", "")
        job.exclude_patterns = json.dumps(excludes)
        job.retries = int(request.form.get("retries", 3))
        job.order_by_field = order_by_field
        job.order_by_direction = order_by_direction
        job.order_by_mixed_window = order_by_mixed_window
        job.compare_method = _parse_compare_method(request.form)
        job.auto_resume = "auto_resume" in request.form
        job.max_retries = int(request.form.get("max_retries", 3))
        job.retry_delay_seconds = int(
            request.form.get("retry_delay_seconds", 60)
        )
        db.session.commit()
        flash(f"Job '{job.name}' updated.", "success")
        return redirect(url_for("jobs.list_jobs"))

    remotes = []
    try:
        rclone = current_app.config["RCLONE_CLIENT"]
        result = rclone.list_remotes()
        remotes = result.get("remotes", [])
    except Exception:
        pass
    return render_template("jobs/form.html", job=job, remotes=remotes)


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
        colon_idx = path.find(":")
        if colon_idx != -1:
            # Remote path: "gdrive:" or "gdrive:Backups/photos"
            fs = path[: colon_idx + 1]  # "gdrive:"
            remote = path[colon_idx + 1 :].lstrip("/")  # "Backups/photos" or ""
        else:
            # Local path: "/mnt/storage" or "/"
            fs = path.rstrip("/") + "/"
            remote = ""

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
    db.session.delete(job)
    db.session.commit()
    flash(f"Job '{name}' deleted.", "success")
    return redirect(url_for("jobs.list_jobs"))
