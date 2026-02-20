import json

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

from app.extensions import db
from app.models import Job

jobs_bp = Blueprint("jobs", __name__)


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


@jobs_bp.route("/<int:job_id>/delete", methods=["POST"])
def delete_job(job_id):
    job = Job.query.get_or_404(job_id)
    name = job.name
    db.session.delete(job)
    db.session.commit()
    flash(f"Job '{name}' deleted.", "success")
    return redirect(url_for("jobs.list_jobs"))
