import logging
import os
import secrets
import socket
import subprocess
import time

import requests

from desktop import win32

logger = logging.getLogger(__name__)


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class RcloneDaemon:
    """Runs ``rclone rcd`` on loopback and keeps it tied to this process.

    Browsers can reach loopback, so unlike the Docker setup the RC API must
    have credentials here, or any website could call it.
    """

    def __init__(self, binary, log_path):
        self.binary = binary
        self.log_path = log_path
        self.user = secrets.token_urlsafe(16)
        self.password = secrets.token_urlsafe(32)
        self.port = _free_port()
        self.process = None
        self._log_mode = "wb"
        self._job = win32.create_kill_on_close_job()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}"

    def _post(self, endpoint, timeout):
        session = requests.Session()
        session.trust_env = False
        return session.post(
            f"{self.url}/{endpoint}",
            json={},
            auth=(self.user, self.password),
            timeout=timeout,
        )

    def start(self, timeout=30):
        # Credentials go through the environment so they aren't visible in the
        # process list.
        env = {
            **os.environ,
            "RCLONE_RC_USER": self.user,
            "RCLONE_RC_PASS": self.password,
        }
        with open(self.log_path, self._log_mode) as log:
            self.process = subprocess.Popen(
                [
                    str(self.binary),
                    "rcd",
                    f"--rc-addr=127.0.0.1:{self.port}",
                    # Fail rather than hang on an encrypted config: there is no
                    # console to ask for its password.
                    "--ask-password=false",
                    # The default of 60s can expire a finished job before the
                    # monitor sees it if the machine sleeps in between.
                    "--rc-job-expire-duration=1h",
                ],
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        self._log_mode = "ab"
        win32.assign_to_job(self._job, self.process.pid)

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(
                    f"rclone exited with code {self.process.returncode}; "
                    f"see {self.log_path}"
                )
            try:
                self._post("rc/noop", timeout=2).raise_for_status()
                logger.info("rclone rcd is ready on port %d", self.port)
                return
            except requests.RequestException:
                time.sleep(0.2)
        raise RuntimeError(f"rclone did not become ready; see {self.log_path}")

    def stop(self):
        if self.process is None or self.process.poll() is not None:
            return
        try:
            self._post("core/quit", timeout=3)
            self.process.wait(timeout=10)
        except (requests.RequestException, subprocess.TimeoutExpired):
            self.process.kill()
            self.process.wait(timeout=10)
