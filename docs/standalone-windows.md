# Standalone Windows app

The Docker image targets homelabs. This is a second way to install rClone GUI:
a per-user Windows program with its own window that needs neither Docker nor
Python, lives in the system tray and keeps running scheduled syncs while the
window is closed.
Windows comes first; Linux follows the same design later (see
[Linux later](#linux-later)).

## Architecture

```
rClone GUI.exe (app window: pywebview + WebView2; tray icon: pystray; no gevent)
  └─ rClone GUI.exe --serve (headless, gevent)
       ├─ gevent.pywsgi serving the Flask app on 127.0.0.1
       ├─ APScheduler and the run monitor
       └─ bundled rclone rcd, authenticated, on a random loopback port
```

- **Two processes.** The window and tray each run a blocking Win32 message loop,
  which would stall every greenlet if they shared a process with gevent.
  Separating them keeps the server on the same concurrency model as Docker and
  means a crash of the window or tray doesn't stop running transfers:
  relaunching attaches to the running server.
- **A native window around the same UI.** The window hosts the Flask app in
  WebView2, so the UI is one codebase shared with Docker. pywebview needs the
  main thread, so the tray icon runs on its own thread and is created there,
  because a pystray icon only receives messages on the thread that created it.
- **Closing the window hides it.** The app stays in the tray so schedules keep
  running, and says so in a notification the first time. Quit in the tray menu
  is what exits.
- **`gevent.pywsgi` instead of gunicorn.** gunicorn doesn't run on Windows.
  Docker keeps using gunicorn.
- **`rcd` is tied to the server** with a Job Object, so it can't outlive a
  crashed server. If `rcd` itself dies, the server restarts it and reuses
  `restore_active_runs()` to resume or fail what was running.
- **One instance per user session.** Named mutexes enforce the single-worker
  rule. A second launch signals a named event, which brings up the existing
  window, and exits.
- **No WebView2, no window.** Windows 11 includes the runtime and Windows 10 gets
  it through updates. If it's missing, the app warns in its log and opens the UI
  in the default browser instead.
- **No Windows service.** A service can't show a tray icon and would run
  outside the user's account, away from their rclone config and mapped drives.

## Tray menu

| Item | What it does |
|------|--------------|
| Open rClone GUI (default) | Shows the app window |
| Pause schedules | Stops cron firing until unticked or the app restarts. Runs skipped while paused are not caught up |
| Configure remotes… | Opens a console running the bundled `rclone config` |
| Start at login | Toggles `HKCU\...\Run` (installed builds only) |
| Open logs folder | Opens the folder with `rclone-gui.log` and `rclone.log` |
| Quit | Warns if transfers are running, then stops everything |

## Files on disk

- Program: `%LOCALAPPDATA%\Programs\rClone GUI` (no admin rights needed)
- Data: `%LOCALAPPDATA%\rClone GUI`, holding the database, `settings.json`
  (secret key, port, optional timezone), `server.json` (current port and
  control token), `webview\` (the window's browser profile) and `logs\`
- Remotes: rclone's normal config, `%APPDATA%\rclone\rclone.conf`, so existing
  remotes work unchanged

## Changes shared with Docker

| Change | Why |
|--------|-----|
| Reject cross-site POSTs (`app/security.py`) and set `SameSite=Lax` | Any website could otherwise drive the app through the user's browser |
| Optional `ALLOWED_HOSTS` (always set on desktop) | Blocks DNS rebinding |
| `misfire_grace_time` on schedules | APScheduler's 1-second default skips any run that comes due during sleep |
| Refuse to start a job that is already running | Catch-up makes overlaps more likely |
| Windows paths in the directory browser | `C:\...` was treated as a remote called `C` |
| Vendored Bootstrap | The UI must work offline |

## Desktop-only behaviour

- **rclone RC auth.** Random username, password and port on every launch, passed
  through the environment rather than the command line.
- **Catch-up.** `Schedule.catch_up_after` marks the last point the schedule was
  accounted for. At startup a schedule with a cron occurrence after that point
  runs once, however many occurrences were missed. Docker keeps this off, so a
  server restart never starts an unexpected sync.
- **No sleep during transfers.** `SetThreadExecutionState` holds the machine
  awake while a run is active. The display may still turn off.

## Limitations

- A tray app can't wake a sleeping or powered-off PC. It catches up afterwards.
- Schedules run only while the user is logged in.
- Quitting mid-transfer stops it. Jobs with auto-resume restart on next launch;
  others are marked failed.
- The window has no right-click menu, because pywebview ties it to debug mode.
  Ctrl+C and Ctrl+V still work in text fields.
- The web UI has no login, and the local server behind the window is still
  reachable from a browser. Other accounts on the same PC can reach its loopback
  port, so don't use it on a PC shared with people you don't trust.
- rclone configs encrypted with a password aren't supported yet. `rcd` runs
  without a console, so it can't ask for the password.
- The installer is unsigned, so SmartScreen warns on first run until a
  code-signing certificate is added.

## Build

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\pip install -r requirements-desktop.txt
.\packaging\build.ps1
```

This downloads rclone and checks its SHA-256 against the value pinned in
`packaging/fetch_rclone.py`, then writes `dist\rClone GUI\`. If Inno Setup 6
is installed it also writes `dist\rClone-GUI-Setup.exe`. Pushing a `v*` tag runs
the same steps in `.github/workflows/release.yml`, which attaches the installer
to a draft GitHub release.

For development, `python -m desktop` runs the tray from source and
`python -m desktop --serve` runs only the server. Both need `rclone.exe` in
`packaging\bin\` or on `PATH`.

## Linux later

The server process and the shared changes are portable. Linux needs:

- `win32.py` equivalents: `PR_SET_PDEATHSIG`, a lock file and `systemd-inhibit`
- `~/.config/autostart` instead of the registry, and XDG data paths
- A headless fallback, since stock GNOME has no tray. `pystray` needs
  AppIndicator and PyGObject from the system, which are the hardest part to
  bundle. If that fails, switch the tray process to PySide6
- An AppImage or tarball instead of the installer
