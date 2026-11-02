"""Spotting directories that are mostly tiny files while a run is in progress.

Thousands of few-KB files (node_modules, .git objects, caches, thumbnails) can
dominate a transfer's time without adding much data. rclone reports completed
transfers only as a rolling window of about 100 entries, so the collector feeds
each poll here and per-directory totals accumulate. Counts are therefore a
lower bound when files finish faster than rclone is polled.
"""

import threading

# A directory is suspicious when it holds at least MIN_FILES transferred files
# and nearly all of them are at most TINY_BYTES.
TINY_BYTES = 64 * 1024
MIN_FILES = 40
TINY_SHARE = 0.8
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


class _DirStats:
    __slots__ = ("files", "tiny", "bytes")

    def __init__(self):
        self.files = 0
        self.tiny = 0
        self.bytes = 0


class _RunState:
    def __init__(self):
        self.dirs = {}
        self.watermark = ""  # newest completed_at already counted
        self.at_watermark = set()  # names finishing exactly on the watermark


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
                if entry.get("checked") or entry.get("error"):
                    continue
                name = entry.get("name")
                stamp = entry.get("completed_at") or ""
                if not name or not stamp or "/" not in name:
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

    @staticmethod
    def _count(state, name, size):
        parts = name.split("/")[:-1]
        for depth in range(1, len(parts) + 1):
            path = "/".join(parts[:depth])
            stats = state.dirs.get(path)
            if stats is None:
                if len(state.dirs) >= MAX_DIRS_PER_RUN:
                    continue
                stats = state.dirs[path] = _DirStats()
            stats.files += 1
            stats.bytes += size
            if size <= TINY_BYTES:
                stats.tiny += 1

    def top(self, run_id, limit=DEFAULT_LIMIT):
        """Highest-level directories that look like piles of tiny files."""
        with self._lock:
            state = self._runs.get(run_id)
            if not state:
                return []
            flagged = {
                path: stats
                for path, stats in state.dirs.items()
                if stats.files >= MIN_FILES and stats.tiny / stats.files >= TINY_SHARE
            }
            result = []
            for path, stats in flagged.items():
                # Report the folder to exclude, not each of its subfolders.
                if any(
                    "/".join(path.split("/")[:depth]) in flagged
                    for depth in range(1, path.count("/") + 1)
                ):
                    continue
                result.append(
                    {
                        "path": path,
                        "files": stats.files,
                        "bytes": stats.bytes,
                        "avg_bytes": stats.bytes // stats.files,
                        "pattern": exclude_pattern(path),
                    }
                )
        result.sort(key=lambda item: item["files"], reverse=True)
        return result[:limit]

    def prune(self, active_run_ids):
        """Drop runs that are no longer active."""
        with self._lock:
            for run_id in set(self._runs) - set(active_run_ids):
                del self._runs[run_id]


def _size_of(entry):
    size = entry.get("size")
    if not isinstance(size, int) or size < 0:
        size = entry.get("bytes")
    return size if isinstance(size, int) and size >= 0 else 0
