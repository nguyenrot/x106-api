# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

This is the **Python (Django + DRF + Celery)** API backend for the X106 ecosystem. Port 4000, served at `api.kynguyen.cc`. The repo lives at `/var/www/api` on the VPS and runs as the systemd unit `x106-api.service`. Three siblings — `x106-celery-worker.service` (cafe review agent), `x106-celery-beat.service` (its 08:30 schedule), `x106-terminal-ws.service` (WebSocket bridge on `127.0.0.1:7682` that attaches the admin console's xterm.js to a tmux session; setup in `infra/terminal-ws-setup.md`) and `x106-tmux.service` (the tmux server holding those shells — deliberately **not** restarted by deploys) — replace the old Go `x106-worker` + ttyd combo. The parent ecosystem doc is at `../CLAUDE.md` — read it first for context on the surrounding apps and shared design system.

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

`/var/www/api/.env` contains the runtime env (DJANGO_SECRET_KEY, DB_*, JWT_SECRET, COOKIE_DOMAIN=.kynguyen.cc, REDIS_URL=redis://127.0.0.1:6379/0, etc.). `GEMINI_API_KEY` / `CONSOLE_SSH_*` may still be in there — leftovers of the removed AI chat, read by nothing.

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
  vandao/       # Cloud save for the Vấn Đạo game — one JSON blob per player, revision-guarded
  ops/          # admin console cockpit: /admin/ops/{overview,terminals,snippets,services/action} — host metrics, tmux sessions, saved snippets (table ops_prefs), restart/reload/start of pm2 apps + systemd units
terminal_ws/    # Standalone async daemon — WebSocket bridge (`/terminal/ws?s=<session>`) that runs `tmux new-session -A` in a PTY. Runs as systemd `x106-terminal-ws` (User=root, :7682). Auth via x106_admin JWT cookie. The shells themselves live in `x106-tmux` (socket /run/x106-console/tmux.sock, config terminal_ws/tmux.conf), so reloads, network drops and deploys only detach — sessions keep running. Root shells, no sandbox. Setup in infra/terminal-ws-setup.md.
infra/systemd/  # production unit files (x106-api, x106-celery-worker, x106-celery-beat, x106-terminal-ws, x106-tmux)
.github/workflows/deploy.yml
deploy.sh
```

To add a feature: pick the right app, drop a model into `models.py`, a serializer into `serializers.py`, a ViewSet/APIView into `views.py`, register on the router in `urls.py`, then `python manage.py makemigrations` + `migrate`. The app is registered in `INSTALLED_APPS` in `x106/settings/base.py`.

### Database — Meta.db_table pinning

We **do not** let Django generate `<app>_<model>` table names. Every model's `Meta.db_table` is pinned to the exact MySQL table name. Legacy ones came from the Go schema (`users`, `vibes`, `site_content`); new ones (`ops_prefs`, `cafe_*`) are plain snake_case Django-owned. Foreign keys to `User` use `db_constraint=False` because the legacy schema dropped FKs (charset/collation mismatch — see git history for the original comment in `internal/database/schema.go`).

The old AI-art stack (`artworks`, `llm_jobs`, `llm_usage`, `llm_request_logs`, `llm_conversations*`, `llm_models`, `llm_prompt_versions`, `app_settings`, `app_setting_changes`) was torn down on 2026-05-22 by `apps/core/migrations/0001_drop_legacy_ai.py`. `apps.console` (the Gemini-based AI ops chat that replaced it) and `apps.quotes` (quotes.kynguyen.cc, retired 2026-09-28) followed on 2026-10-04 via `0002_drop_console_quotes.py` — backup at `/root/backups/console-quotes-before-removal-20261004.sql.gz` on the VPS. The only AI left in this API is the cafe agent shelling out to the agy CLI.

**First deploy used `migrate --fake-initial`** — Django wrote `django_migrations` rows for every initial migration without re-creating the existing tables. From that point forward, schema changes flow through normal Django migrations. The legacy `internal/database/schema.go:EnsureSchema()` additive-ALTER pattern is **retired**; never reimplement it. The legacy `migrations/*.sql` files are gone — historical reference is in git.

### Auth

`apps.accounts.User` subclasses `AbstractBaseUser + PermissionsMixin`, mapped to the existing `users` table. The `password` field uses `db_column='password_hash'` so AbstractBaseUser's `set_password()`/`check_password()` work transparently against legacy bcrypt hashes.

**`PASSWORD_HASHERS` lists `BCryptPasswordHasher` first (NOT `BCryptSHA512PasswordHasher`)** — Django's default SHA512+bcrypt would silently reject every existing user. If you ever rewrite this section, keep that order.

Two cookies / two scopes:

| cookie       | claim required        | lifetime | used by                                      |
|--------------|-----------------------|----------|----------------------------------------------|
| x106_session | `user_id` (simplejwt) | 30 days  | /users/me, /journal/*, /ledger/*             |
| x106_admin   | `role == "admin"`     | 8 hours  | /admin/content/*, /admin/ops/*               |

Both signed with `JWT_SECRET` (HS256). Cookie reading lives in `apps.accounts.auth.JWTCookieAuthentication`; admin permission in `apps.core.permissions.IsAdminToken` (also accepts a Django staff session, so the `/admin/` UI gives you the same scope without a separate JWT).

**Admin authentication is via Django superuser** — `python manage.py createsuperuser` once, then log in with that username/password against `POST /api/v1/admin/login`. The legacy `ADMIN_USERNAME` / `ADMIN_PASSWORD_HASH` env vars are gone.

**Sign in with Google** (`apps.accounts.google`, `POST /auth/google`) — the browser runs Google's popup code flow and posts a one-shot authorization code; the server exchanges it for an `id_token` using `GOOGLE_OAUTH_CLIENT_SECRET` and reads the identity out of that. Ported from the same flow in lumi-backend, so keep the two in sync when either changes.

- The `id_token` signature is **deliberately not verified** — we fetched the token ourselves over TLS from Google's token endpoint (OIDC Core §3.1.3.7 exempts exactly this case), which is what keeps `cryptography`/`google-auth` out of the dependency list. `iss`/`aud`/`exp`/`email_verified` **are** checked, because those catch the failure that actually happens: a swapped or misconfigured OAuth client. If this ever moves to One-Tap, where the *browser* hands us a credential, full signature verification becomes mandatory.
- Account resolution: `users.google_sub` (migration `accounts.0004`) → verified email → new account with an unusable password. Matching by email is required, not optional: without it every player who already has a username/password account would end up with a second one.
- `users.email` has **no unique constraint** (it predates Django and is NULL for most rows), so an address matching two rows is refused rather than resolved to an arbitrary one.
- One OAuth client is shared by every X106 frontend: authorized JavaScript origins list each frontend, and there must be **no** authorized redirect URI (the popup flow exchanges with `redirect_uri=postmessage`). Blank env → `503`, and frontends hide the button.
- Throttled at `30/hour` per IP via `ScopedRateThrottle` (scope `auth_google`) — every call spends an outbound request to Google.


### Routes (mounted under `/api/v1`)

Public: `GET /health`, `POST /auth/{register,login,google,logout}`, `GET /content/{app}/{section}`, `POST /admin/{login,logout}`.

User-auth (cookie x106_session OR Bearer): `GET /users/me`, `GET|PUT /vandao/save`, `GET|POST /journal/vibes`, `GET /journal/vibes/today`, `GET /journal/vibes/stats`, plus `/ledger/*`.

Admin-auth (cookie x106_admin OR Bearer with `role:admin`):
- `GET /admin/content/{app}`, `PUT /admin/content/{app}/{section}`
- `GET /admin/users`, `POST /admin/users/{id}/{activate|deactivate}`, `DELETE /admin/users/{id}`
- Ops cockpit (admin console): `GET /admin/ops/overview` (host + pm2 + systemd, cached 5s — systemd units listed in `apps/ops/services/host.py:SYSTEMD_UNITS`, add new backends there), `GET /admin/ops/terminals`, `PATCH|DELETE /admin/ops/terminals/{name}`, `GET|PUT /admin/ops/snippets`, `POST /admin/ops/services/action` (`{kind: pm2|systemd, name, action: restart|reload|start, force?}` — names must be known to the cockpit; `x106-tmux` needs `force` because it kills every terminal; `x106-api` and pm2 `admin-pkn` run detached via `systemd-run` since they serve the request itself → 202)
- Cafe: CRUD `GET|POST|PATCH|DELETE /admin/cafe/reviews[/{id}]`, `POST /admin/cafe/uploads/image`; **review agent** `POST|GET /admin/cafe/agent/runs[/{id}]` (POST tạo run → Celery `apps.cafe.tasks.run_cafe_agent_now`; client poll). Agent tự chạy 08:30 VN qua beat (`apps.cafe.tasks.generate_cafe_review`), gate env `CAFE_AGENT_ENABLED` (+`CAFE_AGENT_MIN_CONFIDENCE`); pipeline ở `apps/cafe/agent/` (agy CLI web search → validate giọng tổng-hợp/dedup-slug → Nominatim geocode → đăng qua `CafeReviewWriteSerializer`); audit `cafe_agent_runs`. Test: `manage.py run_cafe_agent --dry-run --force`.

OpenAPI schema: `/api/schema/`. Swagger UI: `/api/docs/`.

### CORS

Allowed origins live in `x106/settings/base.py:CORS_ALLOWED_ORIGINS` (the five prod subdomains + localhost:3000–3004). Dev mode (`x106.settings.dev`) sets `CORS_ALLOW_ALL_ORIGINS = True`. `CORS_ALLOW_CREDENTIALS = True` is always on — frontend `fetch` must use `credentials: 'include'`.

### Notes

- **All admin routes live under DRF ViewSets** with `@action`s; no hand-rolled route table.
- **Django `/admin/` UI is enabled** (`/admin/`) — staff users get a free dashboard for editing site_content, users, etc.
- **Pagination on admin list endpoints** is via `LimitOffsetPagination` (default 50, max 200) — query params `?limit=&offset=` are unchanged.
