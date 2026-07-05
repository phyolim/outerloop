#!/usr/bin/env bash
# Fully remove outerloop (any role) from THIS Mac. Run it in Terminal as the
# user who installed it: `bash uninstall.sh`. Reverses what the .pkg postinstall did.
#
#   bash uninstall.sh              # remove app + agents, PROMPT before deleting data
#   bash uninstall.sh --purge      # also delete data (DB, tokens, logs) without asking
#   bash uninstall.sh --keep-data  # never touch the data dir
set -uo pipefail

PURGE=""; KEEP=""
for a in "$@"; do case "$a" in
  --purge) PURGE=1;; --keep-data) KEEP=1;;
  *) echo "unknown opt: $a (use --purge | --keep-data)"; exit 1;;
esac; done

UID_="$(id -u)"
APPDIR="/usr/local/outerloop"
LA="$HOME/Library/LaunchAgents"
DATA="$HOME/Library/Application Support/outerloop"
AGENTS="hub worker tunnel menubar"

# 1. unload every agent from this user's GUI domain (harmless if absent)
for a in $AGENTS; do launchctl bootout "gui/$UID_/com.outerloop.$a" 2>/dev/null; done
killall Outerloop 2>/dev/null || true   # the menu-bar app, if lingering

# 2. remove the LaunchAgent plists
rm -f "$LA"/com.outerloop.*.plist

# 3. remove the app payload (root-owned) + forget receipts
if [ -d "$APPDIR" ] || pkgutil --pkgs | grep -q '^com.outerloop\.'; then
  sudo rm -rf "$APPDIR"
  for r in hub worker; do sudo pkgutil --forget "com.outerloop.$r" 2>/dev/null; done
fi

# 4. data dir (DB, worker tokens, logs, worktrees) — destructive, so gated
if [ -d "$DATA" ]; then
  if [ -n "$KEEP" ]; then
    echo "kept data: $DATA"
  elif [ -n "$PURGE" ]; then
    rm -rf "$DATA"; echo "purged data: $DATA"
  else
    printf 'delete data dir (DB, tokens, logs)? %s [y/N] ' "$DATA"; read -r ans
    case "$ans" in [yY]*) rm -rf "$DATA"; echo "purged.";; *) echo "kept: $DATA";; esac
  fi
fi

echo "outerloop uninstalled from $(hostname -s)."
