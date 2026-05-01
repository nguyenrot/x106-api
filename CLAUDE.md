# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

This is the Go API backend for the X106 ecosystem (port 4000, served at `api.pkn.io.vn`). The repo lives at `/var/www/api` on the VPS and runs as the systemd unit `x106-api.service`. The parent ecosystem doc is at `../CLAUDE.md` — read it first for context on the surrounding apps and shared design system.

## Commands

```bash
make dev      # air hot-reload
make run      # go run ./cmd/server
make build    # go build -o bin/x106-api ./cmd/server
make test     # go test ./...   (currently no test files, but the deploy script gates on this passing)
make clean    # rm -rf bin
```

`make migrate` only seeds `migrations/001_init.sql` against a local MySQL — it is **not** a full migration runner.

### Deploy

`./deploy.sh [ref]` runs *on the VPS* (invoked remotely by `../deploy.sh api`). It pulls `origin/main` (or the supplied ref), runs `go test ./...`, builds `x106-api`, atomically swaps the binary, and restarts `x106-api.service`. It then health-checks `https://api.pkn.io.vn/api/v1/health` before exiting.

### Migrations on the VPS

There is no `mysql` client on the host. MySQL runs inside the Docker container `finance-server-mysql-1`. To apply a SQL file:

```bash
docker cp /tmp/migration.sql finance-server-mysql-1:/tmp/migration.sql
docker exec finance-server-mysql-1 mysql -u finance_user -p'<password>' finance_app -e 'source /tmp/migration.sql'
```

Note the DB-name asymmetry: local config defaults to `DB_NAME=x106` and most migration files start with `USE x106;`, but the production VPS database is `finance_app` (and `003_site_content.sql` already uses `USE finance_app;`). When applying a migration on prod, make sure the `USE` statement matches the target DB.

## Architecture

### Layering

```
cmd/server/main.go            # router + route registration + graceful shutdown
internal/config/config.go     # env loading (SERVER_PORT, DB_*, JWT_SECRET, COOKIE_DOMAIN, ENV, ADMIN_USERNAME, ADMIN_PASSWORD_HASH)
internal/database/mysql.go    # global database.DB *sql.DB + Connect/Close
internal/database/schema.go   # idempotent EnsureSchema() — see below
internal/model/               # request/response/db structs (user, vibe, content, artwork)
internal/service/             # business logic; owns sentinel errors (ErrUserExists, ErrInvalidCreds, ErrUserNotFound, ErrArtworkNotFound, ErrContentNotFound)
internal/handler/             # HTTP edge: decode JSON → validate → call service → writeJSON; map sentinel errors to status codes
internal/middleware/          # cors, logger, Auth (user JWT), AdminAuth (admin JWT)
migrations/                   # SQL files (manually applied — not auto-run by deploy)
```

Handler pattern is uniform across `auth.go` / `journal.go` / `artwork.go` / `content.go` / `admin.go`. `GetUserID(r)` reads the user id injected by `core.Auth`. Use `writeJSON(w, status, body)` for all responses.

To add a feature: create `model/foo.go`, `service/foo.go`, `handler/foo.go`, then register routes in `cmd/server/main.go` — wrap protected ones in `r.Group(func(r chi.Router) { r.Use(core.Auth(cfg)); ... })` (or `core.AdminAuth(cfg)` for admin-only endpoints).

### Schema bootstrap (important)

`database.EnsureSchema()` runs on every startup and is the **actual** migration mechanism for the `users` and `artworks` tables in production. It:

1. `CREATE TABLE IF NOT EXISTS users (...)` and additively `ALTER TABLE` to add any missing columns from `ensureUserColumns`.
2. If a legacy `journal_users` table exists, copies compatible rows into `users` (idempotent, with `ON DUPLICATE KEY UPDATE`).
3. Same pattern for `artworks` via `ensureArtworksTable` / `ensureArtworkColumns`.

When evolving the `users` or `artworks` schema, prefer adding to the `alterations` slice in `internal/database/schema.go` so existing deployments self-upgrade. SQL files in `migrations/` are reference/bootstrap material; the deploy script does **not** apply them. Tables not covered by `EnsureSchema()` (e.g. `vibes`, `site_content`) must be migrated manually with the `docker cp` / `docker exec` recipe above.

### Auth model

Two parallel JWT systems sharing `JWT_SECRET`:

| middleware | cookie | header | claims required | duration | used by |
|---|---|---|---|---|---|
| `core.Auth(cfg)` | `x106_session` | `Authorization: Bearer …` | `user_id` (string) | 30 days | `/users/me`, `/journal/*`, `/artworks/*` |
| `core.AdminAuth(cfg)` | `x106_admin` | `Authorization: Bearer …` | `role == "admin"` | 8 hours | `/admin/content/*` (write) |

Both extract from cookie first, then fall back to the `Bearer` header. Cookies are issued with `Domain=COOKIE_DOMAIN` (`.pkn.io.vn` in prod, `localhost` in dev) and `Secure=true` whenever `ENV=production`.

Admin login (`POST /api/v1/admin/login`) authenticates against `ADMIN_USERNAME` + bcrypt-compared `ADMIN_PASSWORD_HASH` from env — there is no admin user row in the DB.

### IDs and the Register fallback

`service.newID()` mints UUIDv4 values in Go because the schema has no default for `id`. `service.Register` first tries `INSERT (username, password_hash)` and, on error, retries with an explicit `(id, username, password_hash)` — this tolerates both schema variants (with/without an auto default on `id`). Mirror this pattern when inserting into other tables that have a `VARCHAR(36)` primary key.

### Artwork validation (from `internal/handler/artwork.go`)

Caps enforced before reaching the service layer:

- `title ≤ 80`, `prompt ≤ 180`, `style ≤ 40`, `palette ≤ 60`, `kind ≤ 24`, `source_id ≤ 80` (rune-aware truncation, not byte-truncation)
- `settings_json ≤ 4 KB`, `scene_json ≤ 16 KB` — must be valid JSON **objects** (must start with `{`)
- `thumbnail_data_url ≤ 520 KB`, `asset_data_url ≤ 900 KB` — must start with `data:image/webp;base64,` or `data:image/jpeg;base64,`
- `kind ∈ {favorite, upload, snapshot}`

`json.NewDecoder(...).DisallowUnknownFields()` is used on create — adding a field to `model.CreateArtworkRequest` is required before clients can send it.

### Routes (mounted under `/api/v1`)

Public: `GET /health`, `POST /auth/{register,login,logout}`, `GET /content/{app}/{section}`, `POST /admin/{login,logout}`.

User-auth (`core.Auth`): `GET /users/me`, `GET|POST /journal/vibes`, `GET /journal/vibes/today`, `GET /journal/vibes/stats`, `GET|POST /artworks`, `GET|DELETE /artworks/{id}`.

Admin-auth (`core.AdminAuth`): `GET /admin/content/{app}`, `PUT /admin/content/{app}/{section}`.

### CORS

Allowed origins are hardcoded in `internal/middleware/cors.go` (the five prod subdomains + `localhost:3000–3003`). When adding a new frontend origin, update that map. `Access-Control-Allow-Credentials: true` is always set, so the frontend `fetch` must use `credentials: 'include'` for cookie auth.
