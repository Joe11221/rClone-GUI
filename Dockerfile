FROM python:3.12-slim

# Install rclone and curl (for health check in entrypoint)
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl unzip && \
    curl -O https://downloads.rclone.org/current/rclone-current-linux-amd64.zip && \
    unzip rclone-current-linux-amd64.zip && \
    cp rclone-*-linux-amd64/rclone /usr/bin/ && \
    chmod +x /usr/bin/rclone && \
    rm -rf rclone-* && \
    apt-get purge -y unzip && \
    apt-get autoremove -y && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY . .

# Make entrypoint executable
RUN chmod +x entrypoint.sh

# Create instance directory for SQLite
RUN mkdir -p /app/instance

ENV FLASK_APP=wsgi:app
ENV RCLONE_RC_URL=http://127.0.0.1:5572

EXPOSE 8080

ENTRYPOINT ["./entrypoint.sh"]
