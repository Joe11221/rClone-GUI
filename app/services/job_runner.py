import logging
from datetime import datetime

from app.extensions import db
from app.models import Job, RunHistory

logger = logging.getLogger(__name__)


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

        # Capture final stats before marking stopped
        self._save_final_stats(run)

        run.status = "stopped"
        run.finished_at = datetime.utcnow()
        db.session.commit()
        self._active_runs.pop(run.id, None)

    def _parse_rclone_time(self, time_str):
        """Parse an rclone ISO8601 timestamp (e.g. '2025-01-01T12:00:00.123Z')
        into a naive-UTC datetime, or None on failure."""
        if not time_str:
            return None
        try:
            # Python 3.11+ handles 'Z' directly; for 3.9/3.10 replace it
            return datetime.fromisoformat(
                time_str.replace("Z", "+00:00")
            ).replace(tzinfo=None)
        except (ValueError, TypeError):
            return None

    def _save_final_stats(self, run):
        """Fetch per-group stats from rclone and persist them on the run."""
        if not run.stats_group:
            return
        try:
            stats = self.rclone.get_stats(group=run.stats_group)
            run.bytes_transferred = stats.get("bytes", 0)
            run.files_transferred = stats.get("transfers", 0)
            run.errors = stats.get("errors", 0)
        except Exception as exc:
            logger.warning(
                "Failed to fetch final stats for Run #%d (group=%s): %s",
                run.id,
                run.stats_group,
                exc,
            )

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

                # Use rclone's actual end time for accuracy (falls back to now)
                run.finished_at = (
                    self._parse_rclone_time(status.get("endTime"))
                    or datetime.utcnow()
                )

                success = status.get("success", False)
                run.status = "completed" if success else "failed"
                if status.get("error"):
                    run.error_message = str(status["error"])

                self._save_final_stats(run)

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
        if running:
            logger.info(
                "Found %d orphaned 'running' record(s) on startup", len(running)
            )

        for run in running:
            # Re-read from DB to get fresh state (guard against concurrent
            # workers that may have already processed this run).
            db.session.expire(run)
            if run.status != "running":
                logger.info(
                    "Run #%d already handled (status=%s), skipping",
                    run.id,
                    run.status,
                )
                continue

            if run.rclone_jobid:
                try:
                    self.rclone.job_status(run.rclone_jobid)
                    # Job still alive in rclone — re-track it
                    self._active_runs[run.id] = run.rclone_jobid
                    logger.info(
                        "Run #%d still alive in rclone, re-tracking", run.id
                    )
                    continue
                except Exception:
                    pass

            # Job lost after restart — mark failed or resume
            run.finished_at = datetime.utcnow()
            run.error_message = "Lost track of job after restart"

            job = db.session.get(Job, run.job_id)
            if (
                job
                and job.auto_resume
                and run.retry_count < job.max_retries
            ):
                run.status = "interrupted"
                db.session.commit()
                new_run = self._resume_run(run, reason="startup_resume")
                if new_run:
                    logger.info(
                        "Run #%d interrupted, resumed as Run #%d",
                        run.id,
                        new_run.id,
                    )
                else:
                    logger.info(
                        "Run #%d interrupted, resume skipped "
                        "(duplicate or rclone error)",
                        run.id,
                    )
            else:
                run.status = "failed"
                db.session.commit()
                logger.info(
                    "Run #%d marked failed (auto_resume=%s, retries=%d/%s)",
                    run.id,
                    getattr(job, "auto_resume", "N/A") if job else "no job",
                    run.retry_count,
                    getattr(job, "max_retries", "?") if job else "?",
                )

    def _resume_run(self, old_run, reason="auto_retry"):
        """Mark old_run as interrupted and start a fresh run for the same job.

        Returns the new RunHistory on success, or None if resume was skipped.
        """
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

        # For non-manual resumes, enforce the auto_resume toggle and retry cap
        if reason != "manual_resume":
            if not job.auto_resume:
                return None
            if old_run.retry_count >= job.max_retries:
                old_run.error_message = (
                    (old_run.error_message or "")
                    + f" [Max retries ({job.max_retries}) exhausted]"
                )
                db.session.commit()
                return None

        # Guard: check no run is already active in the DB for this job.
        # This prevents duplicates when multiple workers or retries race.
        existing = RunHistory.query.filter_by(
            job_id=job.id, status="running"
        ).first()
        if existing and existing.id != old_run.id:
            logger.info(
                "Skipping resume for job '%s': Run #%d is already running",
                job.name,
                existing.id,
            )
            return None

        # Mark old run as interrupted (if not already)
        if old_run.status not in ("interrupted", "stopped"):
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
            logger.info(
                "Resumed job '%s' as Run #%d (retry #%d, reason=%s)",
                job.name,
                new_run.id,
                new_run.retry_count,
                reason,
            )
            return new_run
        except Exception as e:
            new_run.status = "failed"
            new_run.finished_at = datetime.utcnow()
            new_run.error_message = f"Failed to start resume: {e}"
            db.session.commit()
            logger.warning(
                "Failed to resume job '%s': %s", job.name, e
            )
            return None

    def _schedule_retry(self, failed_run, delay_seconds):
        """Schedule a retry after a delay using gevent.spawn_later."""
        import gevent
        from flask import current_app

        app = current_app._get_current_object()
        run_id = failed_run.id
        self._pending_retries.add(run_id)

        logger.info(
            "Scheduling retry for Run #%d in %ds", run_id, delay_seconds
        )

        def _do_retry():
            with app.app_context():
                self._pending_retries.discard(run_id)
                run = db.session.get(RunHistory, run_id)
                if not run or run.status != "failed":
                    return  # status changed (e.g., user manually restarted)
                self._resume_run(run, reason="auto_retry")

        gevent.spawn_later(delay_seconds, _do_retry)
