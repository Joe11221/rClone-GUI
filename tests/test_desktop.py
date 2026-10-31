import json
import os
import re
import time
from pathlib import Path

import pytest

from desktop import paths, settings


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("RCLONE_GUI_DATA_DIR", str(tmp_path))
    return tmp_path


def test_settings_are_created_once_and_kept(data_dir):
    first = settings.load_settings()
    assert len(first["secret_key"]) == 64
    assert first["port"] == settings.DEFAULT_PORT
    assert settings.load_settings() == first


def test_settings_keep_user_values_and_fill_in_the_rest(data_dir):
    (data_dir / "settings.json").write_text(json.dumps({"port": 9000, "timezone": "UTC"}))
    loaded = settings.load_settings()
    assert (loaded["port"], loaded["timezone"]) == (9000, "UTC")
    assert loaded["secret_key"]


def test_corrupt_settings_are_replaced(data_dir):
    (data_dir / "settings.json").write_text("{not json")
    assert settings.load_settings()["secret_key"]


def test_server_state_round_trip():
    assert settings.read_server_state() is None
    settings.write_server_state({"port": 1, "token": "t"})
    assert settings.read_server_state() == {"port": 1, "token": "t"}
    settings.clear_server_state()
    assert settings.read_server_state() is None
    settings.clear_server_state()  # already gone is fine


def test_build_bundles_assets_the_app_needs():
    spec = (Path(paths.resource_dir()) / "packaging" / "rclone-gui.spec").read_text()
    for needed in ('"../app/templates"', '"../app/static"', '"pystray._win32"'):
        assert needed in spec


def test_installer_mutexes_match_the_app():
    root = Path(paths.resource_dir())
    iss = (root / "packaging" / "rclone-gui.iss").read_text()
    for module in ("server.py", "tray.py"):
        source = (root / "desktop" / module).read_text(encoding="utf-8")
        mutex = re.search(r'_MUTEX = "(.+)"', source).group(1).replace("\\\\", "\\")
        assert mutex in iss


@pytest.mark.skipif(os.name != "nt", reason="uses the Windows registry")
def test_autostart_round_trip():
    from desktop import autostart

    name = "rClone GUI test value"
    autostart.disable(name)
    assert not autostart.is_enabled(name)
    autostart.enable(name)
    try:
        assert autostart.is_enabled(name)
    finally:
        autostart.disable(name)
    assert not autostart.is_enabled(name)


@pytest.mark.skipif(os.name != "nt", reason="uses Win32 mutexes")
def test_named_mutex_is_exclusive_until_released():
    import ctypes

    from desktop import win32

    name = "Local\\rCloneGUI-test-mutex"
    handle = win32.acquire_named_mutex(name)
    assert handle
    assert win32.acquire_named_mutex(name) is None
    ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(handle))
    second = win32.acquire_named_mutex(name)
    assert second
    ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(second))


@pytest.mark.skipif(os.name != "nt", reason="the tray app is Windows-only")
class TestTrayWindow:
    @pytest.fixture()
    def tray_app(self):
        from desktop import tray

        return tray.TrayApp(show_window=True)

    def test_closing_the_window_keeps_the_app_running(self, tray_app, monkeypatch):
        hidden = []
        monkeypatch.setattr(tray_app, "_hide_window", lambda: hidden.append(True))
        assert tray_app._on_window_closing() is False  # False cancels the close
        for _ in range(100):
            if hidden:
                break
            time.sleep(0.01)
        assert hidden

    def test_quitting_lets_the_window_close(self, tray_app):
        tray_app.stopping.set()
        assert tray_app._on_window_closing() is True

    def test_without_webview2_the_browser_opens_once_server_is_ready(
        self, tray_app, monkeypatch
    ):
        from desktop import tray, win32

        opened = []
        monkeypatch.setattr(win32, "webview2_installed", lambda: False)
        monkeypatch.setattr(tray.webbrowser, "open", opened.append)
        settings.write_server_state({"pid": 1, "port": 8765, "token": "t"})

        assert tray_app._run_window() is False
        tray_app._point_window_at_server()
        tray_app._point_window_at_server()
        assert opened == ["http://127.0.0.1:8765/"]

    def test_window_reloads_only_when_a_new_server_takes_over(self, tray_app):
        class FakeWindow:
            urls = []

            def load_url(self, url):
                self.urls.append(url)

        tray_app.window = FakeWindow()
        settings.write_server_state({"pid": 1, "port": 8765, "token": "t"})
        tray_app._point_window_at_server()
        tray_app._point_window_at_server()
        assert len(FakeWindow.urls) == 1
        settings.write_server_state({"pid": 2, "port": 8766, "token": "t"})
        tray_app._point_window_at_server()
        assert FakeWindow.urls[-1] == "http://127.0.0.1:8766/"

    def test_second_launch_signals_instead_of_starting(self, monkeypatch):
        from desktop import tray, win32

        signalled = []
        monkeypatch.setattr(win32, "acquire_named_mutex", lambda name: None)
        monkeypatch.setattr(win32, "signal_named_event", signalled.append)
        assert tray.run(show_window=True) == 0
        assert signalled == [tray.SHOW_WINDOW_EVENT]
        signalled.clear()
        assert tray.run(show_window=False) == 0
        assert signalled == []


@pytest.mark.skipif(os.name != "nt", reason="uses Win32 events")
def test_named_event_signals_across_handles():
    import threading

    from desktop import win32

    name = "Local\\rCloneGUI-test-event"
    handle = win32.create_named_event(name)
    woke = threading.Event()

    def waiter():
        win32.wait_for_event(handle)
        woke.set()

    threading.Thread(target=waiter, daemon=True).start()
    assert not woke.wait(0.1)
    win32.signal_named_event(name)
    assert woke.wait(2)
