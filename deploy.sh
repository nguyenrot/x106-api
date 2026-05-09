#!/usr/bin/env bash
# Manual fallback: extract /tmp/api-deploy.tar.gz built by GitHub Actions,
# then restart systemd services. Production deploys go through Actions
# (.github/workflows/deploy.yml). Use this only for emergency rollback
# or when Actions is unavailable.
set -euo pipefail

APP_DIR="/var/www/api"
API_SERVICE="x106-api"
WORKER_SERVICE="x106-worker"
ARCHIVE="${1:-/tmp/api-deploy.tar.gz}"

[ -f "$ARCHIVE" ] || { echo "no archive at $ARCHIVE"; exit 1; }

cd "$APP_DIR"
cp -f x106-api x106-api.bak 2>/dev/null || true
cp -f x106-worker x106-worker.bak 2>/dev/null || true

tar xzf "$ARCHIVE"
chmod +x x106-api x106-worker

systemctl restart "$API_SERVICE"
sleep 2
systemctl is-active --quiet "$API_SERVICE"
curl -fsSL https://api.pkn.io.vn/api/v1/health -o /dev/null

if [ -f "/etc/systemd/system/${WORKER_SERVICE}.service" ]; then
  systemctl restart "$WORKER_SERVICE"
  sleep 1
  systemctl is-active --quiet "$WORKER_SERVICE"
  echo "[api] worker restarted"
fi
echo "[api] manual deploy done"
