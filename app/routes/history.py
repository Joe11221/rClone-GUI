from flask import Blueprint, render_template

from app.models import RunHistory

history_bp = Blueprint("history", __name__)


@history_bp.route("/")
def list_history():
    runs = (
        RunHistory.query.order_by(RunHistory.started_at.desc())
        .limit(100)
        .all()
    )
    return render_template("history/list.html", runs=runs)


@history_bp.route("/<int:run_id>")
def detail(run_id):
    run = RunHistory.query.get_or_404(run_id)
    return render_template("history/detail.html", run=run)
