import json
import logging
from datetime import datetime

from app.extensions import db
from app.models import Job, RunHistory
from app.services.run_errors import record_failures
from app.services.suspects import SuspectTracker

logger = logging.getLogger(__name__)


class JobAlreadyRunning(Exception):
    def __init__(self, message, existing_run_id=None):
        super().__init__(message)
        self.existing_run_id = existing_run_id


def _mentions_max_delete(text):
    lowered = (text or "").lower()
    return "max-delete" in lowered or "max delete" in lowered


class JobRunner:
    def __init__(self, rclone_client, bandwidth=None, events=None):
        self.rclone = rclone_client
        self.bandwidth = bandwidth  # BandwidthController; None = no limits
        self.events = events  # EventBus; None = nobody listening
        self._active_runs = {}  # {run_history_id: rclone_jobid}
        self._pending_retries = set()  # run_ids awaiting delayed retry
        self.suspects = SuspectTracker()

    def start_job(self, job, triggered_by="manual", allow_deletes=False):
        """Start a run; returns it, already ``held`` if a safety check stopped it.

        ``allow_deletes`` is the one-off override: no preflight, no delete limit.
        """
        existing = RunHistory.query.filter_by(
            job_id=job.id, status="running"
        ).first()
        if existing:
            raise JobAlreadyRunning(
                f"Job '{job.name}' is already running as Run #{existing.id}",
                existing_run_id=existing.id,
            )

        # Create run record first to get an ID for the stats group
        run = RunHistory(
            job_id=job.id,
            triggered_by=triggered_by,
            status="running",
        )
        db.session.add(run)
        db.session.flush()  # get run.id

        run.stats_group = f"job_{run.id}"
        if self._dispatch(run, job, allow_deletes=allow_deletes):
            self._active_runs[run.id] = run.rclone_jobid
        return run

    def _dispatch(self, run, job, allow_deletes=False):
        """Preflight, record the settings and start rclone for ``run``.

        Returns False (run marked held, rclone untouched) if a safety check
        failed, True once rclone has accepted the job and the run is committed.
        """
        config = job.to_rclone_config()
        if allow_deletes:
            config.pop("MaxDelete", None)
        filters = job.to_rclone_filter()

        if job.sync_type == "sync" and not allow_deletes:
            held = self._preflight(job)
            if held:
                self._hold(run, job, *held)
                return False

        run.config_snapshot = json.dumps(
            {
                "source": job.source,
                "destination": job.destination,
                "sync_type": job.sync_type,
                "config": config,
                "filter": filters,
            }
        )

        dispatch = {
            "sync": self.rclone.start_sync,
            "copy": self.rclone.start_copy,
            "move": self.rclone.start_move,
        }
        fn = dispatch[job.sync_type]
        self._update_bandwidth(starting=job)
        result = fn(
            src_fs=job.source,
            dst_fs=job.destination,
            group=run.stats_group,
            _config=config,
            _filter=filters,
        )
        run.rclone_jobid = result["jobid"]
        db.session.commit()
        return True

    def _preflight(self, job):
        """(guard, reason) if a sync must not start, else None.

        An empty or unreadable source (say a dropped NFS mount) would make the
        sync delete everything at the destination.
        """
        try:
            entries = self.rclone.list_top(job.source)
        except Exception as exc:
            return (
                "source_unreachable",
                f"Source could not be read, so the sync was not started: {exc}",
            )
        if not entries:
            return (
                "source_empty",
                "Source folder is empty. A sync would delete everything at "
                "the destination, so it was not started.",
            )
        return None

    def _hold(self, run, job, guard, reason):
        now = datetime.utcnow()
        run.status = "held"
        run.guard = guard
        run.error_message = reason
        run.started_at = run.finished_at = now
        db.session.commit()
        logger.warning("Held Run #%d of '%s': %s", run.id, job.name, reason)
        if self.events:
            self.events.emit(
                "run_held",
                run_id=run.id,
                job_id=job.id,
                job_name=job.name,
                guard=guard,
                reason=reason,
            )

    def _emit_finished(self, run, will_retry=False):
        if not self.events:
            return
        job = db.session.get(Job, run.job_id)
        self.events.emit(
            "run_finished",
            run_id=run.id,
            job_id=run.job_id,
            job_name=job.name if job else None,
            status=run.status,
            errors=run.errors or 0,
            error_message=run.error_message,
            guard=run.guard,
            will_retry=will_retry,
        )

    def _flag_delete_limit(self, run):
        """Mark a run that rclone stopped at --max-delete."""
        snapshot = run.get_config_snapshot() or {}
        limit = (snapshot.get("config") or {}).get("MaxDelete")
        what = f" of {limit} files" if limit is not None else ""
        run.guard = "delete_limit"
        run.error_message = (
            f"Stopped after reaching the delete limit{what}. "
            + (run.error_message or "")
        ).strip()

    def _update_bandwidth(self, starting=None):
        if self.bandwidth:
            self.bandwidth.reconcile(starting=starting)

    def stop_job(self, run):
        if run.rclone_jobid:
            try:
                self.rclone.job_stop(run.rclone_jobid)
            except Exception:
                pass

        # Capture final stats before marking stopped
        self.collect_errors(run)
        self._save_final_stats(run)

        run.status = "stopped"
        run.finished_at = datetime.utcnow()
        db.session.commit()
        self._active_runs.pop(run.id, None)
        self._update_bandwidth()
        self._emit_finished(run)

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
            return None
        try:
            stats = self.rclone.get_stats(group=run.stats_group)
            self._apply_stats(run, stats)
            return stats
        except Exception as exc:
            logger.warning(
                "Failed to fetch final stats for Run #%d (group=%s): %s",
                run.id,
                run.stats_group,
                exc,
            )
        return None

    def collect_errors(self, run):
        """Copy failed transfers/checks of a run out of rclone into the DB."""
        if not run.stats_group:
            return 0
        try:
            reply = self.rclone.get_transferred(group=run.stats_group)
            transferred = (reply or {}).get("transferred")
            self.suspects.observe(run.id, transferred)
            return record_failures(run, transferred)
        except Exception as exc:
            db.session.rollback()
            logger.warning(
                "Failed to collect errors for Run #%d (group=%s): %s",
                run.id,
                run.stats_group,
                exc,
            )
            return 0

    def collect_active_errors(self):
        """Poll every tracked run; called often since rclone forgets old entries."""
        for run_id in list(self._active_runs):
            run = db.session.get(RunHistory, run_id)
            if run:
                self.collect_errors(run)
                self._note_active_transfers(run)
        self.suspects.prune(self._active_runs)

    @staticmethod
    def _apply_stats(run, stats):
        """Copy rclone's counters onto the run; returns True if any changed."""
        values = {
            "bytes_transferred": stats.get("bytes", 0),
            "files_transferred": stats.get("transfers", 0),
            "errors": stats.get("errors", 0),
            "deletes": stats.get("deletes", 0),
            "checks": stats.get("checks", 0),
        }
        changed = False
        for attr, value in values.items():
            if getattr(run, attr) != value:
                setattr(run, attr, value)
                changed = True
        return changed

    def _note_active_transfers(self, run):
        try:
            stats = self.rclone.get_stats(group=run.stats_group)
        except Exception as exc:
            logger.debug("No live stats for Run #%d: %s", run.id, exc)
            return
        self.suspects.observe_active(run.id, stats.get("transferring"))
        # Snapshot the totals as the run goes. If rclone dies with the server,
        # nothing can be fetched afterwards and the run would read 0 bytes /
        # 0 files; the last snapshot is then the best record of what it did.
        try:
            if self._apply_stats(run, stats):
                db.session.commit()
        except Exception as exc:
            db.session.rollback()
            logger.warning(
                "Failed to save live stats for Run #%d: %s", run.id, exc
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

                self.collect_errors(run)
                stats = self._save_final_stats(run)
                # Failures not tied to a file (listing, deleting, ...) only show
                # up as rclone's last error; keep it if nothing else explains
                # the failure.
                if (
                    not success
                    and not run.error_message
                    and stats
                    and stats.get("lastError")
                ):
                    run.error_message = str(stats["lastError"])

                if not success and (
                    _mentions_max_delete(run.error_message)
                    or _mentions_max_delete((stats or {}).get("lastError"))
                ):
                    self._flag_delete_limit(run)

                db.session.commit()
                del self._active_runs[run_id]

                # Auto-retry on failure (never after a safety stop)
                will_retry = False
                if (
                    not success
                    and not run.guard
                    and run_id not in self._pending_retries
                ):
                    job = db.session.get(Job, run.job_id)
                    if (
                        job
                        and job.auto_resume
                        and run.retry_count < job.max_retries
                    ):
                        self._schedule_retry(run, job.retry_delay_seconds)
                        will_retry = True
                self._emit_finished(run, will_retry=will_retry)

        # Limits depend on the time of day and on which runs are active, so
        # this also opens and closes bandwidth windows.
        self._update_bandwidth()

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
                # A held or skipped resume needs attention, so don't hush it.
                self._emit_finished(
                    run,
                    will_retry=bool(new_run and new_run.status == "running"),
                )
            else:
                run.status = "failed"
                db.session.commit()
                self._emit_finished(run)
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

        new_run.stats_group = f"job_{new_run.id}"

        try:
            if not self._dispatch(new_run, job):
                return new_run  # held by a safety check
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
                if not run or run.status != "failed" or run.guard:
                    return  # status changed (e.g., user manually restarted)
                self._resume_run(run, reason="auto_retry")

        gevent.spawn_later(delay_seconds, _do_retry)
