# rClone GUI

Flask web GUI for managing rclone backup jobs on the home network. Create
sync/copy/move jobs, schedule them with cron, watch live transfer progress,
and browse run history — all from a browser.

This is a custom app (not the upstream rclone web GUI). It never shells out
to rclone; every operation goes through the **rclone RC HTTP API**.

## Architecture

```
Browser <--HTTP/SSE--> Gunicorn (gevent, 1 worker) :8080 <--RC HTTP--> rclone rcd :5572
                              |
                          SQLite (./data)
```

Both `rclone rcd` and Gunicorn run in a single container, started by
`entrypoint.sh` (waits for the RC API, initializes the database on first
boot, then execs Gunicorn).

Key design rules:

- All rclone operations are async (`_async: true`) and return a `jobid`; the
  app never blocks on multi-hour transfers.
- Each run gets a stats group `job_{run_id}` so the SSE endpoint can poll
  per-job stats (`core/stats?group=...`) and never shows phantom transfers
  from other rclone processes.
- Gevent is mandatory (SSE connections hold greenlets); stdlib
  `threading`/`queue` must not be used.
- Gunicorn runs with `workers = 1` — the in-memory `_active_runs` dict and
  the APScheduler monitor must live in a single process (see
  `gunicorn.conf.py`).

## Features

- **Jobs** — CRUD for sync/copy/move jobs with source/destination (local
  paths or remotes), exclude patterns, extra flags, per-job bandwidth, and
  `--order-by` composition (field `size`/`name`/`modtime`, direction
  `asc`/`desc`/`mixed` with a window %).
- **Directory browser** — the Browse button on the job form opens a modal
  that walks local paths and remotes via `operations/list`.
- **Schedules** — per-job cron schedules (APScheduler), enable/disable.
- **Live dashboard** — SSE-driven progress bars, speed, bytes, file counts
  (new vs unchanged), and ETA.
- **History** — every run is recorded with final stats and status
  (`completed`/`failed`/`stopped`/`interrupted`).
- **Auto-resume** — per-job toggle. Orphaned `running` records are
  re-dispatched on container restart; failed runs retry after a delay up to
  `max_retries` (default 3). Each retry is a new history record linked via
  `retry_of_run_id`. User-stopped runs are never auto-resumed. A manual
  Resume button is available on failed/interrupted runs.
- **Settings** — configured remotes list and a global bandwidth limit.

## Quickstart

```bash
cp .env.example .env
# Edit .env: PORT, SECRET_KEY, RCLONE_CONFIG, DATA_PATH
docker compose up -d --build
```

The GUI is served at `http://<host>:${PORT}`. The container auto-restarts
(`unless-stopped`).

### Configuration (`.env`)

| Key | Default | Description |
|-----|---------|-------------|
| `PORT` | `8080` | Host port for the web GUI |
| `SECRET_KEY` | — | Flask secret key; set a random value |
| `RCLONE_CONFIG` | `~/.config/rclone` | Host path to the rclone config directory (remotes are defined here) |
| `DATA_PATH` | `/mnt/storage` | Host data path, mounted at `/mnt/` inside the container |

Current cephas deployment: `PORT=9020`, `RCLONE_CONFIG=~/.config/rclone`,
`DATA_PATH=/mnt/` — so the GUI can browse all mounted pools under `/mnt/`.

### Volumes

- `./data` -> `/app/instance` — SQLite database (gitignored)
- `${RCLONE_CONFIG}` -> `/root/.config/rclone` — rclone remotes/config
- `${DATA_PATH}` -> `/mnt/` (**read-write**) — the host data hierarchy.
  Note: this gives the container write access to everything under
  `DATA_PATH` (on cephas: all SnapRAID pools); move operations to local
  destinations rely on this.

## How a run works

1. Start -> `POST /ops/start/<job_id>` -> `JobRunner.start_job()`
2. A `RunHistory` row is created (status `running`) and rclone is called
   with `_async=true` and `_group=job_{run_id}`
3. The SSE endpoint polls `core/stats?group=job_{run_id}` (every 2s while
   active, 5s when idle)
4. A 10-second monitor finalizes completed runs and writes final stats to
   the DB
5. Auto-resume (if enabled) re-dispatches orphaned/failed runs

## Project structure

```
app/
├── __init__.py           # App factory — wires everything, _ensure_columns() migration
├── config.py             # Env-based config (RCLONE_RC_URL, SECRET_KEY, etc.)
├── extensions.py         # SQLAlchemy + Flask-Migrate instances
├── models.py             # Job, Schedule, RunHistory (SQLite)
├── rclone_client.py      # HTTP wrapper for rclone RC API
├── routes/
│   ├── jobs.py           # CRUD for job definitions + directory browse API
│   ├── operations.py     # Start/stop/resume/status of running syncs
│   ├── schedules.py      # Cron schedule CRUD + enable/disable
│   ├── history.py        # Past run list + detail view
│   ├── sse.py            # SSE endpoint — polls rclone directly (no bg thread)
│   └── settings.py       # Remotes list, global bandwidth limit
├── services/
│   ├── job_runner.py     # Core orchestration: start/stop jobs, finalize runs
│   ├── scheduler_service.py  # APScheduler cron triggers
│   └── stats_collector.py    # LEGACY — unused, do not re-introduce
├── templates/            # Jinja2, Bootstrap 5 dark theme
└── static/
    ├── css/style.css
    └── js/
        ├── app.js        # SSE client, formatBytes/Speed/ETA helpers
        └── dashboard.js  # Per-run live stats updates
```

## Tech stack

- **Backend**: Flask 3.1, SQLAlchemy + Flask-Migrate (SQLite), APScheduler
  3.10, Gunicorn + gevent
- **rclone**: installed in the image via the official script; RC API on
  `127.0.0.1:5572` with `--rc-no-auth`
- **Frontend**: Bootstrap 5 (CDN), Bootstrap Icons, vanilla JS (no build
  tools)

## Security notes

- **No authentication** — intended for a trusted LAN only. Blueprints are
  structured so Flask-Login can be added later (`@login_required`).
- **No CSRF protection** (would need Flask-WTF).
- The rclone RC API is bound to loopback inside the container only.

## Gotchas

- `stats_collector.py` is legacy/unused — SSE polling is inline in
  `routes/sse.py`; the old thread-based collector conflicted with gevent.
- `exclude_patterns`/`extra_flags` are JSON strings in SQLite; use
  `job.get_excludes()` / `job.get_extra_flags()`.
- `RunHistory` does not snapshot job config; the detail page shows the
  *current* job config.
- Move operations may not resume cleanly after an interruption (partial file
  relocation); a warning is shown in the job form.
- `_ensure_columns()` in `app/__init__.py` adds new columns to existing
  SQLite databases at startup.
- Contributor/AI development guide: see `CLAUDE.md`.
