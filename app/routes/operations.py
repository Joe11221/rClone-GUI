from flask import (
    Blueprint,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    url_for,
)

from app.extensions import db
from app.models import Job, RunHistory

ops_bp = Blueprint("ops", __name__)


@ops_bp.route("/")
def running():
    active_runs = (
        RunHistory.query.filter_by(status="running")
        .order_by(RunHistory.started_at.desc())
        .all()
    )
    return render_template("operations/running.html", runs=active_runs)


@ops_bp.route("/start/<int:job_id>", methods=["POST"])
def start_job(job_id):
    job = Job.query.get_or_404(job_id)
    runner = current_app.config["JOB_RUNNER"]
    try:
        run = runner.start_job(job, triggered_by="manual")
        flash(
            f"Job '{job.name}' started (rclone job #{run.rclone_jobid}).",
            "success",
        )
    except Exception as e:
        flash(f"Failed to start job: {e}", "danger")
    return redirect(url_for("ops.running"))


@ops_bp.route("/stop/<int:run_id>", methods=["POST"])
def stop_job(run_id):
    run = RunHistory.query.get_or_404(run_id)
    runner = current_app.config["JOB_RUNNER"]
    runner.stop_job(run)
    flash("Job stopped.", "info")
    return redirect(url_for("ops.running"))


@ops_bp.route("/resume/<int:run_id>", methods=["POST"])
def resume_run(run_id):
    run = RunHistory.query.get_or_404(run_id)
    if run.status not in ("failed", "interrupted"):
        flash("Only failed or interrupted runs can be resumed.", "warning")
        return redirect(url_for("history.detail", run_id=run_id))

    job = run.job
    if not job:
        flash("Cannot resume: job has been deleted.", "danger")
        return redirect(url_for("history.detail", run_id=run_id))

    runner = current_app.config["JOB_RUNNER"]
    new_run = runner._resume_run(run, reason="manual_resume")
    if new_run:
        flash(f"Job '{job.name}' resumed as Run #{new_run.id}.", "success")
        return redirect(url_for("ops.running"))
    else:
        flash("Failed to resume job.", "danger")
        return redirect(url_for("history.detail", run_id=run_id))


@ops_bp.route("/status/<int:run_id>")
def job_status(run_id):
    run = RunHistory.query.get_or_404(run_id)
    rclone = current_app.config["RCLONE_CLIENT"]

    result = {
        "run_id": run.id,
        "job_name": run.job.name if run.job else "Unknown",
        "status": run.status,
        "started_at": run.started_at.isoformat() if run.started_at else None,
    }

    if run.rclone_jobid and run.status == "running":
        try:
            rc_status = rclone.job_status(run.rclone_jobid)
            result["rclone"] = rc_status
        except Exception as e:
            result["error"] = str(e)

        # Get per-job stats
        if run.stats_group:
            try:
                stats = rclone.get_stats(group=run.stats_group)
                result["stats"] = stats
            except Exception:
                pass

    return jsonify(result)
