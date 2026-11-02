import requests


class RcloneClient:
    """Thin wrapper around the rclone RC HTTP API."""

    def __init__(self, base_url="http://localhost:5572", user=None, password=None):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        if user and password:
            self.session.auth = (user, password)

    def _call(self, endpoint, params=None):
        url = f"{self.base_url}/{endpoint}"
        resp = self.session.post(url, json=params or {})
        resp.raise_for_status()
        return resp.json()

    # --- Sync operations (always async) ---

    def start_sync(self, src_fs, dst_fs, group=None, _config=None, _filter=None):
        params = {"srcFs": src_fs, "dstFs": dst_fs, "_async": True}
        if group:
            params["_group"] = group
        if _config:
            params["_config"] = _config
        if _filter:
            params["_filter"] = _filter
        return self._call("sync/sync", params)

    def start_copy(self, src_fs, dst_fs, group=None, _config=None, _filter=None):
        params = {"srcFs": src_fs, "dstFs": dst_fs, "_async": True}
        if group:
            params["_group"] = group
        if _config:
            params["_config"] = _config
        if _filter:
            params["_filter"] = _filter
        return self._call("sync/copy", params)

    def start_move(self, src_fs, dst_fs, group=None, _config=None, _filter=None):
        params = {
            "srcFs": src_fs,
            "dstFs": dst_fs,
            "_async": True,
            "deleteEmptySrcDirs": True,
        }
        if group:
            params["_group"] = group
        if _config:
            params["_config"] = _config
        if _filter:
            params["_filter"] = _filter
        return self._call("sync/move", params)

    # --- Job management ---

    def job_status(self, jobid):
        return self._call("job/status", {"jobid": jobid})

    def job_list(self):
        return self._call("job/list")

    def job_stop(self, jobid):
        return self._call("job/stop", {"jobid": jobid})

    # --- Stats ---

    def get_stats(self, group=None):
        params = {}
        if group:
            params["group"] = group
        return self._call("core/stats", params)

    def get_transferred(self, group=None):
        """Recently completed transfers (rclone keeps only the last ~100)."""
        params = {}
        if group:
            params["group"] = group
        return self._call("core/transferred", params)

    def set_bwlimit(self, rate):
        return self._call("core/bwlimit", {"rate": rate})

    # --- Config / remotes ---

    def list_remotes(self):
        return self._call("config/listremotes")

    def get_remote_config(self, name):
        return self._call("config/get", {"name": name})

    def list_providers(self):
        """Every storage type rclone supports, with its configuration options."""
        return self._call("config/providers")

    def create_remote(self, name, remote_type, parameters):
        """Start creating a remote.

        Non-interactive: if rclone has a question (OAuth, shared drives, ...) it
        comes back as a state to answer with :meth:`update_remote`.
        """
        return self._call(
            "config/create",
            {
                "name": name,
                "type": remote_type,
                "parameters": parameters,
                "opt": {"obscure": True, "nonInteractive": True},
            },
        )

    def update_remote(self, name, state, result):
        """Answer the question rclone asked in ``state``."""
        return self._call(
            "config/update",
            {
                "name": name,
                "parameters": {},
                "opt": {
                    "nonInteractive": True,
                    "continue": True,
                    "state": state,
                    "result": result,
                },
            },
        )

    def delete_remote(self, name):
        return self._call("config/delete", {"name": name})

    # --- File operations ---

    def list_files(self, fs, remote="", recurse=False):
        return self._call(
            "operations/list",
            {"fs": fs, "remote": remote, "opt": {"recurse": recurse}},
        )

    def list_dirs(self, fs, remote=""):
        return self._call(
            "operations/list",
            {"fs": fs, "remote": remote, "opt": {"recurse": False, "dirsOnly": True}},
        )
