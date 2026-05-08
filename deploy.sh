#!/usr/bin/env bash
set -euo pipefail

export PATH="/usr/local/go/bin:${PATH}"

APP_DIR="/var/www/api"
API_SERVICE="x106-api"
WORKER_SERVICE="x106-worker"
HEALTH_URL="https://api.pkn.io.vn/api/v1/health"
REF="${1:-origin/main}"

echo "[api] deploy start $(date '+%Y-%m-%d %H:%M:%S') ref=${REF}"
cd "$APP_DIR"

go version
git fetch origin main --tags --prune
git reset --hard "$REF"

go test ./...
go build -o x106-api.new ./cmd/server
go build -o x106-worker.new ./cmd/worker
chmod +x x106-api.new x106-worker.new
mv x106-api.new x106-api
mv x106-worker.new x106-worker

systemctl restart "$API_SERVICE"
sleep 2
systemctl is-active --quiet "$API_SERVICE"
curl -fsSL "$HEALTH_URL" >/dev/null

# Worker is optional during initial migration: if the unit isn't installed
# yet, log it and continue rather than failing the whole deploy. We check the
# file directly because `systemctl list-unit-files` over a non-TTY pipe drops
# rows in some versions (Ubuntu 24.04 systemd 255), so a grep may miss a unit
# that actually exists.
if [ -f "/etc/systemd/system/${WORKER_SERVICE}.service" ]; then
    systemctl restart "$WORKER_SERVICE"
    sleep 1
    systemctl is-active --quiet "$WORKER_SERVICE"
    echo "[api] worker restarted"
else
    echo "[api] WARNING: ${WORKER_SERVICE}.service not installed — async LLM jobs will not be processed"
fi

git diff --quiet
echo "[api] deploy done $(date '+%Y-%m-%d %H:%M:%S')"
