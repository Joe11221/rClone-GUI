# rClone GUI — Project Guide

## What This Is

Flask web GUI for rclone, deployed via Docker on an Ubuntu server. Lets users create backup jobs, schedule them, monitor real-time progress, and view history — all from a browser on the home network.

## Architecture

```
Browser <--HTTP/SSE--> Gunicorn (gevent) :8080 <--HTTP POST--> rclone rcd :5572
                              |
                         SQLite DB
```

Both rclone rcd and gunicorn run inside a single Docker container, started by `entrypoint.sh`. The Flask app never calls rclone as a subprocess — all interaction goes through the **rclone RC HTTP API**.

## Critical Design Rules

1. **Nothing runs unless the user created it.** The app only tracks and displays stats for runs in `JobRunner._active_runs`. The SSE endpoint polls rclone per-group (`core/stats?group=job_{run_id}`), never the global stats endpoint. This prevents showing phantom transfers from other rclone processes.

2. **All rclone operations are async.** Every `sync/copy/move` call passes `_async: True` and gets back a `jobid`. The app never blocks waiting for a multi-hour transfer to finish.

3. **Stats groups isolate per-job metrics.** Each run gets `stats_group = f"job_{run.id}"` passed as `_group` to rclone. This is the only way to get per-job stats from rclone's RC API. If you change this naming convention, SSE and the dashboard will break.

4. **Gevent is mandatory.** SSE connections hold open a worker greenlet. The `wsgi.py` calls `gevent.monkey.patch_all()` before anything else. Without this, `time.sleep()` and `requests` calls block the entire worker. Do NOT use stdlib `threading.Thread` or `queue.Queue` — they conflict with gevent's cooperative model.

## Project Structure

```
app/
├── __init__.py           # App factory — wires everything together
├── config.py             # Env-based config (RCLONE_RC_URL, SECRET_KEY, etc.)
├── extensions.py         # SQLAlchemy + Flask-Migrate instances
├── models.py             # Job, Schedule, RunHistory (SQLite)
├── rclone_client.py      # HTTP wrapper for rclone RC API
├── routes/
│   ├── jobs.py           # CRUD for job definitions
│   ├── operations.py     # Start/stop/status of running syncs
│   ├── schedules.py      # Cron schedule CRUD + enable/disable
│   ├── history.py        # Past run list + detail view
│   ├── sse.py            # SSE endpoint — polls rclone directly (no bg thread)
│   └── settings.py       # Remotes list, global bandwidth limit
├── services/
│   ├── job_runner.py     # Core orchestration: start/stop jobs, finalize runs
│   └── scheduler_service.py  # APScheduler cron triggers
├── templates/            # Jinja2, Bootstrap 5 dark theme
└── static/
    ├── css/style.css
    └── js/
        ├── app.js        # SSE client, formatBytes/Speed/ETA helpers
        └── dashboard.js  # Per-run live stats updates
```

## Key Files to Understand First

1. **`app/services/job_runner.py`** — The core. `_active_runs` dict is the source of truth for what's running. `start_job()` creates the DB record and dispatches to rclone. `check_and_update_runs()` finalizes completed jobs (called every 10s by APScheduler monitor in `__init__.py`).

2. **`app/rclone_client.py`** — Every rclone interaction flows through here. Maps Python methods to RC API endpoints. All sync ops use `_async=True`.

3. **`app/routes/sse.py`** — SSE endpoint. Polls rclone directly in the generator (no background thread). When idle, sleeps 5s. When jobs are active, polls every 2s. Only fetches stats for runs in `job_runner._active_runs`.

4. **`app/static/js/dashboard.js`** — Listens for `rclone-stats` custom events dispatched by `app.js`. Updates DOM elements matched by `data-run-id` attributes on `.transfer-item` elements.

## Data Flow: Starting a Job

1. User clicks Start → `POST /ops/start/<job_id>`
2. `operations.py` → `job_runner.start_job(job)`
3. JobRunner creates `RunHistory` (status="running"), flushes to get `run.id`
4. Sets `stats_group = f"job_{run.id}"`, calls `rclone_client.start_sync()` with `_async=True` and `_group=stats_group`
5. Stores `rclone_jobid` in RunHistory, adds to `_active_runs` dict
6. SSE endpoint sees the run in `_active_runs`, starts polling `core/stats?group=job_{run_id}`
7. Dashboard JS receives SSE data, updates progress bars and stats
8. Monitor (every 10s) calls `check_and_update_runs()` → detects `finished=True` → writes final stats to DB

## Common Gotchas

- **`stats_collector.py` exists but is unused.** SSE polling is done inline in `sse.py`. The old StatsCollector used stdlib threads that conflicted with gevent. Don't re-introduce it.
- **`exclude_patterns` and `extra_flags` are JSON strings in SQLite.** Use `job.get_excludes()` and `job.get_extra_flags()` to deserialize.
- **RunHistory doesn't snapshot job config.** The detail page shows the *current* job config, not config at time of execution.
- **Docker mounts the data drive read-only** (`/mnt/storage:ro`). Move operations to local destinations will fail unless the user changes this in `docker-compose.yml`.
- **No auth.** The app is open on the home network. The code is structured with blueprints so Flask-Login can be added later — just add `@login_required` decorators.
- **No CSRF protection on forms.** Would need Flask-WTF for production hardening.
- **APScheduler's `_run_scheduled_job` needs `app.app_context()`.** It runs outside the request context, so DB queries require an explicit context push.
- **Gunicorn must use `workers = 1`.** `_active_runs` is an in-memory dict, and the APScheduler monitor and `restore_active_runs()` run inside `create_app()`. With multiple workers each gets its own copy, causing duplicate job resumes and retry races against SQLite. Gevent handles concurrency via greenlets within a single process — multiple workers provide no benefit for this I/O-bound app.

## Deployment

```bash
# On Ubuntu server
cp .env.example .env
# Edit .env: set PORT, DATA_PATH, RCLONE_CONFIG, SECRET_KEY
docker compose up -d --build
# Access at http://<server-ip>:<PORT>
```

The container auto-restarts (`restart: unless-stopped`). Port is configurable via `PORT` env var (default 8080).

## Interactive Directory Browser (Create/Edit Job form)

Source and destination fields in `jobs/form.html` have a **Browse** button (<i class="bi bi-folder2-open">) that opens a Bootstrap modal directory browser.

- **Backend**: `GET /jobs/api/browse?path=<encoded>` in `app/routes/jobs.py` → calls `rclone.list_dirs(fs, remote)` (in `rclone_client.py`) which hits `operations/list` with `dirsOnly:True`.
- **Path parsing** in the endpoint: if path contains `:` it's a remote (`gdrive:` or `gdrive:Backups`); otherwise local (`/mnt/storage`).
- **Modal JS** (inline in `form.html`): `openBrowser(targetId)` → start picker if field empty, else `fetchDirs(path)`. Navigate in/out with `navigateInto()` / `goBack()`. `selectCurrent()` writes the chosen path back to the input.
- **Start picker** shows `/` (local root) plus all configured remotes; populated from `availableRemotes` JS variable set via `{{ remotes | tojson }}`.
- Remote quick-links under destination still work — now open the browser at `remote:` instead of just filling the input.
- Text inputs are still directly editable (for typing new/non-existent paths).

## Tech Stack

- **Backend**: Flask 3.1, SQLAlchemy, Flask-Migrate, APScheduler 3.10, gunicorn + gevent
- **Frontend**: Bootstrap 5 (CDN), Bootstrap Icons, vanilla JS (no build tools)
- **Database**: SQLite (persisted in Docker volume at `./data/`)
- **rclone**: RC API on localhost:5572, started by `entrypoint.sh`

## Live Stats Display (dashboard.js)

`dashboard.js` handles SSE-driven live updates for both the dashboard and the running operations page. Key design decisions:

- **Both pages must include `dashboard.js`** via `{% block scripts %}`. The running tab (`operations/running.html`) uses the same SSE listener as the dashboard.
- **Progress uses `bytes/totalBytes`** for overall job progress, not the average of currently-in-flight file percentages (which flickers as files start/finish).
- **`lastKnownStats` prevents flicker**: Speed, bytes, and progress values are tracked per run. Cumulative stats (bytes, transfers) never decrease in the display. Speed shows the last non-zero value when rclone briefly reports 0 between file transfers.
- **"Starting..." text** is preserved in `transfer-detail` until the first non-zero stats arrive, rather than being overwritten with "0 B transferred, 0 files".
- **File breakdown**: Stats show "New Files" (`stats.transfers` — files actually copied) and "Unchanged" (`stats.checks` — files already matching at destination). These map to rclone's `transfers` and `checks` fields from `core/stats`.

## Auto-Resume Interrupted Jobs

Jobs with `auto_resume=True` (per-job setting in the Job form) will automatically retry when:

1. **Container restart**: `restore_active_runs()` detects orphaned "running" records whose rclone jobs no longer exist, and re-dispatches them. The old run is marked `"interrupted"` and a new `RunHistory` record is created.
2. **Runtime failure**: `check_and_update_runs()` detects a failed rclone job and schedules a delayed retry via `gevent.spawn_later(retry_delay_seconds, ...)`.

Key design points:
- Each retry creates a **new RunHistory** record linked via `retry_of_run_id`. The old run is marked `"interrupted"`.
- `retry_count` tracks position in the chain; checked against `job.max_retries` (default 3).
- User-stopped runs (`status="stopped"`) are **never** auto-resumed.
- Retry delay uses `gevent.spawn_later` (not stdlib timers) to stay compatible with the gevent worker model.
- The `_pending_retries` set in `JobRunner` prevents duplicate retry scheduling from the 10s monitor loop.
- A manual **Resume** button is available on failed/interrupted runs in the history pages (`POST /ops/resume/<run_id>`). Manual resume bypasses the `auto_resume` toggle check.
- Move operations may not resume correctly due to partial file relocation — a warning is shown in the job form.
- `_ensure_columns()` in `app/__init__.py` handles SQLite schema migration for existing databases (adds new columns via `ALTER TABLE` since `create_all()` won't add columns to existing tables).

## Job-Level Order-By Option

Jobs now support configurable `--order-by` composition from the Create/Edit Job form:

- `Order By Field`: `size`, `name`, or `modtime`
- `Order Direction`: `asc`, `desc`, or `mixed`
- `Mixed Window (%)`: the third value used when direction is `mixed` (for example: `size,mixed,50`)

Implementation details:
- Stored on `Job` as `order_by_field`, `order_by_direction`, and `order_by_mixed_window`.
- `Job.to_rclone_config()` builds `_config["OrderBy"]` so rclone receives the equivalent of `--order-by`.
- Existing SQLite installs are updated at startup by `_ensure_columns()` adding the three new `job` columns if missing.

## Job-Level File Comparison Method

Each job has a configurable file comparison method that controls how rclone decides whether a file needs to be transferred:

- **Default** (modtime + size): rclone's built-in behavior — no extra config key
- **Size Only** (`--size-only`): only compare file sizes, ignore modification time
- **Checksum** (`--checksum`): compare file hashes instead of modtime
- **Ignore Existing** (`--ignore-existing`): skip any file that already exists on the destination

Implementation details:
- Stored on `Job` as `compare_method` (VARCHAR(20), default `"default"`). Valid values: `default`, `size_only`, `checksum`, `ignore_existing`.
- `Job.to_rclone_config()` maps the value to the corresponding rclone RC API config key (`SizeOnly`, `CheckSum`, or `IgnoreExisting`).
- Route validation in `_parse_compare_method()` rejects unknown values, falling back to `"default"`.
- Existing SQLite installs get the column via `_ensure_columns()` at startup.
