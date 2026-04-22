#!/bin/bash
set -e

# Install hyplan from mounted source (copy to temp to avoid read-only issues)
if [ -d /hyplan ]; then
    cp -r /hyplan /tmp/hyplan-src
    pip install --no-cache-dir "/tmp/hyplan-src[winds]"
    rm -rf /tmp/hyplan-src
fi

exec uvicorn app:app --host 0.0.0.0 --port 8100
