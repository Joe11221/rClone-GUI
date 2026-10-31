"""Capturing per-file failures from rclone so they can be inspected later.

rclone's RC API reports an error *count* and the single most recent error, and
its list of completed transfers (the only place per-file errors appear) is
capped at roughly the last 100. Runs can fail thousands of files, so failures
are copied into the database while the run is in progress.
"""

import hashlib
import logging
import posixpath
import re
from datetime import datetime

from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import RunError

logger = logging.getLogger(__name__)

# Stop storing past this many failures per run; the count rclone reports and
# the grouped summary still tell the story, and the table can't grow forever.
MAX_ERRORS_PER_RUN = 50_000

_URL = re.compile(r"https?://\S+")
_IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b")
_PATH = re.compile(r"(?<![\w.])(?:[A-Za-z]:)?(?:[\\/][^\s\\/'\":]+){2,}")
_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")
_LONG_HEX = re.compile(r"\b[0-9a-fA-F]{8,}\b")
_LONG_NUMBER = re.compile(r"\b\d{5,}\b")
_SPACES = re.compile(r"\s+")


def categorize(message, path=None):
    """Reduce an error message to what it has in common with its siblings.

    Thousands of failures usually share a handful of causes ("403 rate limit",
    "permission denied", ...) that differ only in file names, ids and
    addresses. Those are masked so identical causes group together, while short
    numbers (HTTP status codes) are kept because they matter.
    """
    text = (message or "").strip().splitlines()[0] if message else ""
    if path and len(path) >= 3:
        for needle in {path, posixpath.basename(path.replace("\\", "/"))}:
            if len(needle) >= 3:
                text = text.replace(needle, "<file>")
    text = _URL.sub("<url>", text)
    text = _IPV4.sub("<ip>", text)
    text = _QUOTED.sub("'…'", text)
    text = _PATH.sub("<path>", text)
    text = _LONG_HEX.sub("<id>", text)
    text = _LONG_NUMBER.sub("<n>", text)
    text = _SPACES.sub(" ", text).strip()
    return (text or "Unknown error")[:200]


def _fingerprint(path, stamp, message):
    raw = f"{path or ''}\0{stamp or ''}\0{message}"
    return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()


def _parse_time(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(
            tzinfo=None
        )
    except (ValueError, TypeError):
        return None


def record_failures(run, transferred):
    """Store failed entries of an rclone ``core/transferred`` reply.

    Safe to call repeatedly with overlapping data: entries already stored for
    the run are recognised by fingerprint and skipped. Returns how many new
    rows were added.
    """
    candidates = {}
    for entry in transferred or []:
        message = str(entry.get("error") or "").strip()
        if not message:
            continue
        path = entry.get("name") or None
        stamp = entry.get("completed_at") or entry.get("started_at")
        candidates[_fingerprint(path, stamp, message)] = (entry, path, message)
    if not candidates:
        return 0

    seen = {
        fp
        for (fp,) in db.session.query(RunError.fingerprint).filter(
            RunError.run_id == run.id,
            RunError.fingerprint.in_(list(candidates)),
        )
    }
    fresh = [(fp, c) for fp, c in candidates.items() if fp not in seen]
    if not fresh:
        return 0

    room = MAX_ERRORS_PER_RUN - RunError.query.filter_by(run_id=run.id).count()
    if room <= 0:
        return 0

    # Oldest first so ids (the live feed's cursor) follow the order they failed.
    fresh.sort(key=lambda item: item[1][0].get("completed_at") or "")
    for fp, (entry, path, message) in fresh[:room]:
        db.session.add(
            RunError(
                run_id=run.id,
                occurred_at=_parse_time(
                    entry.get("completed_at") or entry.get("started_at")
                )
                or datetime.utcnow(),
                path=path[:1000] if path else None,
                kind="check" if entry.get("checked") else "transfer",
                size=entry.get("size"),
                message=message,
                category=categorize(message, path),
                fingerprint=fp,
            )
        )
    try:
        db.session.commit()
    except IntegrityError:
        # Another writer stored some of these first; they're recorded either way.
        db.session.rollback()
        return 0
    return min(len(fresh), room)
