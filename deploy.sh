#!/usr/bin/env bash
set -euo pipefail

export PATH="/usr/local/go/bin:${PATH}"

APP_DIR="/var/www/api"
SERVICE_NAME="x106-api"
HEALTH_URL="https://api.pkn.io.vn/api/v1/health"
REF="${1:-origin/main}"

echo "[api] deploy start $(date '+%Y-%m-%d %H:%M:%S') ref=${REF}"
cd "$APP_DIR"

go version
git fetch origin main --tags --prune
git reset --hard "$REF"

go test ./...
go build -o x106-api.new ./cmd/server
chmod +x x106-api.new
mv x106-api.new x106-api

systemctl restart "$SERVICE_NAME"
sleep 2
systemctl is-active --quiet "$SERVICE_NAME"
curl -fsSL "$HEALTH_URL" >/dev/null

git diff --quiet
echo "[api] deploy done $(date '+%Y-%m-%d %H:%M:%S')"
