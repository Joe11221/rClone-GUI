"""Plain-English descriptions of 5-field cron expressions."""

import re

_DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
_DAY_ABBR = {n[:3].lower(): i for i, n in enumerate(_DAYS)}
_NUM = re.compile(r"^\d+$")


def _ordinal(n):
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _day_index(token):
    token = token.lower()
    if _NUM.match(token):
        n = int(token)
        return 0 if n == 7 else n if 0 <= n <= 6 else None
    return _DAY_ABBR.get(token[:3]) if token[:3] in _DAY_ABBR else None


def _expand_days(field):
    """Day indexes in the order written, ranges like mon-wed expanded; None if
    any part isn't a plain day or range."""
    days = []
    for part in field.split(","):
        lo, _, hi = part.partition("-")
        start = _day_index(lo)
        if start is None:
            return None
        if not hi:
            days.append(start)
            continue
        # A range may end on 7 or Sunday, which _day_index folds to 0.
        end = _day_index(hi)
        if end is None:
            return None
        end = end or 7
        if start > end:
            return None
        days.extend(d % 7 for d in range(start, end + 1))
    return list(dict.fromkeys(days))


def _describe_days(field):
    """'1-5' -> 'Weekdays'-style text; None when not confidently describable."""
    days = _expand_days(field)
    if days is None:
        return None
    if sorted(days) == [1, 2, 3, 4, 5]:
        return "Weekdays"
    if sorted(days) == [0, 6]:
        return "Weekends"
    names = [_DAYS[d] for d in days]
    if len(names) == 1:
        return f"Every {names[0]}"
    return "Every " + ", ".join(names[:-1]) + " and " + names[-1]


def _step(field, limit):
    """'*/N' -> N when N divides the range sensibly, else None."""
    m = re.match(r"^\*/(\d+)$", field)
    if not m:
        return None
    n = int(m.group(1))
    return n if 1 <= n < limit else None


def describe_cron(expr):
    """Describe a 5-field cron expression, or return it unchanged if unsure."""
    if not isinstance(expr, str):
        return expr or ""
    raw = expr.strip()
    fields = raw.split()
    if len(fields) != 5:
        return raw
    minute, hour, dom, month, dow = fields

    def is_num(f, hi):
        return bool(_NUM.match(f)) and int(f) <= hi

    if month != "*":
        return raw

    # Every N minutes / every minute
    if hour == "*" and dom == "*" and dow == "*":
        if minute == "*":
            return "Every minute"
        n = _step(minute, 60)
        if n:
            return "Every minute" if n == 1 else f"Every {n} minutes"
        if is_num(minute, 59):
            return f"Every hour at :{int(minute):02d}"
        return raw

    # Every N hours at a fixed minute
    n_hours = _step(hour, 24)
    if n_hours and is_num(minute, 59) and dom == "*" and dow == "*":
        when = "on the hour" if int(minute) == 0 else f"at :{int(minute):02d}"
        return f"Every {n_hours} hours, {when}" if n_hours > 1 else f"Every hour {when}"

    if not (is_num(minute, 59) and is_num(hour, 23)):
        return raw
    time = f"{int(hour):02d}:{int(minute):02d}"

    if dom == "*" and dow == "*":
        return f"Every day at {time}"
    if dom == "*":
        days = _describe_days(dow)
        return f"{days} at {time}" if days else raw
    if dow == "*" and is_num(dom, 31) and 1 <= int(dom) <= 31:
        return f"On the {_ordinal(int(dom))} of every month at {time}"
    return raw
