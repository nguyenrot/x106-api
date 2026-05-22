# VPS Console — one-time infrastructure setup

The `/api/v1/admin/console/` endpoints run shell commands on the VPS through
**SSH** to a dedicated low-privilege user (`x106-ops`), not via subprocess on
the `x106-api` service. The setup below is manual, runs once, and must be
finished **on the VPS** before the feature works (otherwise endpoints return
503 with a clear error).

Do everything as `root` on the VPS unless noted.

## 1. Create the `x106-ops` Linux user

```bash
useradd -m -s /bin/bash x106-ops
sudo -u x106-ops mkdir -p /home/x106-ops/.ssh
chmod 700 /home/x106-ops/.ssh
chown -R x106-ops:x106-ops /home/x106-ops/.ssh
```

The user has no special privileges. Read access to the parts of `/var/www/`
the console will inspect is controlled by file permissions.

## 2. Generate an ed25519 key pair

Run **on your laptop** (not the VPS) so the private key never touches a remote
shell history:

```bash
ssh-keygen -t ed25519 -f ./console_ssh_key -N "" -C "x106-console"
```

This produces `console_ssh_key` (private) + `console_ssh_key.pub` (public).

## 3. Install the public key on the VPS

```bash
# On laptop:
scp console_ssh_key.pub root@82.197.69.172:/tmp/

# On VPS:
cat /tmp/console_ssh_key.pub > /home/x106-ops/.ssh/authorized_keys
chmod 600 /home/x106-ops/.ssh/authorized_keys
chown x106-ops:x106-ops /home/x106-ops/.ssh/authorized_keys
rm /tmp/console_ssh_key.pub
```

## 4. Install the private key for the `x106-api` service

```bash
# On laptop:
scp console_ssh_key root@82.197.69.172:/tmp/

# On VPS:
mkdir -p /etc/x106
chmod 750 /etc/x106
mv /tmp/console_ssh_key /etc/x106/console_ssh_key
# x106-api runs as root per infra/systemd/x106-api.service, so root owns it.
# If you later move the service to a dedicated user, chown to that user.
chmod 600 /etc/x106/console_ssh_key
chown root:root /etc/x106/console_ssh_key
```

## 5. Sudoers whitelist (read-only ops only)

The AI assistant will sometimes need to `sudo` to inspect systemd / pm2.
Whitelist *only* the read-only verbs — never anything that mutates state.

```bash
cat > /etc/sudoers.d/x106-ops <<'EOF'
# Read-only inspection for the AI ops console. Update with care.
x106-ops ALL=(ALL) NOPASSWD: /bin/systemctl status *, /usr/bin/systemctl status *
x106-ops ALL=(ALL) NOPASSWD: /usr/bin/journalctl --no-pager *, /bin/journalctl --no-pager *
x106-ops ALL=(ALL) NOPASSWD: /usr/bin/pm2 list, /usr/bin/pm2 describe *, /usr/bin/pm2 logs *
EOF
chmod 440 /etc/sudoers.d/x106-ops
visudo -c   # MUST print "/etc/sudoers: parsed OK"
```

Any restart / stop / disable command must come from a fully-typed Approve
flow in the UI — it cannot ride along on this whitelist.

## 6. Get an OpenCode Zen API key

Sign up at <https://opencode.ai/> and grab a Zen-tier key. The free models
the console allowlists (`deepseek-v4-flash-free`, `big-pickle`,
`qwen-3.6-plus-free`, `minimax-m2.5-free`) don't bill against credit, but a
key is still required to call the endpoint.

## 7. Add env vars to `/var/www/api/.env`

Append:

```ini
OPENCODE_ZEN_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxx
OPENCODE_ZEN_BASE_URL=https://opencode.ai/zen/v1
CONSOLE_SSH_HOST=127.0.0.1
CONSOLE_SSH_PORT=22
CONSOLE_SSH_USER=x106-ops
CONSOLE_SSH_KEY_PATH=/etc/x106/console_ssh_key
```

## 8. Restart the API + workers

```bash
systemctl daemon-reload   # only needed if you edited the unit file
systemctl restart x106-api x106-celery-worker x106-celery-beat
```

## 9. Smoke test

```bash
# From your laptop, with an admin JWT:
TOKEN=$(curl -s https://api.kynguyen.cc/api/v1/admin/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"<admin>","password":"<pwd>"}' | jq -r .token)

curl -fsS https://api.kynguyen.cc/api/v1/admin/console/sessions \
  -H "Authorization: Bearer $TOKEN"
# → [] (empty list, status 200)

curl -fsS https://api.kynguyen.cc/api/v1/admin/console/settings \
  -H "Authorization: Bearer $TOKEN"
# → JSON with enabled:true, ai_model, allowed_models, …
```

Then open <https://admin.kynguyen.cc/dashboard/console>, create a session,
type `$ df -h` and confirm output renders.

## Rollback

Delete `/etc/sudoers.d/x106-ops`, remove `console_ssh_key`, and unset the
env vars. The feature degrades to 503 cleanly.
