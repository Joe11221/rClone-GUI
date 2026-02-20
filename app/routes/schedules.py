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
from app.models import Job, Schedule

schedules_bp = Blueprint("schedules", __name__)


@schedules_bp.route("/")
def list_schedules():
    schedules = (
        Schedule.query.join(Job).order_by(Job.name).all()
    )
    scheduler_svc = current_app.config["SCHEDULER_SERVICE"]
    # Attach next run times
    for sched in schedules:
        sched.next_run = scheduler_svc.get_next_run(sched.id)
    return render_template("schedules/list.html", schedules=schedules)


@schedules_bp.route("/create", methods=["GET", "POST"])
def create_schedule():
    if request.method == "POST":
        job_id = int(request.form["job_id"])
        # Check if job already has a schedule
        existing = Schedule.query.filter_by(job_id=job_id).first()
        if existing:
            flash("This job already has a schedule. Edit or delete it first.", "warning")
            return redirect(url_for("schedules.list_schedules"))

        sched = Schedule(
            job_id=job_id,
            cron_expression=request.form["cron_expression"],
            enabled="enabled" in request.form,
        )
        db.session.add(sched)
        db.session.commit()

        if sched.enabled:
            scheduler_svc = current_app.config["SCHEDULER_SERVICE"]
            scheduler_svc.add_schedule(sched)

        flash("Schedule created.", "success")
        return redirect(url_for("schedules.list_schedules"))

    jobs = Job.query.order_by(Job.name).all()
    return render_template("schedules/form.html", schedule=None, jobs=jobs)


@schedules_bp.route("/<int:schedule_id>/edit", methods=["GET", "POST"])
def edit_schedule(schedule_id):
    sched = Schedule.query.get_or_404(schedule_id)
    if request.method == "POST":
        sched.cron_expression = request.form["cron_expression"]
        sched.enabled = "enabled" in request.form
        db.session.commit()

        scheduler_svc = current_app.config["SCHEDULER_SERVICE"]
        if sched.enabled:
            scheduler_svc.add_schedule(sched)
        else:
            scheduler_svc.remove_schedule(sched.id)

        flash("Schedule updated.", "success")
        return redirect(url_for("schedules.list_schedules"))

    jobs = Job.query.order_by(Job.name).all()
    return render_template("schedules/form.html", schedule=sched, jobs=jobs)


@schedules_bp.route("/<int:schedule_id>/delete", methods=["POST"])
def delete_schedule(schedule_id):
    sched = Schedule.query.get_or_404(schedule_id)
    scheduler_svc = current_app.config["SCHEDULER_SERVICE"]
    scheduler_svc.remove_schedule(sched.id)
    db.session.delete(sched)
    db.session.commit()
    flash("Schedule deleted.", "success")
    return redirect(url_for("schedules.list_schedules"))


@schedules_bp.route("/<int:schedule_id>/toggle", methods=["POST"])
def toggle_schedule(schedule_id):
    sched = Schedule.query.get_or_404(schedule_id)
    sched.enabled = not sched.enabled
    db.session.commit()

    scheduler_svc = current_app.config["SCHEDULER_SERVICE"]
    if sched.enabled:
        scheduler_svc.add_schedule(sched)
        flash("Schedule enabled.", "success")
    else:
        scheduler_svc.remove_schedule(sched.id)
        flash("Schedule disabled.", "info")

    return redirect(url_for("schedules.list_schedules"))
