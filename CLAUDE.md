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
