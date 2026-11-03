"""Extra rclone options on a job, typed as familiar command-line flags.

``Job.extra_flags`` holds rclone ``_config`` keys (``{"MaxBacklog": 200000}``).
Users know the flag names (``--max-backlog 200000``), so the form takes those
and this module converts both ways. rclone silently ignores ``_config`` keys it
doesn't know, so every flag is checked against the daemon's ``options/info``
before anything is stored: a typo must be an error, not a no-op.
"""

import re

# The job form already has a field for each of these; a second value here would
# silently fight it (extra flags are merged last).
CONTROLLED = {
    "transfers": "Transfers",
    "checkers": "Checkers",
    "retries": "Retries (rclone --retries)",
    "bwlimit": "Bandwidth limits",
    "bwlimit_file": "Bandwidth limits",
    "order_by": "Order by",
    "fast_list": "Fast list",
    "size_only": "File comparison method",
    "checksum": "File comparison method",
    "ignore_existing": "File comparison method",
    "max_delete": "Delete limit",
}

# Filters are held in the Exclude patterns field.
FILTER_FLAGS = {
    "exclude", "exclude_from", "include", "include_from", "filter",
    "filter_from", "files_from", "files_from_raw", "files_from0",
    "min_size", "max_size", "min_age", "max_age", "exclude_if_present",
    "delete_excluded", "ignore_case", "hash_filter",
}

# Would wait for a person, or write progress to a terminal, in a background run.
NOT_FOR_JOBS = {
    "interactive", "ask_password", "auto_confirm", "progress",
    "progress_terminal_title", "no_console", "dry_run",
}

# Value types that fit on one line. rclone parses the string forms of
# Duration and SizeSuffix itself; the others we convert.
SIMPLE_TYPES = {"bool", "int", "int64", "float64", "string", "Duration", "SizeSuffix"}

_SIZE = re.compile(r"^(off|-?\d+(\.\d+)?([bBkKmMgGtTpP]([iI]?[bB]?)?)?)$")
_DURATION = re.compile(r"^(off|-?(\d+(\.\d+)?(ns|us|µs|ms|s|m|h|d|w|M|y)?)+)$")


class OptionsUnavailable(Exception):
    """rclone's option list couldn't be fetched, so flags can't be checked."""


def load_options(client):
    """rclone's main options as {Name: option}, fetched once per client."""
    cached = getattr(client, "_main_options", None)
    if cached is not None:
        return cached
    try:
        info = client.options_info()
        options = {o["Name"]: o for o in info["main"]}
    except Exception as exc:
        raise OptionsUnavailable(str(exc)) from exc
    try:
        client._main_options = options
    except AttributeError:
        pass
    return options


def _flag(name):
    return "--" + name.replace("_", "-")


def _convert(option, text):
    """The typed value for ``text``, or raise ValueError with a short reason."""
    kind = option["Type"]
    if kind == "bool":
        lowered = text.lower()
        if lowered not in ("true", "false"):
            raise ValueError("takes no value, or =true / =false")
        return lowered == "true"
    if text == "":
        raise ValueError("needs a value")
    if kind in ("int", "int64"):
        try:
            return int(text)
        except ValueError:
            raise ValueError("needs a whole number") from None
    if kind == "float64":
        try:
            return float(text)
        except ValueError:
            raise ValueError("needs a number") from None
    if kind == "SizeSuffix":
        if not _SIZE.match(text):
            raise ValueError("needs a size such as 32M, 1G or off")
        return text
    if kind == "Duration":
        if not _DURATION.match(text):
            raise ValueError("needs a duration such as 30s, 5m or 1h30m")
        return text
    return text


def parse(text, options):
    """Parse flags typed one per line.

    Returns (config, errors): ``config`` maps rclone _config keys to typed
    values and ``errors`` is a list of messages that each name the flag; store
    nothing unless it is empty. Blank lines and lines starting with # are
    ignored.
    """
    config = {}
    errors = []
    seen = set()
    for number, raw in enumerate((text or "").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        prefix = f"Line {number}: "
        if not line.startswith("--") or line.startswith("---"):
            errors.append(
                f"{prefix}{line.split()[0]} is not a long flag. "
                "Start each line with -- (for example --buffer-size 32M)."
            )
            continue
        head, equals, value = line[2:].partition("=")
        if not equals:
            head, _, value = line[2:].partition(" ")
        name = head.strip().replace("-", "_")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        shown = _flag(name)

        if not name or not re.fullmatch(r"[a-z0-9_]+", name):
            errors.append(f"{prefix}{line.split()[0]} is not a valid flag name.")
            continue
        if name in CONTROLLED:
            errors.append(
                f"{prefix}{shown} is set by the {CONTROLLED[name]} field above. "
                "Change it there."
            )
            continue
        if name in FILTER_FLAGS:
            errors.append(
                f"{prefix}{shown} is a filter flag. Use Exclude patterns instead."
            )
            continue
        if name in NOT_FOR_JOBS:
            errors.append(f"{prefix}{shown} does not make sense in a background job.")
            continue
        option = options.get(name)
        if option is None:
            errors.append(f"{prefix}{shown} is not an rclone option this app can set.")
            continue
        if option["Type"] not in SIMPLE_TYPES:
            errors.append(
                f"{prefix}{shown} takes a value this form can't carry "
                f"({option['Type']}). Leave it out."
            )
            continue
        if name in seen:
            errors.append(f"{prefix}{shown} appears more than once.")
            continue
        seen.add(name)
        if option["Type"] == "bool" and not value:
            value = "true"
        try:
            config[option["FieldName"]] = _convert(option, value)
        except ValueError as exc:
            errors.append(f"{prefix}{shown} {exc}.")
    return config, errors


def format_flags(config, options=None):
    """Saved _config keys as flag lines, in key order.

    ``options`` may be None when rclone is unreachable; names are then guessed
    from the key (MaxBacklog -> --max-backlog), which is right for most.
    """
    names = {o["FieldName"]: o["Name"] for o in (options or {}).values()}
    lines = []
    for key, value in (config or {}).items():
        name = names.get(key) or re.sub(r"(?<!^)(?=[A-Z])", "_", key).lower()
        flag = _flag(name)
        if value is True:
            lines.append(flag)
        elif value is False:
            lines.append(f"{flag}=false")
        else:
            lines.append(f"{flag} {value}")
    return "\n".join(lines)
