from datetime import datetime

from app.extensions import db
from app.models import Job, RunHistory


class JobRunner:
    def __init__(self, rclone_client):
        self.rclone = rclone_client
        self._active_runs = {}  # {run_history_id: rclone_jobid}
        self._pending_retries = set()  # run_ids awaiting delayed retry

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
                success = status.get("success", False)
                run.status = "completed" if success else "failed"
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

                # Auto-retry on failure
                if not success and run_id not in self._pending_retries:
                    job = db.session.get(Job, run.job_id)
                    if (
                        job
                        and job.auto_resume
                        and run.retry_count < job.max_retries
                    ):
                        self._schedule_retry(run, job.retry_delay_seconds)

    def restore_active_runs(self):
        """On startup, restore tracking or resume interrupted runs."""
        running = RunHistory.query.filter_by(status="running").all()
        for run in running:
            if run.rclone_jobid:
                try:
                    self.rclone.job_status(run.rclone_jobid)
                    # Job still alive in rclone — re-track it
                    self._active_runs[run.id] = run.rclone_jobid
                except Exception:
                    # Job lost after restart
                    run.finished_at = datetime.utcnow()
                    run.error_message = "Lost track of job after restart"

                    job = db.session.get(Job, run.job_id)
                    if (
                        job
                        and job.auto_resume
                        and run.retry_count < job.max_retries
                    ):
                        # Resume: mark interrupted and start a new run
                        run.status = "interrupted"
                        db.session.commit()
                        self._resume_run(run, reason="startup_resume")
                    else:
                        run.status = "failed"
                        db.session.commit()

    def _resume_run(self, old_run, reason="auto_retry"):
        """Mark old_run as interrupted and start a fresh run for the same job."""
        job = db.session.get(Job, old_run.job_id)
        if not job:
            old_run.status = "failed"
            old_run.finished_at = old_run.finished_at or datetime.utcnow()
            old_run.error_message = (
                (old_run.error_message or "")
                + " [Job deleted, cannot resume]"
            )
            db.session.commit()
            self._active_runs.pop(old_run.id, None)
            return None

        if old_run.retry_count >= job.max_retries:
            old_run.error_message = (
                (old_run.error_message or "")
                + f" [Max retries ({job.max_retries}) exhausted]"
            )
            db.session.commit()
            return None

        # Mark old run as interrupted (if not already)
        if old_run.status != "interrupted":
            old_run.status = "interrupted"
            old_run.finished_at = old_run.finished_at or datetime.utcnow()
            db.session.commit()
        self._active_runs.pop(old_run.id, None)

        # Create new run linked to old one
        new_run = RunHistory(
            job_id=job.id,
            triggered_by=reason,
            status="running",
            retry_of_run_id=old_run.id,
            retry_count=old_run.retry_count + 1,
        )
        db.session.add(new_run)
        db.session.flush()

        stats_group = f"job_{new_run.id}"
        new_run.stats_group = stats_group

        dispatch = {
            "sync": self.rclone.start_sync,
            "copy": self.rclone.start_copy,
            "move": self.rclone.start_move,
        }

        try:
            fn = dispatch[job.sync_type]
            result = fn(
                src_fs=job.source,
                dst_fs=job.destination,
                group=stats_group,
                _config=job.to_rclone_config(),
                _filter=job.to_rclone_filter(),
            )
            new_run.rclone_jobid = result["jobid"]
            db.session.commit()
            self._active_runs[new_run.id] = new_run.rclone_jobid
            return new_run
        except Exception as e:
            new_run.status = "failed"
            new_run.finished_at = datetime.utcnow()
            new_run.error_message = f"Failed to start resume: {e}"
            db.session.commit()
            return None

    def _schedule_retry(self, failed_run, delay_seconds):
        """Schedule a retry after a delay using gevent.spawn_later."""
        import gevent
        from flask import current_app

        app = current_app._get_current_object()
        run_id = failed_run.id
        self._pending_retries.add(run_id)

        def _do_retry():
            with app.app_context():
                self._pending_retries.discard(run_id)
                run = db.session.get(RunHistory, run_id)
                if not run or run.status != "failed":
                    return  # status changed (e.g., user manually restarted)
                # Check no other run for same job is already active
                for active_run_id in self._active_runs:
                    active_run = db.session.get(RunHistory, active_run_id)
                    if active_run and active_run.job_id == run.job_id:
                        return  # job already running
                self._resume_run(run, reason="auto_retry")

        gevent.spawn_later(delay_seconds, _do_retry)
