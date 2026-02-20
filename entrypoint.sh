#!/bin/bash
set -e

# Start rclone daemon in the background
echo "Starting rclone rcd..."
rclone rcd \
    --rc-addr=127.0.0.1:5572 \
    --rc-no-auth \
    --rc-allow-origin="*" \
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

# Initialize database if needed
if [ ! -f /app/instance/rclone_gui.db ]; then
    echo "Initializing database..."
    cd /app
    flask db init || true
    flask db migrate -m "initial" || true
    flask db upgrade || true
fi

# Start gunicorn
echo "Starting gunicorn..."
exec gunicorn --config gunicorn.conf.py wsgi:app
