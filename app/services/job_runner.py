from datetime import datetime

from app.extensions import db
from app.models import Job, RunHistory


class JobRunner:
    def __init__(self, rclone_client):
        self.rclone = rclone_client
        self._active_runs = {}  # {run_history_id: rclone_jobid}

    def start_job(self, job, triggered_by="manual"):
        config = job.to_rclone_config()
        filters = job.to_rclone_filter()

        # Create run record first to get an ID for the stats group
        run = RunHistory(
            job_id=job.id,
            triggered_by=triggered_by,
            status="running",
        )
        db.session.add(run)
        db.session.flush()  # get run.id

        stats_group = f"job_{run.id}"
        run.stats_group = stats_group

        dispatch = {
            "sync": self.rclone.start_sync,
            "copy": self.rclone.start_copy,
            "move": self.rclone.start_move,
        }

        fn = dispatch[job.sync_type]
        result = fn(
            src_fs=job.source,
            dst_fs=job.destination,
            group=stats_group,
            _config=config,
            _filter=filters,
        )

        run.rclone_jobid = result["jobid"]
        db.session.commit()

        self._active_runs[run.id] = run.rclone_jobid
        return run

    def stop_job(self, run):
        if run.rclone_jobid:
            try:
                self.rclone.job_stop(run.rclone_jobid)
            except Exception:
                pass
        run.status = "stopped"
        run.finished_at = datetime.utcnow()
        db.session.commit()
        self._active_runs.pop(run.id, None)

    def check_and_update_runs(self):
        for run_id, rclone_jobid in list(self._active_runs.items()):
            try:
                status = self.rclone.job_status(rclone_jobid)
            except Exception:
                continue

            if status.get("finished"):
                run = db.session.get(RunHistory, run_id)
                if not run:
                    del self._active_runs[run_id]
                    continue

                run.finished_at = datetime.utcnow()
                run.status = "completed" if status.get("success") else "failed"
                if status.get("error"):
                    run.error_message = str(status["error"])

                # Grab final stats for this job's group
                try:
                    stats = self.rclone.get_stats(group=run.stats_group)
                    run.bytes_transferred = stats.get("bytes", 0)
                    run.files_transferred = stats.get("transfers", 0)
                    run.errors = stats.get("errors", 0)
                except Exception:
                    pass

                db.session.commit()
                del self._active_runs[run_id]

    def restore_active_runs(self):
        """On startup, restore tracking of any runs marked as running in the DB."""
        running = RunHistory.query.filter_by(status="running").all()
        for run in running:
            if run.rclone_jobid:
                try:
                    self.rclone.job_status(run.rclone_jobid)
                    self._active_runs[run.id] = run.rclone_jobid
                except Exception:
                    # Job no longer exists in rclone — mark as failed
                    run.status = "failed"
                    run.finished_at = datetime.utcnow()
                    run.error_message = "Lost track of job after restart"
                    db.session.commit()
