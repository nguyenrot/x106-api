"""Command danger classifier.

Pure-Python, no DB or network. Returns one of three levels:

- `safe`        — read-only / observational. Direct user commands auto-execute.
- `write`       — modifies state but reversible. Direct user commands run after
                  a single confirm click; AI-proposed commands always confirm.
- `destructive` — irreversible or load-bearing (restart, delete, mkfs,
                  destructive sql). Requires typing the destroy phrase.

Conservative defaults — anything not on the safe list is at least `write`.
"""

from __future__ import annotations

import re
from typing import Literal

DangerLevel = Literal["safe", "write", "destructive"]


# Regex tested against the FULL command string (so we can spot pipes, sudo
# prefixes, env-var assignments, etc.). `re.IGNORECASE | re.VERBOSE` everywhere.
_DESTRUCTIVE_PATTERNS: list[tuple[str, str]] = [
    (r"\brm\s+(-[a-z]*r[a-z]*f|-[a-z]*f[a-z]*r)\b", "rm -rf"),
    (r"\bdd\s+(if|of)=", "dd write"),
    (r"\bmkfs(\.\w+)?\b", "mkfs"),
    (r"\b(shutdown|reboot|halt|poweroff)\b", "host shutdown/reboot"),
    (r"\bsystemctl\s+(stop|disable|mask|restart)\b", "systemctl stop/disable/restart"),
    (r"\bpm2\s+(delete|kill|stop)\b", "pm2 delete/kill"),
    (r"\bkill\s+-9\b", "kill -9"),
    (r"\biptables\s+-[FX]\b", "iptables flush"),
    (r"\bufw\s+(disable|reset)\b", "ufw disable"),
    (r"\bapt(-get)?\s+(remove|purge|autoremove)\b", "apt remove/purge"),
    (r">\s*/etc/", "redirect to /etc/"),
    (r">\s*/boot/", "redirect to /boot/"),
    (r"\bchmod\s+777\b", "chmod 777"),
    (r"\bchown\s+-R\b", "chown -R"),
    (r"\btruncate\s+-s\s*0\s+/var/log", "truncate /var/log"),
    (r"\b(DROP|TRUNCATE)\s+(TABLE|DATABASE)\b", "sql DROP/TRUNCATE"),
    (r"\b:\(\)\{.*\}\s*;:", "fork bomb"),
    (r"\brm\s+-[a-z]*\s+/(\s|$)", "rm /"),
    (r">\s*/dev/sd", "write to raw disk"),
]


# Read-only command bases. The full command must start with one of these tokens
# (after stripping `sudo`/env-assignments) AND must not contain a redirection
# that writes to a file, OR a pipe to a destructive command. Conservative.
_SAFE_BASES = {
    "ls", "cat", "head", "tail", "less", "more",
    "df", "du", "free", "uptime", "uname", "hostname", "whoami", "id",
    "ps", "pgrep", "top",
    "ip", "ss", "netstat", "dig", "nslookup", "host", "ping",
    "curl", "wget",
    "git",
    "echo", "printenv", "env", "date",
    "wc", "sort", "uniq", "tr", "awk", "sed", "grep", "egrep", "fgrep", "rg",
    "find", "stat", "file", "which", "whereis", "type",
    "tree", "lsof",
    "systemctl",   # only safe verbs; see check below
    "journalctl",
    "pm2",         # only safe verbs; see check below
    "docker",      # only safe verbs; see check below
    "mysql",       # only SELECT/SHOW; see check below
    "redis-cli",   # only read commands; see check below
    "nginx",       # only -t / -s reload-with-care; see check below
}

_SYSTEMCTL_SAFE_VERBS = {"status", "is-active", "is-enabled", "list-units", "list-unit-files", "cat", "show"}
_PM2_SAFE_VERBS = {"list", "ls", "describe", "show", "logs", "monit", "prettylist", "jlist"}
_DOCKER_SAFE_VERBS = {"ps", "images", "logs", "inspect", "stats", "version", "info", "top"}
_MYSQL_SAFE_TOKENS = {"select", "show", "describe", "desc", "explain"}


def _strip_prefix_tokens(cmd: str) -> str:
    """Strip leading `sudo`, `time`, `nohup`, env-var assignments like
    `FOO=bar`. Returns the substring starting at the real binary name."""
    tokens = cmd.strip().split()
    while tokens:
        head = tokens[0]
        if head in {"sudo", "time", "nohup", "nice", "ionice"}:
            tokens.pop(0)
            # `sudo -E -u name` etc. — drop flag tokens until we hit a non-flag
            while tokens and tokens[0].startswith("-"):
                tokens.pop(0)
            continue
        if "=" in head and not head.startswith("=") and head.split("=", 1)[0].replace("_", "").isalnum():
            tokens.pop(0)
            continue
        break
    return " ".join(tokens)


def _has_write_redirect(cmd: str) -> bool:
    # `>` or `>>` writing to a file (anything but /dev/null and /dev/stdout/err).
    for m in re.finditer(r">>?\s*(\S+)", cmd):
        target = m.group(1)
        if target in {"/dev/null", "/dev/stderr", "/dev/stdout"}:
            continue
        return True
    return False


def classify(command: str) -> tuple[DangerLevel, list[str]]:
    """Return `(level, reasons)`. `reasons` is always a list (possibly empty)
    so the caller can show them in a confirm panel."""
    cmd = command.strip()
    if not cmd:
        return "safe", []

    reasons: list[str] = []

    # 1) Hard destructive matches — return immediately.
    for pattern, label in _DESTRUCTIVE_PATTERNS:
        if re.search(pattern, cmd, re.IGNORECASE):
            reasons.append(label)
    if reasons:
        return "destructive", reasons

    # 2) Look at the first real binary.
    stripped = _strip_prefix_tokens(cmd)
    tokens = stripped.split()
    if not tokens:
        return "write", ["unable to parse command"]
    binary = tokens[0]

    # If the binary itself is not on the safe list, it's write (or unknown ==
    # treat as write, the user/AI must confirm).
    if binary not in _SAFE_BASES:
        return "write", []

    # 3) Binary is safe-by-default, but specific tools have sub-verbs we have
    #    to check, and a write redirect bumps anything up to write.
    if _has_write_redirect(stripped):
        return "write", ["redirects output to file"]

    if binary == "systemctl":
        verb = (tokens[1] if len(tokens) > 1 else "").lower()
        if verb not in _SYSTEMCTL_SAFE_VERBS:
            return "write", [f"systemctl {verb!r} is not a read-only verb"]
    elif binary == "pm2":
        verb = (tokens[1] if len(tokens) > 1 else "").lower()
        if verb not in _PM2_SAFE_VERBS:
            return "write", [f"pm2 {verb!r} is not a read-only verb"]
    elif binary == "docker":
        verb = (tokens[1] if len(tokens) > 1 else "").lower()
        if verb not in _DOCKER_SAFE_VERBS:
            return "write", [f"docker {verb!r} is not a read-only verb"]
    elif binary == "mysql":
        # If the user passed `-e "..."`, classify the SQL inside.
        m = re.search(r"-e\s+([\"'])(.*?)\1", cmd, re.DOTALL)
        if m:
            first = m.group(2).strip().split()[:1]
            first_token = (first[0] if first else "").lower()
            if first_token not in _MYSQL_SAFE_TOKENS:
                return "write", [f"mysql -e {first_token!r} mutates data"]
        # Otherwise, interactive `mysql` is itself safe — the human typing
        # SQL in the repl is responsible.
    elif binary == "redis-cli":
        # Disallow obvious writes via -e/--eval or explicit SET/DEL after flags.
        flat = " ".join(t for t in tokens[1:] if not t.startswith("-"))
        first_token = (flat.split() or [""])[0].lower()
        if first_token in {"set", "del", "unlink", "flushdb", "flushall", "rename"}:
            return "write", [f"redis-cli {first_token!r} mutates state"]
    elif binary == "nginx":
        # `-t` is safe; `-s reload` modifies running state.
        if "-s" in tokens:
            return "write", ["nginx -s signals running master"]
    elif binary == "git":
        # Read-only verbs are fine, mutating ones bump to write.
        verb = (tokens[1] if len(tokens) > 1 else "").lower()
        if verb in {"push", "reset", "clean", "rebase", "checkout", "branch", "tag", "merge", "commit", "stash"}:
            return "write", [f"git {verb!r} mutates the repo"]
    elif binary == "curl":
        # POST/PUT/DELETE bump to write.
        if re.search(r"-X\s*(POST|PUT|DELETE|PATCH)\b", cmd, re.IGNORECASE):
            return "write", ["curl with non-GET method"]
        if re.search(r"\s(--data|-d|--data-binary|--data-raw|-T|--upload-file)\b", cmd):
            return "write", ["curl with request body"]
    elif binary == "wget":
        return "write", ["wget downloads to disk"]

    return "safe", []
