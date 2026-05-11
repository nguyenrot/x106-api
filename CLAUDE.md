# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

This is the **Python (Django + DRF + Celery)** API backend for the X106 ecosystem. Port 4000, served at `api.kynguyen.cc`. The repo lives at `/var/www/api` on the VPS and runs as the systemd unit `x106-api.service`. Two siblings — `x106-celery-worker.service` (DeepSeek async LLM jobs) and `x106-celery-beat.service` (recovery + cleanup schedule) — replace the old Go `x106-worker`. The parent ecosystem doc is at `../CLAUDE.md` — read it first for context on the surrounding apps and shared design system.

The Go service was rewritten to Python on 2026-05-09. Old Go source lives in git history under tags `pre-python-rewrite` and earlier — use `git log --oneline -- cmd/ internal/` if you need archaeology.

## Commands

```bash
uv sync                                          # install deps
uv run python manage.py runserver 4000           # dev server
uv run python manage.py migrate                  # apply migrations
uv run python manage.py createsuperuser          # admin user (replaces ADMIN_USERNAME / ADMIN_PASSWORD_HASH env)
uv run python manage.py shell                    # ORM shell
uv run celery -A x106 worker -l info             # local LLM worker (needs Redis on :6379)
uv run celery -A x106 beat -l info               # local scheduler (60s recovery, 30min cleanup)
uv run pytest                                    # tests
```

Local Redis: `docker run --rm -p 6379:6379 redis:7`. MySQL: `docker run --rm -p 3306:3306 -e MYSQL_ROOT_PASSWORD=rootpw -e MYSQL_DATABASE=x106 mysql:8`.

### Deploy

`git push` to `main` triggers `.github/workflows/deploy.yml`:

1. **CI:** spin up MySQL, install `uv`, `uv sync --frozen`, `uv run pytest`, `uv run python manage.py collectstatic`.
2. **Package:** tar source tree (`pyproject.toml`, `uv.lock`, `manage.py`, `x106/`, `apps/`, `staticfiles/`, `deploy.sh`).
3. **Ship:** SCP to VPS `/tmp/api-deploy.tar.gz`.
4. **Apply:** extract into `/var/www/api`, run `uv sync --frozen`, `uv run python manage.py migrate --noinput`, `systemctl restart x106-api x106-celery-worker x106-celery-beat`, curl `/api/v1/health`.

End-to-end ~3-4 min (longer than Go because we install deps on the VPS — pays for itself by avoiding glibc-mismatch pain). The two Celery units are restarted only if their unit files exist (`/etc/systemd/system/x106-celery-{worker,beat}.service`), and a missing file logs a warning rather than failing the deploy.

Re-trigger / rollback to a branch or tag: `cd /Users/kynguyenpham/X106 && ./deploy.sh api [ref]`. SHA-direct rollback isn't supported by `gh workflow run` — tag the commit first.

`./deploy.sh` (the local file at `/var/www/api/deploy.sh`) is now a manual fallback only — extracts `/tmp/api-deploy.tar.gz`, runs `uv sync` + `migrate`, restarts the units.

### One-time VPS prep (already done; document for future hands)

```bash
apt-get install -y python3.13 python3.13-dev pkg-config default-libmysqlclient-dev build-essential redis-server
systemctl enable --now redis-server
curl -LsSf https://astral.sh/uv/install.sh | sh
mkdir -p /var/lib/x106
cp /var/www/api/infra/systemd/*.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now x106-api x106-celery-worker x106-celery-beat
# Disable the legacy Go worker if still installed:
systemctl disable --now x106-worker.service 2>/dev/null || true
```

`/etc/x106-api.env` contains the runtime env (DJANGO_SECRET_KEY, DB_*, JWT_SECRET, COOKIE_DOMAIN=.kynguyen.cc, REDIS_URL=redis://127.0.0.1:6379/0, DEEPSEEK_API_KEY, etc.). See `infra/systemd/README.md` for the full template.

### Migrations on the VPS

The deploy workflow already runs `python manage.py migrate --noinput` on every deploy — schema changes ride along with code in PRs. No manual SQL needed for normal additions. There is no `mysql` client on the host; if you ever need raw SQL, the production MySQL runs in the Docker container `finance-server-mysql-1` (DB name `finance_app`, not `x106`). For ad-hoc queries:

```bash
docker exec -it finance-server-mysql-1 mysql -u finance_user -p'<password>' finance_app
```

## Architecture

### Layout

```
manage.py
pyproject.toml            # uv-managed; pin Django==5.2.*, DRF, simplejwt, celery[redis], mysqlclient, httpx
x106/
  settings/{base,dev,production}.py   # split by env
  urls.py                              # mounts /api/v1 + each app's URLs
  wsgi.py                              # gunicorn entry
  celery.py                            # Celery app
apps/
  core/         # health endpoint, tz helpers (Asia/Ho_Chi_Minh), id generator, IsAdminToken
  accounts/    # User on `users` table, JWTCookieAuthentication, login/logout/register/admin views
  journal/      # Vibe + VibeViewSet (today, stats, upsert)
  studio/       # Artwork + LLM (model, quota, scene validator, DeepSeek client, Celery task, maintenance)
  content/      # SiteContent (public + admin upsert)
  admin_art/    # Admin endpoints for AI art mgmt (users quota, llm-prompt, settings, stats, logs, jobs)
infra/systemd/  # production unit files (x106-api, x106-celery-worker, x106-celery-beat)
.github/workflows/deploy.yml
deploy.sh
```

To add a feature: pick the right app, drop a model into `models.py`, a serializer into `serializers.py`, a ViewSet/APIView into `views.py`, register on the router in `urls.py`, then `python manage.py makemigrations` + `migrate`. The app is registered in `INSTALLED_APPS` in `x106/settings/base.py`.

### Database — Meta.db_table pinning

We **do not** let Django generate `<app>_<model>` table names. Every model's `Meta.db_table` is pinned to the exact MySQL table name from the legacy Go schema (`users`, `vibes`, `artworks`, `llm_usage`, `llm_jobs`, `llm_request_logs`, `site_content`, `app_settings`). Foreign keys to `User` use `db_constraint=False` because the legacy schema dropped FKs (charset/collation mismatch — see git history for the original comment in `internal/database/schema.go`).

**First deploy used `migrate --fake-initial`** — Django wrote `django_migrations` rows for every initial migration without re-creating the existing tables. From that point forward, schema changes flow through normal Django migrations. The legacy `internal/database/schema.go:EnsureSchema()` additive-ALTER pattern is **retired**; never reimplement it. The legacy `migrations/*.sql` files are gone — historical reference is in git.

### Auth

`apps.accounts.User` subclasses `AbstractBaseUser + PermissionsMixin`, mapped to the existing `users` table. The `password` field uses `db_column='password_hash'` so AbstractBaseUser's `set_password()`/`check_password()` work transparently against legacy bcrypt hashes.

**`PASSWORD_HASHERS` lists `BCryptPasswordHasher` first (NOT `BCryptSHA512PasswordHasher`)** — Django's default SHA512+bcrypt would silently reject every existing user. If you ever rewrite this section, keep that order.

Two cookies / two scopes:

| cookie       | claim required        | lifetime | used by                                      |
|--------------|-----------------------|----------|----------------------------------------------|
| x106_session | `user_id` (simplejwt) | 30 days  | /users/me, /journal/*, /artworks/*, /studio/* |
| x106_admin   | `role == "admin"`     | 8 hours  | /admin/content/*, /admin/art/*               |

Both signed with `JWT_SECRET` (HS256). Cookie reading lives in `apps.accounts.auth.JWTCookieAuthentication`; admin permission in `apps.core.permissions.IsAdminToken` (also accepts a Django staff session, so the `/admin/` UI gives you the same scope without a separate JWT).

**Admin authentication is via Django superuser** — `python manage.py createsuperuser` once, then log in with that username/password against `POST /api/v1/admin/login`. The legacy `ADMIN_USERNAME` / `ADMIN_PASSWORD_HASH` env vars are gone.

### LLM async pipeline (Celery)

Submit flow (`POST /api/v1/studio/llm/job`):
1. Reserve quota (`apps.studio.quota.reserve` — atomic INSERT…ON DUPLICATE KEY UPDATE on `llm_usage`).
2. Insert pending row into `llm_jobs`.
3. Enqueue `apps.studio.tasks.run_llm_job(job_id)` to Redis.
4. Return 202 + `{jobId, used, remaining, limit}`.

The Celery worker picks up the task, marks the row processing, calls DeepSeek via `apps.studio.services.deepseek.call_deepseek` (httpx streaming, 60s idle watchdog, retry once with stricter prompt + lower temperature), validates the scene with `apps.studio.services.scene.validate_and_clamp_scene`, writes `result_scene` and flips status to done. On failure: refunds quota + writes `error_message`. Hard caps: `time_limit=620`, `soft_time_limit=600` (Cloudflare-bypassing — the worker isn't behind the 100s edge ceiling).

`apps.studio.maintenance` runs two beat jobs:
- **recover_stale_jobs** every 60s — finds rows stuck in `processing` longer than 720s, marks them failed + refunds quota.
- **cleanup_old_jobs** every 30 min — deletes terminal rows older than 24h.

### Artwork validation

Ported byte-for-byte from `internal/handler/artwork.go` into `apps/studio/serializers.py`:

- `title ≤ 80`, `prompt ≤ 180`, `style ≤ 40`, `palette ≤ 60`, `kind ≤ 24`, `source_id ≤ 80` (rune-aware via Python `len()` on `str`).
- `settings ≤ 4 KB`, `scene ≤ 16 KB` — must be JSON **objects** (`dict`).
- `thumbnail_data_url ≤ 520 KB`, `asset_data_url ≤ 900 KB`, must start with `data:image/webp;base64,` or `data:image/jpeg;base64,`.
- `kind ∈ {favorite, upload, snapshot}`.

DRF rejects unknown fields by default at the serializer level when used with explicit `fields = [...]` — no DisallowUnknownFields equivalent needed.

### Routes (mounted under `/api/v1`)

Public: `GET /health`, `POST /auth/{register,login,logout}`, `GET /content/{app}/{section}`, `POST /admin/{login,logout}`.

User-auth (cookie x106_session OR Bearer): `GET /users/me`, `GET|POST /journal/vibes`, `GET /journal/vibes/today`, `GET /journal/vibes/stats`, `GET|POST /artworks`, `GET|DELETE /artworks/{id}`, `GET /studio/llm/quota`, `POST /studio/llm/job`, `GET /studio/llm/job/{id}`, `POST /studio/llm/job/{id}/cancel`.

Admin-auth (cookie x106_admin OR Bearer with `role:admin`): `GET /admin/content/{app}`, `PUT /admin/content/{app}/{section}`, plus the full `admin/art/*` family (users quota, llm-prompt, settings, stats, logs, jobs).

OpenAPI schema: `/api/schema/`. Swagger UI: `/api/docs/`.

### CORS

Allowed origins live in `x106/settings/base.py:CORS_ALLOWED_ORIGINS` (the five prod subdomains + localhost:3000–3004). Dev mode (`x106.settings.dev`) sets `CORS_ALLOW_ALL_ORIGINS = True`. `CORS_ALLOW_CREDENTIALS = True` is always on — frontend `fetch` must use `credentials: 'include'`.

### Greenfield API differences from the Go service

- **No sync `/studio/llm/{random,polish,remix}`.** The frontend submits a job and polls — only one path (the `/job` family) handles LLM calls.
- **All admin routes live under DRF ViewSets** with `@action`s; no hand-rolled chi route table.
- **Django `/admin/` UI is enabled** (`/admin/`) — staff users get a free dashboard for editing site_content, viewing artworks, reading LLM logs.
- **Pagination on admin list endpoints** is via `LimitOffsetPagination` (default 50, max 200) — query params `?limit=&offset=` are unchanged.
