# X106 API systemd units

These are the production systemd unit files. **Install once** on the VPS:

```bash
# As root on the VPS:
mkdir -p /var/lib/x106
cp /var/www/api/infra/systemd/*.service /etc/systemd/system/

# One-time only — disable the legacy Go worker if it's still installed:
systemctl disable --now x106-worker.service 2>/dev/null || true

systemctl daemon-reload
systemctl enable --now x106-api x106-celery-worker x106-celery-beat
systemctl status x106-api x106-celery-worker x106-celery-beat
```

Subsequent deploys (via GitHub Actions) `systemctl restart` these — no need
to touch the unit files again unless you're changing flags.

**Required VPS prep** (also one-time):

```bash
apt-get update
apt-get install -y python3.13 python3.13-dev pkg-config default-libmysqlclient-dev build-essential redis-server
systemctl enable --now redis-server
curl -LsSf https://astral.sh/uv/install.sh | sh   # installs uv to /root/.local/bin
```

`/etc/x106-api.env` should contain (sample — fill in real secrets):

```
DJANGO_ENV=production
DJANGO_SECRET_KEY=...
ALLOWED_HOSTS=api.pkn.io.vn
DB_HOST=127.0.0.1
DB_PORT=3306
DB_NAME=finance_app
DB_USER=finance_user
DB_PASSWORD=...
JWT_SECRET=...
COOKIE_DOMAIN=.pkn.io.vn
REDIS_URL=redis://127.0.0.1:6379/0
DEEPSEEK_API_KEY=...
DEEPSEEK_MODEL=deepseek-v4-pro
LLM_DAILY_LIMIT=5
```
