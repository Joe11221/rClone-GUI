#!/bin/bash
set -e

# Start rclone daemon in the background. The RC API is unauthenticated, so it
# is bound to loopback only (reachable just by the Flask app in this container)
# and no CORS origins are allowed.
echo "Starting rclone rcd..."
rclone rcd \
    --rc-addr=127.0.0.1:5572 \
    --rc-no-auth \
    &

# Wait for rclone to be ready
echo "Waiting for rclone RC API..."
for i in $(seq 1 30); do
    if curl -s http://127.0.0.1:5572/rc/noop > /dev/null 2>&1; then
        echo "rclone RC API is ready."
        break
    fi
    sleep 1
done

# The database schema is created (and upgraded) by the app itself on startup,
# see create_app() / _ensure_columns() in app/__init__.py.
echo "Starting gunicorn..."
exec gunicorn --config gunicorn.conf.py wsgi:app
