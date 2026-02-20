import uuid

from flask import Blueprint, Response, current_app, stream_with_context

sse_bp = Blueprint("sse", __name__)


@sse_bp.route("/api/events")
def stream():
    collector = current_app.config["STATS_COLLECTOR"]
    client_id = str(uuid.uuid4())
    q = collector.subscribe(client_id)

    def generate():
        try:
            while True:
                try:
                    data = q.get(timeout=30)
                    yield f"data: {data}\n\n"
                except Exception:
                    # Keepalive ping
                    yield "data: {}\n\n"
        finally:
            collector.unsubscribe(client_id)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
