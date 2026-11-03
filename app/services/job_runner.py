import json
import logging
from datetime import datetime, timezone

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
        self._previewing = set()  # run_ids still counting deletions (dry run)
        self.suspects = SuspectTracker()

    def is_previewing(self, run_id):
        return run_id in self._previewing

    def start_job(
        self, job, triggered_by="manual", allow_deletes=False, dry_run=False
    ):
        """Start a run; returns it, already ``held`` if a safety check stopped it.

        ``allow_deletes`` is the one-off override: no preflight, no delete limit.
        ``dry_run`` asks rclone to change nothing; it skips every safety check
        (there is nothing to protect) and is recorded as ``triggered_by``
        "dry_run", which health and notifications ignore.
        """
        if dry_run:
            triggered_by = "dry_run"
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
        dry_run = run.triggered_by == "dry_run"
        config = job.to_rclone_config(dry_run=dry_run)
        if allow_deletes:
            config.pop("MaxDelete", None)
        snapshot = {
            "source": job.source,
            "destination": job.destination,
            "sync_type": job.sync_type,
            "config": config,
            "filter": job.to_rclone_filter(),
        }

        preview = False
        if job.sync_type == "sync" and not allow_deletes and not dry_run:
            held = self._preflight(job)
            if held:
                self._hold(run, job, *held)
                return False
            # With no limit (-1) there is nothing to compare the count to.
            preview = job.preview_deletes is not False and "MaxDelete" in config

        run.config_snapshot = json.dumps(snapshot)
        if preview:
            self._start_preview(run, snapshot)
        else:
            self._launch(run, job, snapshot)
        return True

    def _launch(self, run, job, snapshot):
        """Start the run's real rclone job from its recorded settings."""
        dispatch = {
            "sync": self.rclone.start_sync,
            "copy": self.rclone.start_copy,
            "move": self.rclone.start_move,
        }
        fn = dispatch[snapshot["sync_type"]]
        self._update_bandwidth(starting=job)
        result = fn(
            src_fs=snapshot["source"],
            dst_fs=snapshot["destination"],
            group=run.stats_group,
            _config=snapshot["config"],
            _filter=snapshot["filter"],
        )
        run.rclone_jobid = result["jobid"]
        db.session.commit()

    def _start_preview(self, run, snapshot):
        """Start the dry run that counts deletions; never waits for it.

        The run stays ``running`` with ``phase`` "preview", and until the dry
        run ends its rclone job id and stats group are the dry run's.
        """
        config = {k: v for k, v in snapshot["config"].items() if k != "MaxDelete"}
        config["DryRun"] = True
        run.phase = "preview"
        run.stats_group = f"job_{run.id}_preview"
        result = self.rclone.start_sync(
            src_fs=snapshot["source"],
            dst_fs=snapshot["destination"],
            group=run.stats_group,
            _config=config,
            _filter=snapshot["filter"],
        )
        run.rclone_jobid = result["jobid"]
        db.session.commit()
        self._previewing.add(run.id)

    def _finish_preview(self, run, status):
        """The dry run has ended: hold the run, or start the real sync."""
        self._previewing.discard(run.id)
        self._active_runs.pop(run.id, None)
        job = db.session.get(Job, run.job_id)
        snapshot = run.get_config_snapshot() or {}
        limit = (snapshot.get("config") or {}).get("MaxDelete")
        group = run.stats_group
        run.phase = None
        run.stats_group = f"job_{run.id}"

        stats = {}
        try:
            stats = self.rclone.get_stats(group=group)
        except Exception as exc:
            logger.warning("No stats for preview of Run #%d: %s", run.id, exc)
        if not status.get("success"):
            error = status.get("error") or stats.get("lastError")
            reason = "Could not count the deletions, so the sync was not started" + (
                f": {error}" if error else "."
            )
            self._hold(run, job, "preview_failed", reason, keep_start=True)
            return

        count = int(stats.get("deletes") or 0)
        run.would_delete = count
        if limit is not None and count > limit:
            run.would_delete_paths = json.dumps(self._deleting_paths(group))
            reason = (
                f"This sync would delete {count:,} files from "
                f"{snapshot.get('destination')}, more than the limit of "
                f"{limit:,}. It was not started."
            )
            self._hold(run, job, "delete_preview", reason, keep_start=True)
            return

        try:
            self._launch(run, job, snapshot)
        except Exception as exc:
            db.session.rollback()
            run.status = "failed"
            run.finished_at = datetime.utcnow()
            run.error_message = f"Failed to start the sync after the preview: {exc}"
            db.session.commit()
            logger.warning("Failed to start Run #%d after preview: %s", run.id, exc)
            self._emit_finished(run)
            return
        self._active_runs[run.id] = run.rclone_jobid

    def _deleting_paths(self, group, limit=50):
        """Up to ``limit`` paths a dry run would delete. rclone's transferred
        list only holds its latest ~100 entries, so a huge delete shows a
        sample; the count comes from the stats instead."""
        try:
            reply = self.rclone.get_transferred(group=group)
        except Exception:
            return []
        names = [
            t.get("name")
            for t in (reply or {}).get("transferred") or []
            if t.get("what") == "deleting" and t.get("name")
        ]
        return names[:limit]

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

    def _hold(self, run, job, guard, reason, keep_start=False):
        now = datetime.utcnow()
        run.status = "held"
        run.guard = guard
        run.error_message = reason
        run.finished_at = now
        if not keep_start:
            run.started_at = now
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
        # A dry run is something the user asked to look at; nobody needs a ping.
        if not self.events or run.triggered_by == "dry_run":
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

        if run.phase == "preview":
            # Those stats belong to the dry run, not to anything this run did.
            run.phase = None
        else:
            # Capture final stats before marking stopped
            self.collect_errors(run)
            self._save_final_stats(run)

        run.status = "stopped"
        run.finished_at = datetime.utcnow()
        db.session.commit()
        self._active_runs.pop(run.id, None)
        self._previewing.discard(run.id)
        self._update_bandwidth()
        self._emit_finished(run)

    def _parse_rclone_time(self, time_str):
        """Parse an rclone ISO8601 timestamp (e.g. '2025-01-01T12:00:00.123Z')
        into a naive-UTC datetime, or None on failure."""
        if not time_str:
            return None
        try:
            # Python 3.11+ handles 'Z' directly; for 3.9/3.10 replace it
            parsed = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
            # rclone reports its own local offset (e.g. -05:00), not always Z.
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone(timezone.utc)
            return parsed.replace(tzinfo=None)
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
            if run and run.phase != "preview":
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

                if run.phase == "preview":
                    self._finish_preview(run, status)
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
                    and not run.is_dry_run
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
                    if run.phase == "preview":
                        self._previewing.add(run.id)
                    logger.info(
                        "Run #%d still alive in rclone, re-tracking", run.id
                    )
                    continue
                except Exception:
                    pass

            # Job lost after restart — mark failed or resume. A run lost during
            # its preview is treated the same way: nothing was deleted yet, and
            # a resume starts over with a fresh preview. Dry runs never resume.
            run.finished_at = datetime.utcnow()
            run.error_message = "Lost track of job after restart"
            run.phase = None

            job = db.session.get(Job, run.job_id)
            if (
                job
                and not run.is_dry_run
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
