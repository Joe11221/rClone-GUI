import json
import time

from flask import Blueprint, Response, current_app, stream_with_context

sse_bp = Blueprint("sse", __name__)


@sse_bp.route("/api/events")
def stream():
    """SSE endpoint that polls rclone directly — no background threads."""
    rclone = current_app.config["RCLONE_CLIENT"]
    job_runner = current_app.config["JOB_RUNNER"]

    def generate():
        while True:
            try:
                active_runs = dict(job_runner._active_runs)

                if not active_runs:
                    # Nothing running — send minimal keepalive, sleep longer
                    payload = json.dumps(
                        {"runs": {}, "active_count": 0, "total_speed": 0, "total_bytes": 0}
                    )
                    yield f"data: {payload}\n\n"
                    time.sleep(5)
                    continue

                # Poll stats only for our app's runs
                run_stats = {}
                total_speed = 0
                total_bytes = 0

                for run_id, rclone_jobid in active_runs.items():
                    stats_group = f"job_{run_id}"
                    try:
                        stats = rclone.get_stats(group=stats_group)
                        run_stats[str(run_id)] = {
                            "stats": stats,
                            "rclone_jobid": rclone_jobid,
                        }
                        total_speed += stats.get("speed", 0) or 0
                        total_bytes += stats.get("bytes", 0) or 0
                    except Exception:
                        pass

                payload = json.dumps(
                    {
                        "runs": run_stats,
                        "active_count": len(active_runs),
                        "total_speed": total_speed,
                        "total_bytes": total_bytes,
                        "timestamp": time.time(),
                    }
                )
                yield f"data: {payload}\n\n"
                time.sleep(2)

            except GeneratorExit:
                return
            except Exception:
                yield "data: {}\n\n"
                time.sleep(5)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
