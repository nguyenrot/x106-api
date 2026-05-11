#!/usr/bin/env bash
# Manual fallback for the X106 Python API deploy.
#
# Production deploys go through GitHub Actions (.github/workflows/deploy.yml),
# which builds the source tarball + ships it. This script is the "I need to
# extract and restart by hand" companion: extract /tmp/api-deploy.tar.gz, run
# `uv sync` + `manage.py migrate`, restart the systemd units. Use only for
# emergency rollback or when Actions is unavailable.

set -euo pipefail

APP_DIR="/var/www/api"
API_SERVICE="x106-api"
CELERY_SERVICE="x106-celery-worker"
BEAT_SERVICE="x106-celery-beat"
ARCHIVE="${1:-/tmp/api-deploy.tar.gz}"

[ -f "$ARCHIVE" ] || { echo "no archive at $ARCHIVE"; exit 1; }

cd "$APP_DIR"
tar xzf "$ARCHIVE"

export PATH="$HOME/.local/bin:$PATH"
uv sync --frozen
uv run python manage.py migrate --noinput

systemctl restart "$API_SERVICE"
sleep 2
systemctl is-active --quiet "$API_SERVICE"
curl -fsSL https://api.kynguyen.cc/api/v1/health -o /dev/null

for svc in "$CELERY_SERVICE" "$BEAT_SERVICE"; do
  if [ -f "/etc/systemd/system/${svc}.service" ]; then
    systemctl restart "$svc"
    sleep 1
    systemctl is-active --quiet "$svc"
    echo "[api] $svc restarted"
  fi
done

echo "[api] manual deploy done"
