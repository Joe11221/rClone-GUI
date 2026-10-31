from gevent import monkey

monkey.patch_all()

import hmac  # noqa: E402
import logging  # noqa: E402
import os  # noqa: E402
import secrets  # noqa: E402
import socket  # noqa: E402

import gevent  # noqa: E402
from flask import Blueprint, abort, current_app, jsonify, request  # noqa: E402
from gevent.event import Event  # noqa: E402
from gevent.pywsgi import WSGIServer  # noqa: E402

from app import create_app  # noqa: E402
from app.config import Config  # noqa: E402
from desktop import paths, settings, win32  # noqa: E402
from desktop.rclone_daemon import RcloneDaemon  # noqa: E402

logger = logging.getLogger(__name__)

SERVER_MUTEX = "Local\\rCloneGUI-server"
EXIT_ALREADY_RUNNING = 3
WATCH_INTERVAL_SECONDS = 5


def make_config(user_settings, rclone, port):
    """Build the app config for this launch."""
    db_path = (paths.data_dir() / "rclone_gui.db").as_posix()

    class DesktopConfig(Config):
        SECRET_KEY = user_settings["secret_key"]
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{db_path}"
        RCLONE_RC_URL = rclone.url
        RCLONE_RC_USER = rclone.user
        RCLONE_RC_PASS = rclone.password
        TIMEZONE = user_settings.get("timezone") or settings.system_timezone()
        ALLOWED_HOSTS = [f"127.0.0.1:{port}", f"localhost:{port}"]
        # A PC that was off for days should still run its backup when it's back.
        SCHEDULE_MISFIRE_GRACE_SECONDS = None
        SCHEDULE_CATCH_UP = True
        DESKTOP_MODE = True

    return DesktopConfig


def _bind_listener(preferred_port):
    """Bind the preferred port, or any free one if something else has it."""
    for port in (preferred_port, 0):
        listener = socket.socket()
        # Without this, Windows would let another process bind over our port.
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            listener.bind(("127.0.0.1", port))
        except OSError:
            listener.close()
            logger.warning("Port %d is unavailable", port)
            continue
        listener.listen(128)
        return listener
    raise RuntimeError("Could not bind a local port")


def create_control_blueprint(token, stop_event):
    """Routes the tray uses to talk to this server, guarded by a launch token."""
    control = Blueprint("desktop_control", __name__, url_prefix="/__desktop")

    @control.before_request
    def _require_token():
        supplied = request.headers.get("X-Control-Token", "")
        if not hmac.compare_digest(supplied, token):
            abort(403)

    @control.route("/status")
    def status():
        return jsonify(
            active_runs=len(current_app.config["JOB_RUNNER"]._active_runs),
            paused=current_app.config["SCHEDULER_SERVICE"].paused,
        )

    @control.route("/pause", methods=["POST"])
    def pause():
        current_app.config["SCHEDULER_SERVICE"].pause()
        return status()

    @control.route("/resume", methods=["POST"])
    def resume():
        current_app.config["SCHEDULER_SERVICE"].resume()
        return status()

    @control.route("/shutdown", methods=["POST"])
    def shutdown():
        # Let this response go out before the server stops.
        gevent.spawn_later(0.2, stop_event.set)
        return jsonify(stopping=True)

    return control


def _watch(app, rclone, stop_event):
    """Keep the machine awake during transfers and rclone itself alive."""
    runner = app.config["JOB_RUNNER"]
    while not stop_event.wait(WATCH_INTERVAL_SECONDS):
        win32.keep_system_awake(bool(runner._active_runs))
        if rclone.process.poll() is None:
            continue
        logger.warning(
            "rclone exited with code %s; restarting", rclone.process.returncode
        )
        try:
            rclone.start()
            # The old jobs died with the process. This resumes or fails them.
            with app.app_context():
                runner._active_runs.clear()
                runner.restore_active_runs()
        except Exception:
            logger.exception("Failed to restart rclone")


def run():
    paths.configure_logging("rclone-gui.log")
    mutex = win32.acquire_named_mutex(SERVER_MUTEX)
    if mutex is None:
        logger.info("Server is already running")
        return EXIT_ALREADY_RUNNING

    user_settings = settings.load_settings()
    listener = _bind_listener(user_settings["port"])
    port = listener.getsockname()[1]

    rclone = RcloneDaemon(paths.rclone_binary(), paths.log_dir() / "rclone.log")
    try:
        rclone.start()
        app = create_app(make_config(user_settings, rclone, port))
        # Loopback only, and the system proxy must not see the RC credentials.
        app.config["RCLONE_CLIENT"].session.trust_env = False

        token = secrets.token_urlsafe(32)
        stop_event = Event()
        app.register_blueprint(create_control_blueprint(token, stop_event))

        server = WSGIServer(listener, app, log=logger, error_log=logger)
        server.start()
        settings.write_server_state({"pid": os.getpid(), "port": port, "token": token})
        logger.info("Serving on http://127.0.0.1:%d", port)

        gevent.spawn(_watch, app, rclone, stop_event)
        stop_event.wait()
        server.stop(timeout=2)
    except Exception:
        logger.exception("Server failed")
        return 1
    finally:
        settings.clear_server_state()
        rclone.stop()
    return 0
