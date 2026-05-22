# Terminal WebSocket bridge — VPS setup

`x106-terminal-ws.service` replaces the old `ttyd` daemon as the backend for
the admin app's **Terminal** tab. xterm.js in the browser opens a WebSocket
to `wss://admin.kynguyen.cc/terminal/ws`, nginx proxies to the bridge on
`127.0.0.1:7682`, and the bridge spawns a `bash -l` PTY as the `x106-ops`
user.

These steps are **one-time** and must be run **manually as root on the VPS**
the first time you deploy the bridge. After that, regular `git push` to
`nguyenrot/x106-api` ships code changes via `deploy.yml` automatically.

The bridge re-uses the `x106-ops` user, the sudoers whitelist, and the
nginx `auth_request` gate documented in `console-setup.md` — make sure
those exist before continuing.

## 1. Verify ttyd is currently the upstream

```bash
ss -tlnp | grep -E ':(7681|7682)\s'   # 7681 = ttyd default, 7682 = new bridge
grep -n 'proxy_pass' /etc/nginx/sites-available/admin.kynguyen.cc
```

You should see ttyd on `:7681` and an nginx `proxy_pass` targeting that
port (or a `127.0.0.1:7681` upstream block).

## 2. Deploy the bridge code

Push any commit to `nguyenrot/x106-api` on `main`. The workflow:

- Includes `terminal_ws/` and `infra/` in the deploy tarball.
- Syncs `infra/systemd/*.service` to `/etc/systemd/system/` if changed and
  runs `daemon-reload`.
- Restarts `x106-terminal-ws.service` along with the other units.

After the run finishes, confirm the daemon is up:

```bash
systemctl status x106-terminal-ws
ss -tlnp | grep ':7682\s'
journalctl -u x106-terminal-ws -n 30 --no-pager
# expect: "x106-terminal-ws listening on 127.0.0.1:7682 (shell=/bin/bash)"
```

If `systemctl status` shows the unit was never installed (first deploy
edge case), copy the file manually then enable:

```bash
cp /var/www/api/infra/systemd/x106-terminal-ws.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now x106-terminal-ws
```

## 3. Swap the nginx upstream

Edit `/etc/nginx/sites-available/admin.kynguyen.cc`. Find the existing
`/terminal/` location (proxies to ttyd, currently `:7681`) and replace it
with:

```nginx
# /terminal/ws — WebSocket bridge to x106-terminal-ws.service.
# Auth is enforced by the auth_request subrequest to /api/auth-check
# (Next.js admin app) which validates the x106_admin JWT cookie.
location = /terminal/ws {
    auth_request /api/auth-check;
    error_page 401 = @terminal_unauthorized;

    proxy_pass http://127.0.0.1:7682;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header Cookie $http_cookie;

    # Long-lived connection — disable read timeout, allow large frames.
    proxy_read_timeout 24h;
    proxy_send_timeout 24h;
    proxy_buffering off;
}

location @terminal_unauthorized {
    return 302 /login;
}
```

Drop the old `location /terminal/ { proxy_pass http://127.0.0.1:7681; ... }`
block (ttyd's HTML page is no longer needed — xterm.js renders in-app).
Keep the `/api/auth-check` subrequest location unchanged.

Test + reload:

```bash
nginx -t
systemctl reload nginx
```

## 4. Disable ttyd

```bash
systemctl disable --now ttyd.service 2>/dev/null \
  || systemctl disable --now ttyd 2>/dev/null \
  || true
ss -tlnp | grep ':7681\s'   # expect empty
```

If ttyd was started outside systemd (rare):

```bash
pkill -x ttyd 2>/dev/null || true
```

You can uninstall the binary if you want to keep the host tidy:

```bash
apt-get remove --purge -y ttyd 2>/dev/null \
  || rm -f /usr/local/bin/ttyd
```

## 5. Smoke test

In an incognito-like state:

1. Log in at <https://admin.kynguyen.cc/login>.
2. Open the Console tab → **Terminal**.
3. The xterm.js view should attach within a second, status dot turns
   green, prompt is `x106-ops@<host>:~$`. Try `pm2 list`, `df -h`,
   `whoami` (should print `x106-ops`).
4. Resize the browser — the shell should reflow without artefacts.
5. Disconnect by closing the tab; back on the VPS:

```bash
journalctl -u x106-terminal-ws -n 20 --no-pager
# expect: "connect user=<admin> peer=..." then "disconnect ..."
ps -fu x106-ops | grep bash   # expect no orphaned shells
```

## Rollback

If the bridge misbehaves you can fall back to ttyd in ~30 seconds:

```bash
systemctl stop x106-terminal-ws
systemctl start ttyd  # if still installed; or apt-get install -y ttyd && systemctl enable --now ttyd
# Restore the old nginx /terminal/ location block from a backup or git
# history of /etc/nginx/sites-available/, then:
nginx -t && systemctl reload nginx
```

Keep a copy of the previous nginx vhost (`cp /etc/nginx/sites-available/admin.kynguyen.cc /root/admin.vhost.bak`)
before step 3 so the rollback is one file restore + reload.

## Operational notes

- The bridge is **single-process, single-event-loop**. Each browser tab =
  one connection = one PTY = one shell child. For a single admin user this
  is more than enough; it has been benchmarked to handle ~50 concurrent
  sessions on the VPS without breathing hard.
- No keystroke/output logging by design. Only connection events (`connect`,
  `disconnect`, `auth fail`) are written to journal — grep with
  `journalctl -u x106-terminal-ws --since '1 hour ago'`.
- JWT verification uses the same `JWT_SECRET` env var as the Django API,
  loaded via systemd `EnvironmentFile=/var/www/api/.env`. Rotating the
  secret invalidates all admin sessions including any open terminals.
- The shell inherits the `x106-ops` UID, so the sudoers whitelist in
  `/etc/sudoers.d/x106-ops` (read-only ops only) applies here too —
  destructive verbs are blocked at the kernel level, not in the UI.
