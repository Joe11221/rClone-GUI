"""Build APScheduler triggers from standard 5-field cron expressions.

APScheduler's ``CronTrigger.from_crontab`` counts numeric weekdays from Monday
(0 = Monday), while cron counts from Sunday (0 or 7 = Sunday). Passed
through as-is, "0 3 * * 0" fires on Mondays. Every trigger goes through here
so the weekday field is rewritten as day names first.
"""

from apscheduler.triggers.cron import CronTrigger

_NAMES = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"]


def _day(token):
    token = token.lower()
    if token.isdigit():
        n = int(token)
        return 0 if n == 7 else n if 0 <= n <= 6 else None
    return _NAMES.index(token[:3]) if token[:3] in _NAMES else None


def _days(part):
    """Cron day numbers (Sunday = 0) for one comma part, or None if unparsable."""
    body, _, step = part.partition("/")
    if step and not step.isdigit():
        return None
    step = int(step) if step else 1
    if step < 1:
        return None
    if body in ("*", "?"):
        start, end = 0, 6
    else:
        lo, _, hi = body.partition("-")
        start = _day(lo)
        # A range may end on 7 (Sunday); keep it as 7 so 5-7 means Fri to Sun.
        end = (7 if hi == "7" else _day(hi)) if hi else (6 if step > 1 else start)
        if start is None or end is None:
            return None
        if end < start:  # sat-mon wraps past Sunday
            end += 7
    return {d % 7 for d in range(start, end + 1, step)}


def crontab_for_apscheduler(expr):
    """``expr`` with its weekday field spelled as names; unchanged if it can't
    be read, so APScheduler still reports the error."""
    fields = expr.split()
    if len(fields) != 5 or fields[4] in ("*", "?"):
        return expr
    days = set()
    for part in fields[4].split(","):
        found = _days(part)
        if found is None:
            return expr
        days |= found
    fields[4] = ",".join(_NAMES[d] for d in sorted(days))
    return " ".join(fields)


def cron_trigger(expr, timezone=None):
    """``CronTrigger.from_crontab`` with cron's weekday numbering."""
    return CronTrigger.from_crontab(crontab_for_apscheduler(expr), timezone=timezone)
