# rClone GUI

## Why this exists

rclone is an excellent tool for moving data to and from cloud storage, but
running it on a schedule means editing command lines and cron files by hand.
I wanted a web interface for it that I could deploy on my own homelab server,
and I couldn't find a good option for that.

So I built this one with functionality first. The goal is that anything rclone
can do for a sync, copy, or move, you can control from the job form, without
dropping to a terminal. That means exclude patterns, extra flags, per-job
bandwidth, file comparison method, and transfer ordering are all exposed, with
sensible defaults so simple jobs stay simple.

Backups are rarely uniform. A single job can hold a few huge files and many
small ones, and the order rclone transfers them in matters: either the big
files finish last, or the small ones sit idle behind them. That's why the ordering options (see [Why size + mixed
ordering?](#why-size--mixed-ordering)) are a first-class part of the job form,
and why the dashboard and failure list are built to stay readable with
thousands of files in a run.

It runs in Docker on a home server, or as a Windows app for a desktop PC. Both
use the same web UI.

> [!CAUTION]
> Anyone who can log in can browse the mounted filesystem, create jobs, and run
> `sync`/`move` operations against your data (which is mounted **read-write**).
> Login is on by default: the first visit asks you to choose a password.
>
> - By default the port is published on `127.0.0.1` only.
> - Only set `BIND_ADDRESS=0.0.0.0` on a network you trust.
> - **Never expose it to the internet directly.** For remote access, use a VPN
>   (WireGuard, Tailscale) or a reverse proxy that enforces authentication.
>   See [Security notes](#security-notes).
> - `AUTH_MODE=none` turns login off. It is an explicit opt-out, not a default.

A Flask web GUI for managing [rclone](https://rclone.org) backup jobs, on a home
server with Docker or on a Windows PC as a [tray app](#windows-app). Create sync/copy/move jobs, schedule them with cron, watch live
transfer progress, and browse run history — from a browser, or from the Windows
app's own window. It also checks that each backup runs on time and succeeds,
and alerts you when one doesn't.

This is a standalone app (not the upstream rclone web GUI). It never shells out
to rclone; every operation goes through the **rclone RC HTTP API**.

![Dashboard](docs/screenshots/dashboard.png)

<table>
  <tr>
    <td><img src="docs/screenshots/running.png" alt="Running operations"></td>
    <td><img src="docs/screenshots/job-form.png" alt="Create job form"></td>
  </tr>
  <tr>
    <td><img src="docs/screenshots/schedules.png" alt="Schedules"></td>
    <td><img src="docs/screenshots/history.png" alt="Run history"></td>
  </tr>
</table>

*(Screenshots use demo data served by a stub rclone.)*

## Features

- **Jobs** — CRUD for sync/copy/move jobs with source/destination (local
  paths or remotes), exclude patterns, extra flags, per-job bandwidth rules, and
  `--order-by` composition (field `size`/`name`/`modtime`, direction
  `asc`/`desc`/`mixed` with a window %). The default, **size + mixed**, keeps
  small and large files moving at the same time — see
  [Why size + mixed ordering?](#why-size--mixed-ordering).
- **File comparison method** — per job: default (modtime + size),
  `--size-only`, `--checksum`, or `--ignore-existing`.
- **Directory browser** — the Browse button on the job form opens a modal
  that walks local paths and remotes via `operations/list`.
- **Schedules** — per-job cron schedules (APScheduler), enable/disable,
  interpreted in your configured `TIMEZONE`. A run that comes due while the
  server is down starts if it's less than an hour late (Docker); the Windows
  app instead starts one catch-up run for everything missed.
- **Live dashboard** — SSE-driven progress bars, speed, bytes, file counts
  (new vs unchanged), and ETA.
- **Failures** — per-file errors from rclone are saved while a run is still
  going, not just the error count. The Running page shows a live feed of the
  newest failures; the run's history page lists them all, grouped by cause,
  with a filter (by file or message), paging, and a CSV export. Up to 50,000
  failures are kept per run.
- **Backup health** — the dashboard opens with a summary of every job's state:
  `healthy`, `failing`, `overdue`, `warning` (its last run deleted an unusual
  number of files), or `pending`. Each job also shows its last success, next
  run, failures in a row, and the 7-day success rate. Deadlines come from the
  cron schedule, or from a per-job "Must succeed at least every N hours".
  `GET /api/health` returns the same data as JSON.
  - `failing`: a run has failed or been held since the last success.
  - `overdue`: no success by the deadline. For a scheduled job, that is the
    first cron time after the last success plus 60 minutes of grace. A running
    job is not overdue against its schedule.
  - `warning`: the last successful run deleted more than 100 files and more
    than ten times the usual number (the median of the ten runs before it).
  - `healthy` shows the time of the last success. `pending` means the job has
    no successful run yet and no failure to report.
- **Safety checks** — before a sync starts, its source is read. If the source
  can't be read or is empty, the run is `held` and rclone is not started. Each
  sync also has a delete limit (default 1000 files). A held or delete-limited run
  has a **Run anyway** button. See [Keeping backups safe](#keeping-backups-safe).
- **Skipped runs** — if a scheduled run comes due while the previous run of the
  same job is still going, it is recorded as `skipped` instead of disappearing.
- **Notifications** — alerts through [Apprise](https://github.com/caronc/apprise)
  to ntfy, Discord, Telegram, email, Gotify and 100+ more. You choose which events
  notify on the Settings page. Failure messages list the top causes, and a
  "Send test notification" button checks the setup.
- **Heartbeat** — pings a dead-man's-switch URL (Healthchecks.io, an Uptime Kuma
  push monitor) every N minutes while the app and rclone are working. If either
  one dies, the pings stop and your monitor alerts you.
- **Login** — a single password by default. Behind a reverse proxy (Authelia,
  Authentik, oauth2-proxy), the app can trust the proxy's login instead. See
  [Security notes](#security-notes).
- **History** — every run is recorded with final stats and status
  (`completed`/`failed`/`stopped`/`interrupted`/`held`/`skipped`). Each run also
  records its deletes, checks, and the exact settings it used.
- **Auto-resume** — per-job toggle. Orphaned `running` records are
  re-dispatched on container restart; failed runs retry after a delay up to
  `max_retries` (default 3). Each retry is a new history record linked via
  `retry_of_run_id`. User-stopped runs are never auto-resumed. A manual
  Resume button is available on failed/interrupted runs.
- **Settings** — configured remotes list, guided setup for all of rclone's
  storage types (with browser sign-in for Google Drive, OneDrive, Dropbox, ...)
  and global bandwidth limits.
- **Bandwidth limits** — any number of weekly rules, each with days, a time
  window (overnight windows work), and a speed in KiB/s, MiB/s, GiB/s or
  Mbit/s. Set them per job (job edit page) or globally (Settings), where a rule
  can apply to everything, to all remotes — including remotes added later — or
  to specific remotes. A job counts as using a remote if its source or
  destination is on it. Where rules overlap, the lowest limit wins. rclone has a
  single speed limiter shared by all its transfers, so the app sets it as runs
  start and stop and as windows open and close; jobs running at the same time
  share the lowest limit that applies to any of them. Times use `TIMEZONE`.
- **Themes** — eight colour themes (dark and light) from the **Theme** menu in
  the navigation bar. The choice is stored in your browser, so each browser
  picks its own.

## Keeping backups safe

`rclone sync` makes the destination match the source, so it deletes files there
that the source no longer has. If the source disappears (a dropped NFS or SMB
mount, an unplugged drive), it can look empty, and a sync would delete the whole
backup.

Two checks stop that:

- **Source check.** Before a sync starts, the app lists the top level of the
  source. If that fails or comes back empty, the run is **held**. rclone is not
  started, and the run page says why.
- **Delete limit.** A sync refuses to delete more than a set number of files in
  one run (default 1000, set per job; `-1` turns it off). It is passed to rclone
  as `--max-delete`. A run stopped this way is marked failed, with the reason.

Neither kind of run is retried automatically, even when auto-resume is on. Check
the run before you run the job again.

Copy and move jobs are not checked, because they can't delete files at the
destination.

**Run anyway** (on a held or delete-limited run) starts the job once with both
checks off. It asks for confirmation first. Use it only when you know the
deletions are expected.

## Why size + mixed ordering?

If a backup contains both huge files (videos, disk images) and thousands of
tiny ones (photos, documents, source code), the order rclone transfers them in
matters a lot:

| Order | What happens |
|-------|--------------|
| Default (directory order) | Arbitrary. Big files can start late and become a long tail at the end. |
| Largest first (`size,desc`) | Every worker is busy on huge files, so the small files sit idle in the queue — you see few "new files" for hours, and per-file overhead (API calls, round trips) goes unused. |
| Smallest first (`size,asc`) | Thousands of small files finish quickly, but the huge files only start at the end, so the job's final stretch is a few slow, bandwidth-bound transfers. |
| **Mixed (`size,mixed,50`)** | Half of the transfer workers take the **smallest** remaining file, the other half take the **largest**. Small files keep flowing while the big ones are also being processed. |

The mixed window is the percentage of workers that go smallest-first (default
`50`); the rest go largest-first, and each group keeps its direction for the
whole run. For example, with `Transfers = 4` and `size,mixed,50`, two workers
chew through small files while two work on large ones. Use a lower percentage
(say `25`) if you want most workers on the big files, or a higher one if you
want small files to finish sooner.

Set it per job under **Order By Field / Order Direction / Mixed Window (%)** in
the job form; it is passed to rclone as `--order-by size,mixed,50`. Because the
split is across transfer workers, it works best with several parallel
transfers, and rclone's own docs note that ordering is more accurate with a
larger `--max-backlog`.

## Windows app

A regular Windows program with its own window. It runs without Docker or Python
and includes rclone. Closing the window leaves it in the system tray so scheduled
syncs keep running; choose **Quit** from the tray icon's menu to exit.

1. Download `rClone-GUI-Setup.exe` from the
   [releases page](https://github.com/Joe11221/rClone-GUI/releases). No admin
   rights are needed. The installer is unsigned, so SmartScreen warns on first
   run: choose **More info**, then **Run anyway**.
2. Leave **Start rClone GUI when I log in** ticked if you want schedules to keep
   running.
3. Right-click the tray icon and choose **Configure remotes…** to add cloud
   remotes. Existing rclone remotes are picked up automatically.

If the PC was asleep or off when a schedule came due, the run starts once when
the app is next running, however many were missed. The PC stays awake during a
transfer, but the app can't wake a sleeping PC, and schedules only run while you
are logged in.

The Windows app has no login by default. It listens on loopback only and is
meant for one user. Other accounts on the same PC can still reach that port, so
on a PC shared with people you don't trust, turn login on. Open
`%LOCALAPPDATA%\rClone GUI\settings.json`, add `"auth_mode": "password"`, quit the
app from the tray, and start it again. The window will then ask you to choose a
password.

Data is kept in `%LOCALAPPDATA%\rClone GUI` and is left in place on uninstall.
Password-encrypted rclone configs aren't supported yet.

See [docs/standalone-windows.md](docs/standalone-windows.md) for the design and
build steps.

## Quickstart (Docker)

Requires Docker with the Compose plugin, and an existing rclone config
(`rclone config`) if you want to use cloud remotes.

```bash
cp .env.example .env
# Edit .env: TIMEZONE, RCLONE_CONFIG, DATA_PATH, (PORT, BIND_ADDRESS)
docker compose up -d --build
```

Then open `http://127.0.0.1:${PORT}` (default `8080`). The first visit asks you
to choose a password, so do that before anyone else can reach the port. The
container auto-restarts (`unless-stopped`).

`SECRET_KEY` is optional. If it is empty, the app generates one on first start
and keeps it in `./data`, so logins survive restarts. Placeholder values such as
`change-me` are rejected.

### Configuration (`.env`)

| Key | Default | Description |
|-----|---------|-------------|
| `SECRET_KEY` | generated | Signs login sessions. Leave empty to have one generated and saved in `./data`, or set a long random value (`python3 -c "import secrets; print(secrets.token_hex(32))"`). Placeholder values are refused. |
| `AUTH_MODE` | `password` | `password`: one password, chosen on first visit. `proxy`: trust the login from a reverse proxy (see [Security notes](#security-notes)). `none`: no login at all. |
| `AUTH_PROXY_HEADER` | `Remote-User` | Header the reverse proxy sets to the logged-in user. Only used when `AUTH_MODE=proxy`. |
| `SECURE_COOKIES` | `false` | Set to `true` when the app is served over https, so the login cookie is never sent over plain http. |
| `PUBLIC_URL` | — | Base address for links in notifications, e.g. `http://nas.lan:8080`. The value saved on the Settings page takes precedence. Passed through by `docker-compose.yml`. |
| `PORT` | `8080` | Host port for the web GUI |
| `BIND_ADDRESS` | `127.0.0.1` | Host address the port is published on. `0.0.0.0` exposes it to your whole network — see the warning above. |
| `TIMEZONE` | `UTC` | IANA timezone (e.g. `America/New_York`) used to display times **and** to interpret cron schedules |
| `RCLONE_CONFIG` | `~/.config/rclone` | Host path to the rclone config directory (remotes are defined here) |
| `DATA_PATH` | `/mnt/storage` | Host data path, mounted at `/mnt/` inside the container |
| `ALLOWED_HOSTS` | any | Comma-separated `host:port` values the app answers to, e.g. `nas.lan:9020`. Setting it blocks DNS rebinding attacks |

Example for a home server you want to reach from other machines on your LAN:

```ini
PORT=9020
BIND_ADDRESS=0.0.0.0
TIMEZONE=America/New_York
DATA_PATH=/mnt
```

### Volumes

- `./data` -> `/app/instance` — SQLite database (gitignored)
- `${RCLONE_CONFIG}` -> `/root/.config/rclone` — rclone remotes/config. This
  contains your cloud credentials; keep it out of version control.
- `${DATA_PATH}` -> `/mnt/` (**read-write**) — the host data hierarchy.
  Note: this gives the container write access to everything under
  `DATA_PATH`; `move` operations to local destinations rely on this. Point it
  at the narrowest directory that works for you.

### Upgrading

This release adds login and safety checks, so two things change on upgrade:

- After upgrading, the first visit asks you to choose a password. To keep the
  old no-login behaviour, set `AUTH_MODE=none` in `.env` and restart.
- Existing sync jobs get a delete limit of 1000 files. If a job legitimately
  deletes more than that in one run, raise the limit on the job form, or set it
  to `-1` for no limit. A run that hits the limit is marked failed. **Run
  anyway** starts it once without the limit.

The database schema is created and upgraded by the app itself on startup
(`db.create_all()` plus `_ensure_columns()` in `app/__init__.py`), so there is
no separate migration step. Just `docker compose up -d --build`.

If you previously ran a version where `TIMEZONE` was not set in
`docker-compose.yml`, note that cron schedules used the container's clock
(UTC) while times were displayed in `America/New_York`. Schedules now run in
`TIMEZONE`, so set it explicitly and double-check your cron expressions.

## Architecture

```
Browser <--HTTP/SSE--> Gunicorn (gevent, 1 worker) :8080 <--RC HTTP--> rclone rcd :5572
                              |
                          SQLite (./data)
```

Both `rclone rcd` and Gunicorn run in a single container, started by
`entrypoint.sh`. The rclone RC API is bound to
`127.0.0.1` inside the container and protected by a password generated on every
start, so only the Flask app can reach it. (rclone only allows adding remotes
through an authenticated API.)

Key design rules:

- **Nothing runs unless the user created it.** The app only tracks runs in
  `JobRunner._active_runs`, and the SSE endpoint polls rclone per stats group
  (`core/stats?group=job_{run_id}`), never the global stats, so unrelated
  rclone activity never shows up.
- **All rclone operations are async** (`_async: true`) and return a `jobid`;
  the app never blocks on multi-hour transfers.
- **Each run gets a stats group** `job_{run_id}` for per-job metrics. SSE and
  the dashboard depend on this naming.
- **Gevent is mandatory** (SSE connections hold greenlets). `wsgi.py` calls
  `gevent.monkey.patch_all()` first; don't use stdlib `threading`/`queue`.
- **Gunicorn runs with `workers = 1`** — the in-memory `_active_runs` dict and
  the APScheduler monitor must live in a single process (see
  `gunicorn.conf.py`).

### How a run works

1. Start -> `POST /ops/start/<job_id>` -> `JobRunner.start_job()`
2. A `RunHistory` row is created (status `running`). For a sync, the source
   is checked first and the run is `held` if the check fails. Then rclone is
   called with `_async=true` and `_group=job_{run_id}`
3. The SSE endpoint polls `core/stats?group=job_{run_id}` (every 2s while
   active, 5s when idle)
4. A 10-second monitor copies new per-file failures into the DB while a run is
   active, then finalizes completed runs and writes their final stats
5. Auto-resume (if enabled) re-dispatches orphaned/failed runs

### Project structure

```
app/
├── __init__.py           # App factory, startup checks, _ensure_columns() schema upgrades
├── auth.py               # Login (password / proxy / none), sessions, lockout, secret key
├── config.py             # Env-based config (SECRET_KEY, AUTH_MODE, TIMEZONE, RCLONE_RC_URL, ...)
├── extensions.py         # SQLAlchemy instance
├── models.py             # Job, Schedule, RunHistory (SQLite)
├── rclone_client.py      # HTTP wrapper for rclone RC API
├── routes/
│   ├── health.py         # GET /api/health: backup health as JSON
│   ├── jobs.py           # CRUD for job definitions + directory browse API
│   ├── operations.py     # Start/stop/resume/status of running syncs
│   ├── schedules.py      # Cron schedule CRUD + enable/disable
│   ├── history.py        # Past run list + detail view
│   ├── run_errors.py     # Per-run failure list (JSON, paged), summary, CSV export
│   ├── sse.py            # SSE endpoint — polls rclone directly (no bg thread)
│   └── settings.py       # Remotes, bandwidth rules, notification and heartbeat settings
├── services/
│   ├── app_settings.py   # Key/value settings edited in the UI (stored in SQLite)
│   ├── bandwidth.py      # Weekly bandwidth rules and the single rclone speed limit
│   ├── events.py         # In-process event bus: run finished/held/skipped, health changed
│   ├── health.py         # Backup health rules: per-job state, deadlines, 7-day success rate
│   ├── job_runner.py     # Core orchestration: start/stop jobs, safety checks, finalize runs
│   ├── notifier.py       # Notifications (Apprise) and the heartbeat ping
│   ├── remotes.py        # Creating rclone remotes from the GUI, including OAuth sign-in
│   ├── run_errors.py     # Turns rclone transfer errors into stored, grouped failures
│   ├── scheduler_service.py  # APScheduler cron triggers and skipped-run records
│   └── suspects.py       # Suggests folders to exclude while a run is in progress
├── security.py           # Cross-site POST and ALLOWED_HOSTS checks
├── templates/            # Jinja2, Bootstrap 5 (themed via data-theme)
└── static/               # CSS, vendored Bootstrap, vanilla JS (SSE, errors, themes)
desktop/                  # Windows app: WebView2 window, tray icon, rclone supervisor
packaging/                # PyInstaller spec, Inno Setup script, build.ps1
tests/                    # pytest suite (no rclone needed)
```

Tech stack: Flask 3.1, SQLAlchemy (SQLite), APScheduler 3.10, Apprise
(notifications), Gunicorn + gevent, Bootstrap 5 (vendored, no CDN) and vanilla JS (no build tools). The
Windows app adds pywebview, pystray, Pillow and PyInstaller.

## Security notes

- **Login** (`AUTH_MODE`):
  - `password` (default): one shared password, chosen on the first visit. Login
    sessions last 30 days. Five failed attempts from one address lock that
    address out for 15 minutes. Changing the password on the Settings page signs
    out every other browser.
  - `proxy`: the app trusts the user named in the `AUTH_PROXY_HEADER` header
    (default `Remote-User`), which a reverse proxy such as Authelia, Authentik or
    oauth2-proxy sets after it has logged the user in. Requests without the
    header get `401`. The app's port must then be reachable only by that proxy,
    or anyone can send the header.
  - `none`: no login. The web UI shows a warning banner.
- **Lockouts are per client address.** Behind a reverse proxy in `password`
  mode, every client appears to come from the proxy's address, so one person's
  failed attempts lock everyone out for 15 minutes.
- **Cross-site requests are rejected** (`app/security.py`). A browser POST that
  another website sent is refused, based on the `Sec-Fetch-Site` or `Origin`
  header. Requests without those headers (scripts, `curl`) are not covered by
  that check, so they still need a login.
- The directory browser (`/jobs/api/browse`) and job `extra_flags` let any
  user of the app list arbitrary paths and set arbitrary rclone options.
- The data path is mounted read-write, and mounted rclone config contains your
  remote credentials.
- The published port defaults to loopback; widening it is an explicit opt-in
  (`BIND_ADDRESS`).
- The rclone RC API is bound to loopback inside the container only, behind a
  per-start password.

Found a security problem? Please open an issue (or contact the maintainer
privately for anything sensitive).

## Development

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
pytest
```

The tests use an in-memory SQLite database and a fake rclone client, and do
not start any background scheduler threads (`BACKGROUND_TASKS_ENABLED = False`).
CI runs the test suite and a Docker build on every push and pull request.

Gotchas:

- `exclude_patterns`/`extra_flags` are JSON strings in SQLite; use
  `job.get_excludes()` / `job.get_extra_flags()`.
- Runs record the settings they were dispatched with (`config_snapshot`),
  shown under "Settings used for this run" on the run page. Runs from before
  this release have no snapshot, so for those the job's *current* settings are
  the only reference.
- Move operations may not resume cleanly after an interruption (partial file
  relocation); a warning is shown in the job form.
- New columns on existing tables need an `ALTER TABLE` entry in
  `_ensure_columns()` (`create_all()` won't add them).
- APScheduler jobs run outside a request, so DB access needs
  `app.app_context()`.

## License

[MIT](LICENSE)
