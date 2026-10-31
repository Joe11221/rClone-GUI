import logging
import os
import subprocess
import threading
import webbrowser

import pystray
import requests
import webview
from PIL import Image

from desktop import autostart, paths, settings, win32

logger = logging.getLogger(__name__)

TRAY_MUTEX = "Local\\rCloneGUI-tray"
SHOW_WINDOW_EVENT = "Local\\rCloneGUI-show-window"
# Keep in sync with desktop.server. Importing it here would monkey-patch this
# process.
EXIT_ALREADY_RUNNING = 3
MAX_START_FAILURES = 3
POLL_SECONDS = 3
QUIT_TIMEOUT_SECONDS = 20
WINDOW_SIZE = (1280, 860)
WINDOW_MIN_SIZE = (800, 600)

# Shown until the server is ready. Dark to match the app and avoid a white flash.
_SPLASH_HTML = """<!DOCTYPE html>
<html><body style="margin:0;height:100vh;display:flex;align-items:center;
justify-content:center;background:#212529;color:#adb5bd;font:16px 'Segoe UI',sans-serif">
Starting rClone GUI&hellip;</body></html>"""


class ServerControl:
    """Talks to the running server, wherever server.json says it is."""

    def __init__(self):
        self._session = requests.Session()
        self._session.trust_env = False

    def _request(self, method, endpoint):
        state = settings.read_server_state()
        if state is None:
            return None
        try:
            response = self._session.request(
                method,
                f"http://127.0.0.1:{state['port']}/__desktop/{endpoint}",
                headers={"X-Control-Token": state["token"]},
                timeout=5,
            )
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError, KeyError):
            return None

    def status(self):
        return self._request("GET", "status")

    def pause(self):
        return self._request("POST", "pause")

    def resume(self):
        return self._request("POST", "resume")

    def shutdown(self):
        return self._request("POST", "shutdown")


def server_url():
    state = settings.read_server_state()
    return f"http://127.0.0.1:{state['port']}/" if state else None


class TrayApp:
    def __init__(self, show_window):
        self.control = ServerControl()
        self.child = None
        self.status = None
        self.stopping = threading.Event()
        self.icon = None
        self.window = None
        self._show_on_start = show_window
        self._open_browser_when_ready = False
        self._loaded_pid = None
        self._close_hint_shown = False

    def run(self):
        """Run until Quit. The window needs the main thread, so the tray gets its own."""
        threading.Thread(target=self._run_tray, daemon=True).start()
        threading.Thread(target=self._wait_for_show_requests, daemon=True).start()
        if not self._run_window():
            self.stopping.wait()

    def _run_window(self):
        """Show the app window until Quit. Returns False if it can't be used."""
        if not win32.webview2_installed():
            logger.warning("WebView2 runtime not found; falling back to the browser")
            self._use_browser_instead()
            return False
        try:
            self.window = webview.create_window(
                paths.APP_NAME,
                html=_SPLASH_HTML,
                width=WINDOW_SIZE[0],
                height=WINDOW_SIZE[1],
                min_size=WINDOW_MIN_SIZE,
                hidden=not self._show_on_start,
                background_color="#212529",
            )
            self.window.events.closing += self._on_window_closing
            webview.start(
                gui="edgechromium",
                private_mode=False,
                storage_path=str(paths.data_dir() / "webview"),
            )
            return True
        except Exception:
            logger.exception("Could not open the app window")
            self.window = None
            self._use_browser_instead()
            return False

    def _use_browser_instead(self):
        self._open_browser_when_ready = self._show_on_start

    def _on_window_closing(self):
        """Closing the window hides it: the app keeps running in the tray."""
        if self.stopping.is_set():
            return True
        # This blocks the UI thread until it returns, and hide() needs that
        # thread, so hiding has to happen after returning.
        threading.Thread(target=self._hide_window, daemon=True).start()
        return False

    def _hide_window(self):
        self.window.hide()
        if not self._close_hint_shown:
            self._close_hint_shown = True
            self.icon.notify(
                "Still running here so scheduled syncs can run. "
                "Use Quit in this icon's menu to exit.",
                paths.APP_NAME,
            )

    def _wait_for_show_requests(self):
        """Show the window when a second launch of the app asks for it."""
        event = win32.create_named_event(SHOW_WINDOW_EVENT)
        while True:
            win32.wait_for_event(event)
            self._on_open()

    def _run_tray(self):
        # The icon must be created on the thread that runs its message loop.
        self.icon = pystray.Icon(
            "rclone-gui",
            Image.open(paths.icon_path()),
            paths.APP_NAME,
            pystray.Menu(
                pystray.MenuItem("Open rClone GUI", self._on_open, default=True),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(
                    "Pause schedules",
                    self._on_toggle_pause,
                    checked=lambda _: bool(self.status and self.status["paused"]),
                ),
                pystray.MenuItem("Configure remotes…", self._on_configure_remotes),
                pystray.MenuItem(
                    "Start at login",
                    self._on_toggle_autostart,
                    checked=lambda _: autostart.is_enabled(),
                    # From source, the command would depend on the working folder.
                    enabled=paths.is_frozen(),
                ),
                pystray.MenuItem("Open logs folder", self._on_open_logs),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Quit", self._on_quit),
            ),
        )
        self.icon.run(setup=self._setup)

    def _setup(self, icon):
        icon.visible = True
        threading.Thread(target=self._monitor, daemon=True).start()

    def _start_server(self):
        logger.info("Starting server")
        self.child = subprocess.Popen(
            paths.app_command("--serve"),
            cwd=paths.resource_dir(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

    def _monitor(self):
        """Keep the server running and the tray icon up to date."""
        failures = 0
        while not self.stopping.is_set():
            status = self.control.status()
            self.status = status
            if status is not None:
                failures = 0
                self._point_window_at_server()
            elif self.child is None or self.child.poll() is not None:
                # EXIT_ALREADY_RUNNING means a server is up but not answering
                # yet, which is no reason to give up on it.
                if self.child and self.child.returncode != EXIT_ALREADY_RUNNING:
                    failures += 1
                if failures >= MAX_START_FAILURES:
                    self._give_up()
                    return
                self._start_server()
            self._refresh()
            self.stopping.wait(POLL_SECONDS)

    def _point_window_at_server(self):
        """Load the app once the server is up, and again if a new one took over."""
        state = settings.read_server_state()
        if state is None or state["pid"] == self._loaded_pid:
            return
        self._loaded_pid = state["pid"]
        url = server_url()
        if self.window is not None:
            self.window.load_url(url)
        elif self._open_browser_when_ready:
            self._open_browser_when_ready = False
            webbrowser.open(url)

    def _refresh(self):
        title = paths.APP_NAME
        if self.status is None:
            title += " (starting)"
        elif self.status["active_runs"]:
            title += f": {self.status['active_runs']} running"
        elif self.status["paused"]:
            title += " (schedules paused)"
        self.icon.title = title
        self.icon.update_menu()

    def _exit_app(self):
        self.stopping.set()
        self.icon.stop()
        if self.window is not None:
            self.window.destroy()

    def _give_up(self):
        logger.error("Server failed to start %d times", MAX_START_FAILURES)
        win32.message_box(
            f"The rClone GUI server failed to start.\n\nSee the logs in:\n{paths.log_dir()}",
            paths.APP_NAME,
            win32.MB_ICONERROR,
        )
        self._exit_app()

    # --- Menu actions ---

    def _on_open(self, *_):
        if self.window is not None:
            self.window.show()
            self.window.restore()
            return
        url = server_url()
        if url:
            webbrowser.open(url)

    def _on_toggle_pause(self, *_):
        action = self.control.resume if self.status and self.status["paused"] else self.control.pause
        status = action()
        if status is not None:
            self.status = status
            self._refresh()

    def _on_configure_remotes(self, *_):
        subprocess.Popen(
            [str(paths.rclone_binary()), "config"],
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )

    def _on_toggle_autostart(self, *_):
        if autostart.is_enabled():
            autostart.disable()
        else:
            autostart.enable()
        self.icon.update_menu()

    def _on_open_logs(self, *_):
        os.startfile(paths.log_dir())

    def _on_quit(self, *_):
        # Off the tray's own thread so the icon stays responsive meanwhile.
        threading.Thread(target=self._quit, daemon=True).start()

    def _quit(self):
        active = self.status["active_runs"] if self.status else 0
        if active:
            answer = win32.message_box(
                f"{active} transfer(s) are running and will be stopped. Quit anyway?",
                paths.APP_NAME,
                win32.MB_YESNO | win32.MB_ICONWARNING,
            )
            if answer != win32.IDYES:
                return

        self.stopping.set()
        self.control.shutdown()
        if self.child is not None:
            try:
                self.child.wait(timeout=QUIT_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                logger.warning("Server did not stop in time; killing it")
                self.child.kill()
        self._exit_app()


def run(show_window=True):
    paths.configure_logging("tray.log")
    mutex = win32.acquire_named_mutex(TRAY_MUTEX)  # noqa: F841 - held until exit
    if mutex is None:
        # Already running: bring its window up instead of starting another.
        if show_window:
            win32.signal_named_event(SHOW_WINDOW_EVENT)
        return 0
    TrayApp(show_window).run()
    return 0
