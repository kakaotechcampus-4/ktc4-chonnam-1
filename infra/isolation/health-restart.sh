#!/bin/bash
set -euo pipefail
status=$(docker inspect --format '{{.State.Health.Status}}' ktc-isolation-browser 2>/dev/null || true)
if [ "$status" = unhealthy ]; then
    systemctl restart ktc-isolation-collector
fi
