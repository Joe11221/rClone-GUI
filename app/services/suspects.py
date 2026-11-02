"""Spotting directories worth excluding while a run is in progress.

Thousands of few-KB files (node_modules, .git objects, caches, thumbnails) can
dominate a run's time without adding much data. rclone reports completed
transfers and checks only as a rolling window of about 100 entries, so the
collector feeds each poll here and per-directory totals accumulate. Counts are
therefore a sample, and a lower bound on the real number of files.

Being mostly tiny files is not enough. Big files take long enough to be missing
from that sample, so a folder holding a whole drive can look like a pile of tiny
files too. A folder is only suggested when it is recognisably disposable, or
when it is a specific corner of the run with no big file seen in it.
"""

import threading

# Folders that are rebuildable or machine-generated whatever their contents.
KNOWN_NAMES = {
    ".cache", ".git", ".gradle", ".hg", ".m2", ".mypy_cache", ".next", ".npm",
    ".nuxt", ".pnpm-store", ".pytest_cache", ".svn", ".terraform", ".thumbnails",
    ".tox", ".trash", ".venv", ".yarn", "$recycle.bin", "__pycache__",
    "bower_components", "cache", "caches", "code cache", "gpucache",
    "node_modules", "site-packages", "system volume information", "venv",
}
KNOWN_SUFFIXES = ("/mobilesync/backup",)
MIN_KNOWN_FILES = 20

# Any other folder must hold at least MIN_FILES seen files, nearly all of them at
# most TINY_BYTES, none above LARGE_BYTES, and no more than MAX_RUN_SHARE of
# everything seen in the run.
TINY_BYTES = 64 * 1024
LARGE_BYTES = 10 * 1024 * 1024
MIN_FILES = 200
TINY_SHARE = 0.8
MAX_RUN_SHARE = 0.25
# Suggest the subfolder holding this share of a folder's files instead of it.
DOMINANT_SHARE = 0.8
MAX_DIRS_PER_RUN = 50_000
DEFAULT_LIMIT = 8

_GLOB_SPECIAL = set("\\*?[]{}")


def exclude_pattern(directory):
    """rclone filter rule excluding everything under ``directory``.

    The leading slash anchors it to the root of the transfer, so a folder of
    the same name elsewhere in the tree isn't caught.
    """
    escaped = "".join(
        "\\" + ch if ch in _GLOB_SPECIAL else ch for ch in directory.strip("/")
    )
    return f"/{escaped}/**"


def _is_known(path):
    lower = path.lower()
    return lower.rsplit("/", 1)[-1] in KNOWN_NAMES or lower.endswith(KNOWN_SUFFIXES)


def _ancestors(path):
    """Every folder above ``path``, top first. For a file, the folders holding it."""
    parts = path.split("/")
    return ["/".join(parts[:depth]) for depth in range(1, len(parts))]


class _DirStats:
    __slots__ = ("files", "tiny", "bytes", "large")

    def __init__(self):
        self.files = 0
        self.tiny = 0
        self.bytes = 0
        self.large = 0


class _RunState:
    def __init__(self):
        self.dirs = {}
        self.total_files = 0
        self.large_names = set()  # big files already marked on their folders
        self.watermark = ""  # newest completed_at already counted
        self.at_watermark = set()  # names finishing exactly on the watermark
        self.suggestions = []
        self.stale = False


class SuspectTracker:
    def __init__(self):
        self._runs = {}
        self._lock = threading.Lock()

    def observe(self, run_id, transferred):
        """Fold an rclone ``core/transferred`` list into the run's totals."""
        with self._lock:
            state = self._runs.setdefault(run_id, _RunState())
            newest = state.watermark
            newest_names = set(state.at_watermark)
            for entry in transferred or []:
                if entry.get("error"):
                    continue
                name = entry.get("name")
                stamp = entry.get("completed_at") or ""
                if not name or not stamp:
                    continue
                if stamp < state.watermark or (
                    stamp == state.watermark and name in state.at_watermark
                ):
                    continue
                self._count(state, name, _size_of(entry))
                if stamp > newest:
                    newest, newest_names = stamp, {name}
                elif stamp == newest:
                    newest_names.add(name)
            state.watermark, state.at_watermark = newest, newest_names

    def observe_active(self, run_id, transferring):
        """Note big files still in flight: they rule out the folders holding them."""
        with self._lock:
            state = self._runs.setdefault(run_id, _RunState())
            for entry in transferring or []:
                name = entry.get("name")
                if name and _size_of(entry) > LARGE_BYTES:
                    self._mark_large(state, name)

    @staticmethod
    def _dir_stats(state, path):
        stats = state.dirs.get(path)
        if stats is None and len(state.dirs) < MAX_DIRS_PER_RUN:
            stats = state.dirs[path] = _DirStats()
        return stats

    @classmethod
    def _mark_large(cls, state, name):
        if name in state.large_names:
            return
        state.large_names.add(name)
        state.stale = True
        for path in _ancestors(name):
            stats = cls._dir_stats(state, path)
            if stats:
                stats.large += 1

    @classmethod
    def _count(cls, state, name, size):
        state.total_files += 1
        state.stale = True
        for path in _ancestors(name):
            stats = cls._dir_stats(state, path)
            if stats:
                stats.files += 1
                stats.bytes += size
                if size <= TINY_BYTES:
                    stats.tiny += 1
        if size > LARGE_BYTES:
            cls._mark_large(state, name)

    def top(self, run_id, limit=DEFAULT_LIMIT):
        """The folders most worth excluding, known-disposable ones first."""
        with self._lock:
            state = self._runs.get(run_id)
            if not state:
                return []
            if state.stale:
                state.suggestions = _suggest(state)
                state.stale = False
            return state.suggestions[:limit]

    def prune(self, active_run_ids):
        """Drop runs that are no longer active."""
        with self._lock:
            for run_id in set(self._runs) - set(active_run_ids):
                del self._runs[run_id]


def _suggest(state):
    known = {path for path in state.dirs if _is_known(path)}
    known = {p for p in known if not any(a in known for a in _ancestors(p))}
    result = [
        _suggestion(path, state.dirs[path], known=True)
        for path in known
        if state.dirs[path].files >= MIN_KNOWN_FILES
    ]

    # Judge everything else on what is left once known folders are taken out, so
    # a project isn't suggested because of the node_modules inside it.
    rest = {}
    for path, stats in state.dirs.items():
        if path in known or stats.files < MIN_FILES:
            continue
        if any(a in known for a in _ancestors(path)):
            continue
        copy = rest[path] = _DirStats()
        copy.files, copy.tiny = stats.files, stats.tiny
        copy.bytes, copy.large = stats.bytes, stats.large
    for path in known:
        inner = state.dirs[path]
        for ancestor in _ancestors(path):
            if ancestor in rest:
                rest[ancestor].files -= inner.files
                rest[ancestor].tiny -= inner.tiny
                rest[ancestor].bytes -= inner.bytes
                rest[ancestor].large -= inner.large

    children = {}
    for path in rest:
        children.setdefault(path.rpartition("/")[0], []).append(path)

    covered = set()
    for path in sorted(rest, key=lambda p: p.count("/")):
        if any(a in covered for a in _ancestors(path)):
            continue
        if _qualifies(rest[path], state.total_files):
            covered.add(path)
            target = _narrow(path, rest, children)
            result.append(_suggestion(target, rest[target], known=False))

    result.sort(key=lambda item: (not item["known"], -item["files"]))
    return result


def _qualifies(stats, total_files):
    return (
        stats.files >= MIN_FILES
        and stats.tiny / stats.files >= TINY_SHARE
        and stats.large == 0
        and stats.files <= total_files * MAX_RUN_SHARE
    )


def _narrow(path, rest, children):
    """Walk down while one subfolder holds nearly everything."""
    while True:
        largest = max(
            children.get(path, []), key=lambda p: rest[p].files, default=None
        )
        if (
            largest is None
            or rest[largest].files < MIN_FILES
            or rest[largest].files < rest[path].files * DOMINANT_SHARE
        ):
            return path
        path = largest


def _suggestion(path, stats, known):
    return {
        "path": path,
        "files": stats.files,
        "bytes": stats.bytes,
        "avg_bytes": stats.bytes // stats.files if stats.files else 0,
        "pattern": exclude_pattern(path),
        "known": known,
    }


def _size_of(entry):
    size = entry.get("size")
    if not isinstance(size, int) or size < 0:
        size = entry.get("bytes")
    return size if isinstance(size, int) and size >= 0 else 0
