# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

This is the **Python (Django + DRF + Celery)** API backend for the X106 ecosystem. Port 4000, served at `api.kynguyen.cc`. The repo lives at `/var/www/api` on the VPS and runs as the systemd unit `x106-api.service`. Two siblings — `x106-celery-worker.service` (AI ops chat + SSH exec) and `x106-celery-beat.service` (recovery + cleanup schedule) — replace the old Go `x106-worker`. The parent ecosystem doc is at `../CLAUDE.md` — read it first for context on the surrounding apps and shared design system.

The Go service was rewritten to Python on 2026-05-09. Old Go source lives in git history under tags `pre-python-rewrite` and earlier — use `git log --oneline -- cmd/ internal/` if you need archaeology.

## Commands

```bash
uv sync                                          # install deps
uv run python manage.py runserver 4000           # dev server
uv run python manage.py migrate                  # apply migrations
uv run python manage.py createsuperuser          # admin user (replaces ADMIN_USERNAME / ADMIN_PASSWORD_HASH env)
uv run python manage.py shell                    # ORM shell
uv run celery -A x106 worker -l info             # local Celery worker (needs Redis on :6379)
uv run celery -A x106 beat -l info               # local scheduler (60s recovery, 1h cleanup)
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

`/var/www/api/.env` contains the runtime env (DJANGO_SECRET_KEY, DB_*, JWT_SECRET, COOKIE_DOMAIN=.kynguyen.cc, REDIS_URL=redis://127.0.0.1:6379/0, etc.). The admin console AI no longer needs an API key — it shells out to the `agy` Antigravity CLI installed under root's home. See `infra/console-setup.md` for the console-specific bits.

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
  core/         # health endpoint, tz helpers (Asia/Ho_Chi_Minh), id generator, IsAdminToken, legacy-table-drop migration
  accounts/    # User on `users` table, JWTCookieAuthentication, login/logout/register/admin views
  journal/      # Vibe + VibeViewSet (today, stats, upsert)
  ledger/       # personal finance (transactions, categories, budgets)
  content/      # SiteContent (public + admin upsert)
  console/      # VPS console + AI ops assistant (OpenCode Zen + paramiko SSH); see infra/console-setup.md
infra/systemd/  # production unit files (x106-api, x106-celery-worker, x106-celery-beat)
infra/console-setup.md  # one-time x106-ops user + ssh key + sudoers setup on VPS
.github/workflows/deploy.yml
deploy.sh
```

To add a feature: pick the right app, drop a model into `models.py`, a serializer into `serializers.py`, a ViewSet/APIView into `views.py`, register on the router in `urls.py`, then `python manage.py makemigrations` + `migrate`. The app is registered in `INSTALLED_APPS` in `x106/settings/base.py`.

### Database — Meta.db_table pinning

We **do not** let Django generate `<app>_<model>` table names. Every model's `Meta.db_table` is pinned to the exact MySQL table name. Legacy ones came from the Go schema (`users`, `vibes`, `site_content`); new ones (`console_*`, `ledger_*`) are plain snake_case Django-owned. Foreign keys to `User` use `db_constraint=False` because the legacy schema dropped FKs (charset/collation mismatch — see git history for the original comment in `internal/database/schema.go`).

The old AI-art stack (`artworks`, `llm_jobs`, `llm_usage`, `llm_request_logs`, `llm_conversations*`, `llm_models`, `llm_prompt_versions`, `app_settings`, `app_setting_changes`) was torn down on 2026-05-22 by `apps/core/migrations/0001_drop_legacy_ai.py`. The AI capability now lives entirely in `apps.console` against OpenCode Zen free-tier models.

**First deploy used `migrate --fake-initial`** — Django wrote `django_migrations` rows for every initial migration without re-creating the existing tables. From that point forward, schema changes flow through normal Django migrations. The legacy `internal/database/schema.go:EnsureSchema()` additive-ALTER pattern is **retired**; never reimplement it. The legacy `migrations/*.sql` files are gone — historical reference is in git.

### Auth

`apps.accounts.User` subclasses `AbstractBaseUser + PermissionsMixin`, mapped to the existing `users` table. The `password` field uses `db_column='password_hash'` so AbstractBaseUser's `set_password()`/`check_password()` work transparently against legacy bcrypt hashes.

**`PASSWORD_HASHERS` lists `BCryptPasswordHasher` first (NOT `BCryptSHA512PasswordHasher`)** — Django's default SHA512+bcrypt would silently reject every existing user. If you ever rewrite this section, keep that order.

Two cookies / two scopes:

| cookie       | claim required        | lifetime | used by                                      |
|--------------|-----------------------|----------|----------------------------------------------|
| x106_session | `user_id` (simplejwt) | 30 days  | /users/me, /journal/*, /ledger/*             |
| x106_admin   | `role == "admin"`     | 8 hours  | /admin/content/*, /admin/console/*           |

Both signed with `JWT_SECRET` (HS256). Cookie reading lives in `apps.accounts.auth.JWTCookieAuthentication`; admin permission in `apps.core.permissions.IsAdminToken` (also accepts a Django staff session, so the `/admin/` UI gives you the same scope without a separate JWT).

**Admin authentication is via Django superuser** — `python manage.py createsuperuser` once, then log in with that username/password against `POST /api/v1/admin/login`. The legacy `ADMIN_USERNAME` / `ADMIN_PASSWORD_HASH` env vars are gone.

### VPS console + AI ops assistant (`apps.console`)

The AI surface in the ecosystem. AI calls shell out to the **`agy` Antigravity CLI** (Google) at `/root/.local/bin/agy` as a subprocess from `x106-celery-worker` (which runs as root). agy authenticates via Google OAuth, executes shell commands / reads files / edits code autonomously — no human-approve gate, no ConsoleExec rows, no run_shell tool.

Three tables: `console_settings`, `console_sessions`, `console_messages`. Lifecycle per chat turn:

```
user message → ConsoleMessage (role=user, done)
             + ConsoleMessage (role=assistant, pending)
             → run_console_chat (Celery)
                    ↓
             apps.console.services.agy.run_agy(prompt)
                    ↓ subprocess `agy --print --print-timeout 3m30s "<full prompt>"`
             agy autonomously runs lệnh, đọc file, …
                    ↓
             stdout text → ConsoleMessage.content + status=done
```

agy's `--print` mode is **stateless**, so `_build_prompt` in `apps/console/tasks.py` concatenates `console.system_prompt` + the last 12 ConsoleMessage rows into a single string each turn. agy's own settings.json must set `"toolPermission": "always-proceed"` (see infra/console-setup.md) or it will hang waiting for a TTY-only permission prompt.

Hard caps: per-task Celery `time_limit=300/soft=270`; agy's own `--print-timeout=3m30s`; outer `subprocess.run(..., timeout=240)`. Beat task: `cleanup_old_messages` (1h, drops assistant messages >60 days old).

Security: agy runs with root on the VPS. There is no whitelist, danger classifier, or approval flow. Any user prompt can become any command — trust the model + be careful with phrasing. To disable temporarily: `UPDATE console_settings SET value='false' WHERE \`key\`='console.enabled'`.

One-time setup (install agy, OAuth sign-in, verify always-proceed) lives in `infra/console-setup.md`. agy missing or returning an error → endpoint returns the failure inline; the admin UI shows a friendly Vietnamese message + Retry button.

### Routes (mounted under `/api/v1`)

Public: `GET /health`, `POST /auth/{register,login,logout}`, `GET /content/{app}/{section}`, `POST /admin/{login,logout}`.

User-auth (cookie x106_session OR Bearer): `GET /users/me`, `GET|POST /journal/vibes`, `GET /journal/vibes/today`, `GET /journal/vibes/stats`, plus `/ledger/*`.

Admin-auth (cookie x106_admin OR Bearer with `role:admin`):
- `GET /admin/content/{app}`, `PUT /admin/content/{app}/{section}`
- `GET /admin/users`, `POST /admin/users/{id}/{activate|deactivate}`, `DELETE /admin/users/{id}`
- VPS console: `GET|POST|DELETE /admin/console/sessions[/{id}]`, `POST /admin/console/sessions/{id}/messages`, `GET /admin/console/messages/{id}`, `GET /admin/console/execs/{id}`, `POST /admin/console/execs/{id}/{approve,deny,cancel,explain}`, `GET /admin/console/logs`, `GET|PUT /admin/console/settings`

OpenAPI schema: `/api/schema/`. Swagger UI: `/api/docs/`.

### CORS

Allowed origins live in `x106/settings/base.py:CORS_ALLOWED_ORIGINS` (the five prod subdomains + localhost:3000–3004). Dev mode (`x106.settings.dev`) sets `CORS_ALLOW_ALL_ORIGINS = True`. `CORS_ALLOW_CREDENTIALS = True` is always on — frontend `fetch` must use `credentials: 'include'`.

### Notes

- **All admin routes live under DRF ViewSets** with `@action`s; no hand-rolled route table.
- **Django `/admin/` UI is enabled** (`/admin/`) — staff users get a free dashboard for editing site_content, console settings, etc.
- **Pagination on admin list endpoints** is via `LimitOffsetPagination` (default 50, max 200) — query params `?limit=&offset=` are unchanged.
