from flask import Blueprint, render_template, request

from app.models import Job, RunError, RunHistory

history_bp = Blueprint("history", __name__)

PER_PAGE = 50
STATUSES = [
    "running", "completed", "failed", "stopped", "interrupted", "held", "skipped",
]


@history_bp.route("/")
def list_history():
    jobs = Job.query.order_by(Job.name).all()
    job_id = request.args.get("job", type=int)
    if job_id not in {j.id for j in jobs}:
        job_id = None
    status = request.args.get("status", "")
    if status not in STATUSES:
        status = ""
    page = max(1, request.args.get("page", 1, type=int) or 1)

    query = RunHistory.query
    if job_id:
        query = query.filter(RunHistory.job_id == job_id)
    if status:
        query = query.filter(RunHistory.status == status)
    total = query.count()
    pages = max(1, -(-total // PER_PAGE))
    page = min(page, pages)

    # Newest start first; id breaks ties so paging stays stable.
    runs = (
        query.order_by(RunHistory.started_at.desc(), RunHistory.id.desc())
        .offset((page - 1) * PER_PAGE)
        .limit(PER_PAGE)
        .all()
    )
    return render_template(
        "history/list.html",
        runs=runs,
        jobs=jobs,
        statuses=STATUSES,
        job_id=job_id,
        status=status,
        page=page,
        pages=pages,
        total=total,
        per_page=PER_PAGE,
    )


@history_bp.route("/<int:run_id>")
def detail(run_id):
    run = RunHistory.query.get_or_404(run_id)
    error_count = RunError.query.filter_by(run_id=run.id).count()
    return render_template(
        "history/detail.html", run=run, error_count=error_count
    )
