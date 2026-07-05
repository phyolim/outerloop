#!/bin/bash
# Reverse-tunnel launcher for the hub. Reads relay config at RUNTIME from settings.json
# (written by the menu-bar Settings), falling back to the baked deploy.env. No host/key
# configured => idle (exit 0); launchd (KeepAlive=SuccessfulExit:false) then leaves the
# tunnel stopped until Settings configures it and kickstarts this back to life. This is
# why --vps/--ssh-key are optional at build: set them once in the menu bar, no rebuild.
set -u
ENVF="/usr/local/outerloop/deploy.env"
[ -f "$ENVF" ] && . "$ENVF"   # baked defaults (VPS_HOST/TUNNEL_USER/SSH_KEY) + $PYTHON
PY="${PYTHON:-/usr/bin/python3}"
SETTINGS="${OUTERLOOP_HOME:-$HOME/Library/Application Support/outerloop}/settings.json"

# settings.json overrides the baked deploy.env values (runtime reconfig, no rebuild).
if [ -f "$SETTINGS" ]; then
  get() { "$PY" -c 'import json,sys;print(json.load(open(sys.argv[1])).get(sys.argv[2]) or "")' "$SETTINGS" "$1" 2>/dev/null; }
  v=$(get vps_host);    [ -n "$v" ] && VPS_HOST="$v"
  v=$(get tunnel_user); [ -n "$v" ] && TUNNEL_USER="$v"
  v=$(get ssh_key);     [ -n "$v" ] && SSH_KEY="$v"
fi
TUNNEL_USER="${TUNNEL_USER:-tunnel}"
SSH_KEY="${SSH_KEY:-}"; SSH_KEY="${SSH_KEY/#\~/$HOME}"   # expand a leading ~ (Settings entry)

if [ -z "${VPS_HOST:-}" ] || [ -z "$SSH_KEY" ]; then
  echo "tunnel: no relay configured (set VPS host + SSH key in menu-bar Settings) — idle."
  exit 0
fi

exec /usr/bin/ssh -NT \
  -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
  -o StrictHostKeyChecking=accept-new \
  -i "$SSH_KEY" -R 8765:localhost:8765 "$TUNNEL_USER@$VPS_HOST"
