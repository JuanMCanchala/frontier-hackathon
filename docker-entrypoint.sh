#!/bin/sh
set -e
# A freshly mounted volume is empty and owned by root, so the app user could not write to it.
if [ "$(id -u)" = "0" ]; then
    mkdir -p "$(dirname "$TASKS_DB")" "$TRACES_DIR"
    if [ -n "${APP_MEMORY_DB:-}" ]; then
        mkdir -p "$(dirname "$APP_MEMORY_DB")"
    fi
    chown -R app:app /app/data
    exec setpriv --reuid=app --regid=app --init-groups "$@"
fi
exec "$@"
