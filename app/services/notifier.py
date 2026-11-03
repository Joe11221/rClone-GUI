"""Phone notifications (through Apprise) and the heartbeat ping.

The Notifier listens to the EventBus, decides whether an event is worth a
message, builds it inside the app context and sends it off the request path.
Settings live in AppSetting: notify_urls, notify_on, public_url,
heartbeat_url, heartbeat_minutes.
"""

import logging
import time
from urllib.parse import urlsplit

import requests
from sqlalchemy import func

from app.extensions import db
from app.models import Job, RunError
from app.services.app_settings import get_setting

logger = logging.getLogger(__name__)

DEFAULT_NOTIFY_ON = {
    "failed": True,
    "held": True,
    "skipped": True,
    "overdue": True,
    "recovered": True,
    "warning": True,
    "succeeded": False,
}

NOTIFY_TYPES = {
    "failed": "failure",
    "held": "failure",
    "overdue": "failure",
    "skipped": "warning",
    "warning": "warning",
    "recovered": "success",
    "succeeded": "success",
}

DEFAULT_HEARTBEAT_MINUTES = 5


def _service(url):
    """Scheme only: URLs carry tokens and must never be logged."""
    return str(url).split("://", 1)[0] if "://" in str(url) else "unknown"


def apprise_send(urls, title, body, notify_type):
    """Send through Apprise. Returns True if every service accepted it."""
    import apprise

    sender = apprise.Apprise()
    added = 0
    for url in urls:
        if sender.add(url):
            added += 1
        else:
            logger.warning("Ignoring invalid notification URL (%s)", _service(url))
    if not added:
        return False
    return bool(sender.notify(title=title, body=body, notify_type=notify_type))


def _spawn(func_, *args):
    import gevent

    gevent.spawn(func_, *args)


def get_urls():
    urls = get_setting("notify_urls", []) or []
    return [u.strip() for u in urls if isinstance(u, str) and u.strip()]


def get_notify_on():
    chosen = get_setting("notify_on", {}) or {}
    return {k: bool(chosen.get(k, default)) for k, default in DEFAULT_NOTIFY_ON.items()}


def get_public_url(app):
    value = (get_setting("public_url", "") or "").strip()
    if not value:
        value = (app.config.get("PUBLIC_URL") or "").strip()
    return value.rstrip("/")


def get_heartbeat():
    url = (get_setting("heartbeat_url", "") or "").strip()
    try:
        minutes = int(get_setting("heartbeat_minutes", DEFAULT_HEARTBEAT_MINUTES))
    except (TypeError, ValueError):
        minutes = DEFAULT_HEARTBEAT_MINUTES
    return url, max(1, minutes)


def _truncate(text, limit=300):
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


class Notifier:
    def __init__(self, app, sender=apprise_send, spawner=_spawn):
        self.app = app
        self.sender = sender
        self.spawner = spawner
        self._last_ping = None  # monotonic time of the last successful ping

    # --- events ---

    def __call__(self, event, data):
        try:
            self.handle(event, data)
        except Exception:
            logger.exception("Could not process notification for %s", event)

    def handle(self, event, data):
        with self.app.app_context():
            message = self.build(event, data)
            if message is None:
                return
            category, title, body = message
            urls = get_urls()
            if not urls or not get_notify_on().get(category):
                return
            notify_type = NOTIFY_TYPES[category]
        self.spawner(self._send, urls, title, body, notify_type)

    def _send(self, urls, title, body, notify_type):
        try:
            ok = self.sender(urls, title, body, notify_type)
            if not ok:
                logger.warning(
                    "Notification not delivered (%s)",
                    ", ".join(sorted({_service(u) for u in urls})),
                )
        except Exception:
            logger.exception("Sending notification failed")

    # --- message building (needs app context) ---

    def build(self, event, data):
        """Return (category, title, body) or None when nothing should be sent."""
        name = data.get("job_name") or "Unknown job"
        if event == "run_finished":
            status = data.get("status")
            if data.get("will_retry"):
                return None
            if status in ("failed", "interrupted"):
                if data.get("guard") == "delete_limit":
                    reason = data.get("error_message") or (
                        "The delete limit was reached, so the sync was stopped "
                        "before deleting more."
                    )
                    return (
                        "held",
                        f"Backup stopped at delete limit: {name}",
                        self._body(data, reason, run=True),
                    )
                reason = data.get("error_message") or f"Run {status}."
                return (
                    "failed",
                    f"Backup failed: {name}",
                    self._body(data, reason, run=True, causes=True),
                )
            if status == "completed":
                return (
                    "succeeded",
                    f"Backup succeeded: {name}",
                    self._body(data, "Finished without errors.", run=True),
                )
            return None
        if event == "run_held":
            return (
                "held",
                f"Backup held: {name}",
                self._body(data, data.get("reason") or "Held by a safety check.", run=True),
            )
        if event == "run_skipped":
            return (
                "skipped",
                f"Scheduled run skipped: {name}",
                self._body(data, data.get("reason") or "Skipped.", run=True),
            )
        if event == "health_changed":
            new, old = data.get("new_state"), data.get("old_state")
            detail = data.get("detail") or ""
            if new == "overdue":
                return "overdue", f"Backup overdue: {name}", self._body(data, detail)
            if new == "warning":
                return "warning", f"Backup warning: {name}", self._body(data, detail)
            if new == "healthy" and old in ("failing", "overdue", "warning"):
                return (
                    "recovered",
                    f"Backup recovered: {name}",
                    self._body(data, detail),
                )
        return None

    def _body(self, data, reason, run=False, causes=False):
        lines = []
        job = db.session.get(Job, data["job_id"]) if data.get("job_id") else None
        if job is not None:
            lines.append(f"{job.source} → {job.destination}")
        if reason:
            lines.append(_truncate(reason))
        run_id = data.get("run_id")
        if causes and run_id:
            lines.extend(self._top_causes(run_id))
        base = get_public_url(self.app)
        if base:
            lines.append(f"{base}/history/{run_id}" if run and run_id else f"{base}/")
        return "\n".join(lines)

    @staticmethod
    def _top_causes(run_id):
        rows = (
            db.session.query(RunError.category, func.count(RunError.id))
            .filter(RunError.run_id == run_id)
            .group_by(RunError.category)
            .order_by(func.count(RunError.id).desc())
            .limit(3)
            .all()
        )
        return [f"{count:,} × {_truncate(category, 100)}" for category, count in rows]

    # --- test message ---

    def send_test(self, urls):
        return bool(
            self.sender(
                urls,
                "rClone GUI test",
                "Notifications are working.",
                "success",
            )
        )

    # --- heartbeat ---

    def heartbeat_tick(self, now=None):
        """Ping the external monitor if due and rclone is alive."""
        with self.app.app_context():
            url, minutes = get_heartbeat()
        if not url:
            return False
        now = time.monotonic() if now is None else now
        if self._last_ping is not None and now - self._last_ping < minutes * 60:
            return False
        try:
            self.app.config["RCLONE_CLIENT"].noop()
        except Exception as exc:
            logger.warning("Heartbeat skipped: rclone is not responding (%s)", exc)
            return False
        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
        except Exception as exc:
            logger.warning(
                "Heartbeat ping to %s failed: %s",
                urlsplit(url).netloc or "?",
                type(exc).__name__,
            )
            return False
        self._last_ping = now
        return True
