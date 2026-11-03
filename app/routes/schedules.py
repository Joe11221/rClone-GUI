from datetime import datetime, timedelta, timezone

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
from app.services.cron_text import describe_cron
from app.services.cron_trigger import cron_trigger

schedules_bp = Blueprint("schedules", __name__)


@schedules_bp.app_template_filter("cron_text")
def cron_text_filter(expr):
    """Cron expression in plain words, for any template."""
    return describe_cron(expr)


@schedules_bp.route("/")
def list_schedules():
    schedules = (
        Schedule.query.join(Job).order_by(Job.name).all()
    )
    scheduler_svc = current_app.config["SCHEDULER_SERVICE"]
    # Attach next run times
    for sched in schedules:
        nxt = scheduler_svc.get_next_run(sched.id)
        # local_time expects naive UTC; APScheduler hands back an aware datetime.
        sched.next_run = (
            nxt.astimezone(timezone.utc).replace(tzinfo=None) if nxt else None
        )
    return render_template("schedules/list.html", schedules=schedules)


@schedules_bp.route("/describe")
def describe():
    """Reading of ?expr= for the live preview on the forms: plain words, why
    the scheduler would refuse it, and the next few run times."""
    expr = " ".join((request.args.get("expr") or "").split())[:100]
    text = describe_cron(expr)
    error = _cron_error(expr) if expr else None
    return {
        "text": text,
        "described": bool(expr) and text != expr,
        "error": error,
        "next": _next_runs(expr) if expr and not error else [],
    }


def _next_runs(cron, count=3):
    """The next few fire times of a valid expression, in the scheduler's
    timezone and the app's usual date format."""
    trigger = cron_trigger(
        cron, timezone=current_app.config["SCHEDULER_SERVICE"].timezone
    )
    cursor = datetime.now(trigger.timezone)
    runs = []
    for _ in range(count):
        try:
            fire = trigger.get_next_fire_time(None, cursor)
        except (OverflowError, OSError):
            # An impossible date (31 February) searches past the year range.
            fire = None
        if fire is None:
            break
        runs.append(fire.strftime("%a %Y-%m-%d %H:%M"))
        cursor = fire + timedelta(seconds=1)
    return runs


def _cron_error(cron):
    """Why the scheduler would refuse this expression, or None."""
    try:
        cron_trigger(
            cron, timezone=current_app.config["SCHEDULER_SERVICE"].timezone
        )
    except ValueError:
        return (
            "Not a valid cron expression. Use five fields: minute hour "
            "day-of-month month day-of-week, for example 0 2 * * *."
        )
    return None


@schedules_bp.route("/create", methods=["GET", "POST"])
def create_schedule():
    if request.method == "POST":
        job_id = int(request.form["job_id"])
        # Check if job already has a schedule
        existing = Schedule.query.filter_by(job_id=job_id).first()
        if existing:
            flash("This job already has a schedule. Edit or delete it first.", "warning")
            return redirect(url_for("schedules.list_schedules"))

        cron = " ".join(request.form["cron_expression"].split())
        error = _cron_error(cron)
        if error:
            jobs = Job.query.order_by(Job.name).all()
            return render_template(
                "schedules/form.html", schedule=None, jobs=jobs,
                cron_value=cron, cron_error=error, selected_job_id=job_id,
            ), 400

        sched = Schedule(
            job_id=job_id,
            cron_expression=cron,
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
        cron = " ".join(request.form["cron_expression"].split())
        error = _cron_error(cron)
        if error:
            jobs = Job.query.order_by(Job.name).all()
            return render_template(
                "schedules/form.html", schedule=sched, jobs=jobs,
                cron_value=cron, cron_error=error,
            ), 400
        sched.cron_expression = cron
        sched.enabled = "enabled" in request.form
        sched.catch_up_after = datetime.utcnow()
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
    if sched.enabled:
        sched.catch_up_after = datetime.utcnow()
    db.session.commit()

    scheduler_svc = current_app.config["SCHEDULER_SERVICE"]
    if sched.enabled:
        scheduler_svc.add_schedule(sched)
        flash("Schedule enabled.", "success")
    else:
        scheduler_svc.remove_schedule(sched.id)
        flash("Schedule disabled.", "info")

    return redirect(url_for("schedules.list_schedules"))
