#!/bin/bash
# Start the mail-agent Docker container.
# Waits for Docker Desktop to be ready before launching.

COMPOSE_DIR="/Users/g4ndr1k/agentic-ai"
DOCKER="/usr/local/bin/docker"
LOG_PREFIX="[start_agent]"
MAX_WAIT=120   # seconds to wait for Docker Desktop
INTERVAL=5

echo "$(date '+%Y-%m-%d %H:%M:%S') $LOG_PREFIX Waiting for Docker..."

elapsed=0
while ! "$DOCKER" info >/dev/null 2>&1; do
    if [ "$elapsed" -ge "$MAX_WAIT" ]; then
        echo "$(date '+%Y-%m-%d %H:%M:%S') $LOG_PREFIX ERROR: Docker not available after ${MAX_WAIT}s — giving up."
        exit 1
    fi
    sleep "$INTERVAL"
    elapsed=$((elapsed + INTERVAL))
done

echo "$(date '+%Y-%m-%d %H:%M:%S') $LOG_PREFIX Docker ready (waited ${elapsed}s)."
echo "$(date '+%Y-%m-%d %H:%M:%S') $LOG_PREFIX Starting mail-agent container..."

cd "$COMPOSE_DIR" || exit 1
"$DOCKER" compose up -d

STATUS=$?
if [ "$STATUS" -eq 0 ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') $LOG_PREFIX mail-agent started successfully."
else
    echo "$(date '+%Y-%m-%d %H:%M:%S') $LOG_PREFIX ERROR: docker compose up exited with code $STATUS."
    exit "$STATUS"
fi
