"""Bandwidth limits: weekly rules, and how they reach rclone.

rclone has one speed limiter, shared by every transfer in its process. It can
be changed while running (``core/bwlimit``), but a per-call
``_config: {"BwLimit": ...}`` is accepted and then ignored, so a limit cannot
be attached to a single job or remote. Instead the rules live here and
:class:`BandwidthController` works out which of them apply to the runs in
progress at this moment, then sets that one limiter. It re-checks whenever a run
starts or stops and every few seconds, so windows open and close on time.

Rules only ever lower the speed. When several apply, the lowest wins; and since
the limiter is shared, jobs that run together share the lowest limit among them.
"""

import json
import logging
import os
import re
import threading
from collections import namedtuple
from datetime import datetime

from app.extensions import db
from app.models import BandwidthRule, Job, RunHistory

logger = logging.getLogger(__name__)

DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
# Bytes per second in one of each unit.
UNITS = {"KiB": 1024, "MiB": 1024**2, "GiB": 1024**3, "Mbit": 125_000}
UNIT_LABELS = {
    "KiB": "KiB/s",
    "MiB": "MiB/s",
    "GiB": "GiB/s",
    "Mbit": "Mbit/s",
}
SCOPES = ("everything", "all_remotes", "remotes")
MIN_BYTES_PER_SECOND = 1024
MAX_VALUE = 1_000_000
MAX_RULES = 50
MINUTES_PER_DAY = 24 * 60

Limit = namedtuple("Limit", "bytes_per_second rule")

_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:([\\/]|$)")


# --- Evaluating rules ---


def bytes_per_second(value, unit):
    return int(value * UNITS[unit])


def window_active(days, start, end, now):
    """True if ``now`` (local time) is inside the weekly window.

    ``days`` are the weekdays the window starts on. A window that ends before
    it starts runs past midnight, into the day after the one it started on.
    """
    minute = now.hour * 60 + now.minute
    today = now.weekday()
    if start == end:
        return today in days
    if start < end:
        return today in days and start <= minute < end
    return (today in days and minute >= start) or (
        (today - 1) % 7 in days and minute < end
    )


def rule_active(rule, now):
    return rule.enabled and window_active(
        rule.day_list(), rule.start_minute, rule.end_minute, now
    )


def fs_remote(fs, windows=os.name == "nt"):
    """The remote a path points at: None for local disk, "" for an inline
    backend such as ``:s3,env_auth:bucket``, else the remote's name."""
    fs = (fs or "").strip()
    if windows and _WINDOWS_DRIVE.match(fs):
        return None
    head, colon, _ = fs.partition(":")
    if not colon or "/" in head or "\\" in head:
        return None
    return head.split(",", 1)[0]


def job_remotes(job, windows=os.name == "nt"):
    """(names of the remotes a job touches, whether it touches any remote)."""
    found = [
        fs_remote(fs, windows) for fs in (job.source, job.destination)
    ]
    remotes = [name for name in found if name is not None]
    return {name for name in remotes if name}, bool(remotes)


def _global_rule_matches(rule, names, has_remote):
    if rule.scope == "all_remotes":
        return has_remote
    if rule.scope == "remotes":
        return bool(names & set(rule.remote_list()))
    return True


def effective_limit(rules, jobs, now):
    """The lowest limit that applies right now, as a :class:`Limit` or None.

    ``rules`` is every rule, global and per job; ``jobs`` the jobs running (or
    about to). Global rules for everything count even when nothing is running,
    so a job that starts is already limited.
    """
    active = [rule for rule in rules if rule_active(rule, now)]
    best = None

    def consider(rule):
        nonlocal best
        speed = bytes_per_second(rule.limit_value, rule.limit_unit)
        if best is None or speed < best.bytes_per_second:
            best = Limit(speed, rule)

    for rule in active:
        if rule.job_id is None and rule.scope == "everything":
            consider(rule)
    for job in jobs:
        names, has_remote = job_remotes(job)
        for rule in active:
            if rule.job_id is None:
                if _global_rule_matches(rule, names, has_remote):
                    consider(rule)
            elif rule.job_id == job.id:
                consider(rule)
    return best


# --- Wording ---


def format_days(days):
    days = sorted(set(days))
    if len(days) == 7:
        return "Every day"
    if days == [0, 1, 2, 3, 4]:
        return "Weekdays"
    if days == [5, 6]:
        return "Weekends"
    runs = []  # consecutive days, e.g. [[0, 1, 2], [4]]
    for day in days:
        if runs and day == runs[-1][-1] + 1:
            runs[-1].append(day)
        else:
            runs.append([day])
    return ", ".join(
        DAY_NAMES[run[0]] if len(run) == 1
        else ", ".join(DAY_NAMES[d] for d in run) if len(run) == 2
        else f"{DAY_NAMES[run[0]]}–{DAY_NAMES[run[-1]]}"
        for run in runs
    )


def format_clock(minute):
    return f"{minute // 60:02d}:{minute % 60:02d}"


def format_limit(value, unit):
    return f"{value:g} {UNIT_LABELS[unit]}"


def describe_rule(rule):
    parts = [format_days(rule.day_list())]
    if rule.start_minute == rule.end_minute:
        parts.append("all day")
    else:
        parts.append(
            f"{format_clock(rule.start_minute)}–{format_clock(rule.end_minute)}"
        )
    parts.append(format_limit(rule.limit_value, rule.limit_unit))
    return " · ".join(parts)


# --- Reading and writing rules ---


def rule_to_dict(rule):
    """A rule as the editor on the page sees it."""
    return {
        "enabled": bool(rule.enabled),
        "days": rule.day_list(),
        "all_day": rule.start_minute == rule.end_minute,
        "start": format_clock(rule.start_minute),
        "end": format_clock(rule.end_minute),
        "value": rule.limit_value,
        "unit": rule.limit_unit,
        "scope": rule.scope,
        "remotes": rule.remote_list(),
    }


def _parse_clock(text, label):
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", str(text or "").strip())
    if not match or int(match[1]) > 23 or int(match[2]) > 59:
        raise ValueError(f"{label}: enter times as HH:MM.")
    return int(match[1]) * 60 + int(match[2])


def _rule_values(days, start, end, value, unit, enabled=True,
                 scope="everything", remotes=()):
    """Column values for a rule, in the form ``BandwidthRule(**values)`` takes."""
    return {
        "enabled": enabled,
        "days": ",".join(str(d) for d in sorted(days)),
        "start_minute": start,
        "end_minute": end,
        "limit_value": value,
        "limit_unit": unit,
        "scope": scope,
        "remotes": json.dumps(list(remotes)),
    }


def parse_rules(raw, *, allow_remote_scope):
    """Validate the editor's JSON and return rule values, or raise ValueError
    with a message fit to show the user."""
    try:
        items = json.loads(raw)
    except (TypeError, ValueError):
        raise ValueError("The bandwidth rules could not be read.") from None
    if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
        raise ValueError("The bandwidth rules could not be read.")
    if len(items) > MAX_RULES:
        raise ValueError(f"Use at most {MAX_RULES} bandwidth rules.")

    rules = []
    for number, item in enumerate(items, 1):
        label = f"Bandwidth rule {number}"
        try:
            days = {int(d) for d in item.get("days") or []}
        except (TypeError, ValueError):
            days = set()
        if not days or not days <= set(range(7)):
            raise ValueError(f"{label}: pick at least one day.")

        if item.get("all_day"):
            start = end = 0
        else:
            start = _parse_clock(item.get("start"), label)
            end = _parse_clock(item.get("end"), label)
            if start == end:
                raise ValueError(
                    f"{label}: the start and end time are the same. "
                    "Tick “All day” to cover the whole day."
                )

        unit = item.get("unit")
        try:
            value = float(item.get("value"))
        except (TypeError, ValueError):
            value = 0.0
        if unit not in UNITS or not 0 < value <= MAX_VALUE:
            raise ValueError(f"{label}: enter a speed limit above zero.")
        if bytes_per_second(value, unit) < MIN_BYTES_PER_SECOND:
            raise ValueError(f"{label}: the limit can't be below 1 KiB/s.")

        scope, remotes = "everything", []
        if allow_remote_scope:
            scope = item.get("scope", "everything")
            if scope not in SCOPES:
                raise ValueError(f"{label}: choose what the limit applies to.")
            if scope == "remotes":
                chosen = item.get("remotes")
                remotes = list(dict.fromkeys(
                    str(r).strip() for r in chosen or [] if str(r).strip()
                ))
                if not remotes:
                    raise ValueError(f"{label}: choose at least one remote.")
        rules.append(_rule_values(
            days, start, end, value, unit,
            enabled=bool(item.get("enabled", True)),
            scope=scope, remotes=remotes,
        ))
    return rules


def replace_rules(rules, *, job=None):
    """Replace a job's rules (or the global ones) with already-validated values.
    The caller commits."""
    if job is not None:
        job.bandwidth_rules = [BandwidthRule(**values) for values in rules]
        return
    BandwidthRule.query.filter_by(job_id=None).delete()
    db.session.add_all(BandwidthRule(**values) for values in rules)


# --- Old free-text limits ---

_LEGACY_RATE = re.compile(r"(\d+(?:\.\d+)?)([bBkKMG]?)")
_LEGACY_ENTRY = re.compile(r"(?:(\w+)-)?(\d{1,2}):(\d{2}),(\S+)")
_LEGACY_UNITS = {"": ("KiB", 1), "k": ("KiB", 1), "K": ("KiB", 1),
                 "M": ("MiB", 1), "G": ("GiB", 1), "b": ("KiB", 1 / 1024),
                 "B": ("KiB", 1 / 1024)}
_LEGACY_OFF = "off"


def _legacy_rate(text):
    """(value, unit) for rclone rate text like "10M", or "off", or None."""
    if text.lower() == _LEGACY_OFF:
        return _LEGACY_OFF
    match = _LEGACY_RATE.fullmatch(text)
    if not match:
        return None
    unit, factor = _LEGACY_UNITS[match[2]]
    value = round(float(match[1]) * factor, 3)
    if value <= 0 or bytes_per_second(value, unit) < MIN_BYTES_PER_SECOND:
        return None
    return value, unit


def _legacy_day(token):
    if token is None:
        return None
    if token.isdigit() and int(token) <= 6:
        return (int(token) - 1) % 7  # rclone counts Sunday as 0
    names = [name.lower() for name in DAY_NAMES]
    if token[:3].lower() in names:
        return names.index(token[:3].lower())
    raise ValueError(token)


def legacy_rules(spec):
    """Rule values for an old rclone ``--bwlimit`` string such as ``10M`` or
    ``Mon-08:00,5M 23:00,off``, or None if it can't be read."""
    tokens = (spec or "").split()
    if not tokens:
        return []
    if len(tokens) == 1 and "," not in tokens[0]:
        rate = _legacy_rate(tokens[0])
        if rate is None:
            return None
        if rate == _LEGACY_OFF:
            return []
        return [_rule_values(range(7), 0, 0, *rate)]

    # A timetable: each entry holds from its time until the next one starts.
    changes = []
    try:
        for token in tokens:
            match = _LEGACY_ENTRY.fullmatch(token)
            rate = _legacy_rate(match[4]) if match else None
            hour, minute = (int(match[2]), int(match[3])) if match else (99, 0)
            if rate is None or hour > 23 or minute > 59:
                return None
            day = _legacy_day(match[1])
            for d in range(7) if day is None else [day]:
                changes.append((d * MINUTES_PER_DAY + hour * 60 + minute, rate))
    except ValueError:
        return None

    changes.sort(key=lambda change: change[0])
    week = 7 * MINUTES_PER_DAY
    windows = {}  # (start, end, rate) -> days
    for index, (begin, rate) in enumerate(changes):
        if rate == _LEGACY_OFF:
            continue
        stop = (
            changes[index + 1][0] if index + 1 < len(changes)
            else changes[0][0] + week
        )
        pieces = []
        if stop - begin < MINUTES_PER_DAY:
            pieces.append((begin, stop))  # one window, overnight if it wraps
        else:
            point = begin
            while point < stop:  # one piece per calendar day
                day_end = (point // MINUTES_PER_DAY + 1) * MINUTES_PER_DAY
                pieces.append((point, min(stop, day_end)))
                point = pieces[-1][1]
        for first, last in pieces:
            key = (first % MINUTES_PER_DAY, last % MINUTES_PER_DAY, rate)
            windows.setdefault(key, set()).add(first // MINUTES_PER_DAY % 7)
    return [
        _rule_values(days, start, end, *rate)
        for (start, end, rate), days in windows.items()
    ]


def migrate_legacy_limits():
    """Turn each job's old free-text limit into rules, once.

    The old field never worked (see the module docstring), so a value that can't
    be read is left alone and reported rather than guessed at.
    """
    converted = False
    for job in Job.query.filter(Job.bwlimit.isnot(None), Job.bwlimit != "").all():
        rules = legacy_rules(job.bwlimit)
        if rules is None:
            logger.warning(
                "Job '%s' has a bandwidth limit (%r) that can't be converted; "
                "re-create it under Bandwidth Limits.", job.name, job.bwlimit,
            )
            continue
        if not job.bandwidth_rules:
            replace_rules(rules, job=job)
        job.bwlimit = ""
        converted = True
    if converted:
        db.session.commit()


# --- Applying the limit ---


class BandwidthController:
    """Keeps rclone's one speed limiter in step with the rules."""

    def __init__(self, rclone, timezone):
        self.rclone = rclone
        self.timezone = timezone
        self._lock = threading.Lock()
        # Whether we have set a limit rclone might still be holding.
        self._limiting = False
        self._failing = False  # so an rclone outage is logged once, not every tick

    def _running_jobs(self):
        return (
            Job.query.join(RunHistory, RunHistory.job_id == Job.id)
            .filter(RunHistory.status == "running")
            .all()
        )

    def current(self, starting=None):
        """The :class:`Limit` that applies right now, or None."""
        rules = BandwidthRule.query.filter_by(enabled=True).all()
        jobs = self._running_jobs()
        if starting is not None and starting.id not in {j.id for j in jobs}:
            jobs.append(starting)
        return effective_limit(rules, jobs, datetime.now(self.timezone))

    def reconcile(self, starting=None):
        """Set rclone's limit to what the rules say. Safe to call often.

        Pass ``starting`` for a job about to start, so its limit is in place
        before its first byte moves. Failures are logged, never raised: the
        next call tries again.
        """
        with self._lock:
            try:
                self._reconcile(starting)
                self._failing = False
            except Exception as exc:
                db.session.rollback()
                if not self._failing:
                    logger.warning("Could not apply the bandwidth limit: %s", exc)
                self._failing = True

    def _reconcile(self, starting):
        if not self._limiting and not BandwidthRule.query.first():
            return  # never used: leave rclone's own setting alone
        limit = self.current(starting)
        wanted = limit.bytes_per_second if limit else None

        reply = self.rclone.get_bwlimit()
        speed = reply.get("bytesPerSecond", -1)
        actual = speed if speed is not None and speed >= 0 else None
        if actual != wanted:
            # A bare number would mean KiB/s; "b" makes it bytes.
            self.rclone.set_bwlimit(f"{wanted}b" if wanted else "off")
            logger.info(
                "Bandwidth limit is now %s",
                f"{wanted} B/s" if wanted else "off",
            )
        self._limiting = wanted is not None
