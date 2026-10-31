import csv
import io

from flask import Blueprint, Response, jsonify, request, stream_with_context
from sqlalchemy import func, or_

from app.extensions import db
from app.models import RunError, RunHistory

run_errors_bp = Blueprint("run_errors", __name__)

DEFAULT_LIMIT = 100
MAX_LIMIT = 500


def _filtered(run_id):
    """Errors of a run narrowed by the ?category= and ?q= query parameters."""
    query = RunError.query.filter(RunError.run_id == run_id)
    category = request.args.get("category")
    if category:
        query = query.filter(RunError.category == category)
    needle = (request.args.get("q") or "").strip()
    if needle:
        escaped = (
            needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        )
        pattern = f"%{escaped}%"
        query = query.filter(
            or_(
                RunError.path.ilike(pattern, escape="\\"),
                RunError.message.ilike(pattern, escape="\\"),
            )
        )
    return query


def _int_arg(name, default=None):
    try:
        return int(request.args.get(name, ""))
    except ValueError:
        return default


@run_errors_bp.route("/<int:run_id>/errors")
def list_errors(run_id):
    """Failures of a run as JSON, newest first, paged by id cursors.

    ``?after=<id>`` returns what failed since that id (for the live feed) and
    ``?before=<id>`` the next older page. Cursors rather than offsets keep
    paging cheap and stable while new failures keep arriving. With
    ``?summary=1`` the reply also groups the run's failures by cause.
    """
    run = RunHistory.query.get_or_404(run_id)
    limit = max(1, min(_int_arg("limit", DEFAULT_LIMIT), MAX_LIMIT))
    after = _int_arg("after")
    before = _int_arg("before")

    query = _filtered(run.id)
    matched = query.count() if request.args.get("count") else None
    if after is not None:
        query = query.filter(RunError.id > after)
    if before is not None:
        query = query.filter(RunError.id < before)

    rows = query.order_by(RunError.id.desc()).limit(limit + 1).all()
    has_more = len(rows) > limit
    rows = rows[:limit]

    reply = {
        "errors": [row.to_dict() for row in rows],
        "has_more": has_more,
        "total": RunError.query.filter_by(run_id=run.id).count(),
        "last_id": db.session.query(func.max(RunError.id))
        .filter(RunError.run_id == run.id)
        .scalar()
        or 0,
        "status": run.status,
    }
    if matched is not None:
        reply["matched"] = matched
    if request.args.get("summary"):
        reply["categories"] = _categories(run.id)
    return jsonify(reply)


def _categories(run_id):
    groups = (
        db.session.query(
            RunError.category,
            func.count(RunError.id),
            func.min(RunError.occurred_at),
            func.max(RunError.occurred_at),
        )
        .filter(RunError.run_id == run_id)
        .group_by(RunError.category)
        .order_by(func.count(RunError.id).desc(), RunError.category)
        .limit(100)
        .all()
    )
    return [
        {
            "category": category,
            "count": count,
            "first": first.isoformat() + "Z" if first else None,
            "last": last.isoformat() + "Z" if last else None,
        }
        for category, count, first, last in groups
    ]


def _spreadsheet_safe(value):
    """Stop file names like '=cmd|...' from being run as formulas when opened."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


@run_errors_bp.route("/<int:run_id>/errors.csv")
def export_errors(run_id):
    """Every stored failure of a run (honouring filters) as a CSV download."""
    run = RunHistory.query.get_or_404(run_id)
    query = _filtered(run.id).order_by(RunError.id)

    def generate():
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["time_utc", "kind", "path", "size", "message"])
        yield buffer.getvalue()
        for row in query.yield_per(500):
            buffer.seek(0)
            buffer.truncate()
            writer.writerow(
                [
                    row.occurred_at.isoformat() if row.occurred_at else "",
                    row.kind,
                    _spreadsheet_safe(row.path),
                    row.size if row.size is not None else "",
                    _spreadsheet_safe(row.message),
                ]
            )
            yield buffer.getvalue()

    return Response(
        stream_with_context(generate()),
        mimetype="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="run-{run.id}-errors.csv"'
        },
    )
