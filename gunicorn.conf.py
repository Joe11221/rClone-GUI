# Listens on all interfaces *inside the container* so Docker can forward to it.
# What the host exposes is controlled by BIND_ADDRESS in docker-compose.yml.
bind = "0.0.0.0:8080"
# Single worker: gevent handles concurrency via greenlets (not processes).
# Multiple workers would duplicate the in-memory _active_runs dict,
# APScheduler monitors, and restore_active_runs() calls — causing race
# conditions with SQLite and duplicate job resumes.
workers = 1
worker_class = "gevent"
timeout = 300
keepalive = 5
accesslog = "-"
errorlog = "-"
