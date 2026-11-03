# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Self-hosters who run rclone backups of their own data: homelab owners deploying with Docker on a home server, and desktop users running the Windows tray app on a PC (the same web UI shown in the app's own window). They check in occasionally and mostly want to know whether each backup ran on time and succeeded. The project is also published as open source for other self-hosters.

## Product Purpose

A standalone Flask GUI for creating, scheduling, running and monitoring rclone sync/copy/move jobs without editing command lines or cron files. Success: anything rclone can do for a sync, copy or move is controllable from the job form, and the user is told when a backup is late or failed.

## Positioning

- Full rclone control in the job form (excludes, extra flags, per-job bandwidth rules, comparison method, `--order-by` composition including size + mixed), with sensible defaults so simple jobs stay simple.
- Backup health: checks that each backup runs on time and succeeds, with notifications and a heartbeat when one doesn't.
- Dashboard and failure list stay readable with thousands of files in a run.

## Operating Context

Jobs have source/destination (local paths or remotes), cron schedules interpreted in the configured `TIMEZONE`, live SSE-driven progress, per-file failures, and run history. All rclone operations go through the rclone RC HTTP API; the app never shells out. Docker deployment binds to `127.0.0.1` by default; login is on by default (first visit sets a password). Runs on a single gunicorn worker.

## Capabilities and Constraints

Dashboard, Jobs, Running, Schedules, History, Settings; directory browser modal over local paths and remotes; auto-resume of interrupted runs; run statuses running/completed/failed/stopped/interrupted. Bootstrap 5 and Bootstrap Icons are vendored; no external CDN. The app already offers six user-selectable themes (Graphite, Liquid Glass, Keycaps, Terminal, Synthwave, Brutalist), each in light, dark and system modes. Screenshots in `docs/screenshots` use demo data.

## Brand Commitments

Name: "rClone GUI". Icon: `app/static/favicon.svg`. Not the upstream rclone web GUI.

## Evidence on Hand

Demo screenshots (dashboard, running, job form, schedules, history) in `docs/screenshots`. No testimonials, user counts or benchmarks exist; do not fabricate them.

## Product Principles

1. Functionality first: expose rclone's real options rather than hiding them.
2. Answer "did my backup run?" before anything else.
3. Stay legible at scale: thousands of files, many jobs.
4. Safe by default: login on, localhost binding, explicit opt-outs.

## Accessibility & Inclusion

No product-specific standard established.
