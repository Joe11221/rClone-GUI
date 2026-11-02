#!/bin/bash
set -e

# Start rclone daemon in the background. Its RC API is bound to loopback only
# (reachable just by the Flask app in this container), allows no CORS origins,
# and requires a password made fresh on every start. rclone only permits
# configuration changes (adding remotes) on an authenticated API. rclone and the
# app both read the credentials from these variables.
export RCLONE_RC_USER=rclone-gui
RCLONE_RC_PASS=$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' 
')
export RCLONE_RC_PASS

echo "Starting rclone rcd..."
rclone rcd     --rc-addr=127.0.0.1:5572     &

# Wait for rclone to be ready
echo "Waiting for rclone RC API..."
for i in $(seq 1 30); do
    if curl -s -u "$RCLONE_RC_USER:$RCLONE_RC_PASS" -X POST http://127.0.0.1:5572/rc/noop > /dev/null 2>&1; then
        echo "rclone RC API is ready."
        break
    fi
    sleep 1
done

# The database schema is created (and upgraded) by the app itself on startup,
# see create_app() / _ensure_columns() in app/__init__.py.
echo "Starting gunicorn..."
exec gunicorn --config gunicorn.conf.py wsgi:app
