"""x106-terminal-ws — standalone WebSocket daemon that bridges xterm.js in the
admin app to a local PTY on the VPS.

Replaces the previous ttyd iframe. Auth via `x106_admin` JWT cookie (same
HS256 secret as the Django API). Shell spawned as the user this daemon runs
under (systemd unit pins `User=x106-ops`).
"""
