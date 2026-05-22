# VPS Console — one-time infrastructure setup

The `/api/v1/admin/console/` endpoints drive the **`agy` Antigravity CLI**
as a subprocess. `agy` runs **as root on the VPS itself** (the
`x106-celery-worker` systemd unit also runs as root, so no SSH or sudo
indirection is needed). agy handles shell execution, file reads, and code
edits autonomously inside its own process.

There is no human-in-the-loop approval gate. agy runs with root-level
access — assume any prompt can become any command.

## 1. Install agy on the VPS

```bash
# Run interactively in a TTY as root so the OAuth browser flow can complete.
curl -fsSL https://antigravity.google/install.sh | sh
# Or follow the official install instructions at
# https://github.com/google-antigravity/agy
```

After install, `agy` lives at `/root/.local/bin/agy`. Make sure that
directory is in root's `PATH` (the installer normally writes a shell
profile entry; the Celery worker uses the absolute path so this is just
for interactive convenience).

## 2. OAuth — sign in to a Google account

```bash
agy install   # follows the setup flow
agy --print "hello"   # confirms auth + first response
```

agy stores its OAuth token at `/root/.gemini/antigravity-cli/`. The
account used here is the one that pays for / has free-tier Gemini quota.

## 3. Confirm tool permission is auto-approve

```bash
cat /root/.gemini/antigravity-cli/settings.json
# Must contain: "toolPermission": "always-proceed"
```

If not, edit the file or run agy interactively once and accept "always
allow" — otherwise agy will hang waiting for TTY input inside the
subprocess and our outer timeout will kill it.

## 4. Verify headless invocation

The Celery worker calls agy via `subprocess.run([...])` with no TTY. Test
the same path:

```bash
echo "" | /root/.local/bin/agy --print --print-timeout 60s "free -h, df -h. Tóm tắt ngắn." 2>&1
# Expected: a short Vietnamese summary, exit code 0
```

If this returns "could not open TTY" or hangs, the always-proceed setting
is missing (see step 3).

## 5. Restart the API + workers

```bash
systemctl daemon-reload   # only needed if the unit file changed
systemctl restart x106-api x106-celery-worker x106-celery-beat
```

## 6. Smoke test from the admin UI

Open <https://admin.kynguyen.cc/dashboard/console>, create a session,
send "kiểm tra ram và disk". Within ~10–30s agy returns a single
markdown-formatted reply.

## Rollback

If something is wrong, disable the feature at the database level:

```bash
mysql -u root finance_app -e "
  UPDATE console_settings SET value='false' WHERE \`key\`='console.enabled';
"
```

The `/dashboard/console` chat then returns 503 cleanly until re-enabled.
