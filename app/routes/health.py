from datetime import datetime

from flask import Blueprint, current_app, jsonify

from app.services import health

health_bp = Blueprint("health", __name__)


@health_bp.route("/api/health")
def api_health():
    report, _jobs = health.current_report(
        current_app.config["TIMEZONE"],
        current_app.config["HEALTH_GRACE_MINUTES"],
        datetime.utcnow(),
    )
    return jsonify(health.report_to_dict(report))
