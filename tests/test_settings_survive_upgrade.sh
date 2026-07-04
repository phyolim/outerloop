#!/bin/bash
# Runtime device identity (INBOX_DEVICE/TOKEN) lives ONLY in the worker launchd plist.
# A .pkg upgrade re-renders that plist from the baked deploy.env; postinstall must
# prefer the EXISTING plist's runtime-owned values so an upgrade never de-pairs a box.
# This drives the same plist_env + prefer-existing logic postinstall uses. macOS only.
set -euo pipefail
cd "$(dirname "$0")/.."
# macOS only: this drives postinstall's plist logic via PlistBuddy, which ships only
# on macOS. On other platforms (e.g. the Linux CI matrix) there's nothing to exercise —
# skip cleanly instead of failing every assertion on an absent PlistBuddy.
if [ "$(uname -s)" != "Darwin" ] || ! command -v /usr/libexec/PlistBuddy >/dev/null 2>&1; then
  echo "SKIP: macOS-only (PlistBuddy unavailable)"
  exit 0
fi

TPL="deploy/mac/templates/com.outerloop.worker.plist"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
out="$TMP/worker.plist"

plist_env() { /usr/libexec/PlistBuddy -c "Print :EnvironmentVariables:$2" "$1" 2>/dev/null || true; }

# Same slot logic as postinstall's render_load (device/caps/hub/token/models).
render() {
  local dev="${DEVICE:-}" tok="${TOKEN:-}" hub="${HUB_URL:-}" caps="${CAPS:-[]}" models="${MODELS:-}" p
  if [ -f "$out" ]; then
    p="$(plist_env "$out" INBOX_DEVICE)";        [ -n "$p" ] && dev="$p"
    p="$(plist_env "$out" INBOX_DEVICE_TOKEN)";  [ -n "$p" ] && tok="$p"
    p="$(plist_env "$out" INBOX_HUB)";           [ -n "$p" ] && hub="$p"
    p="$(plist_env "$out" INBOX_CAPABILITIES)";  [ -n "$p" ] && caps="$p"
    p="$(plist_env "$out" INBOX_MODELS)";        [ -n "$p" ] && models="$p"
  fi
  sed -e "s#__PYTHON__#/usr/bin/python3#g" -e "s#__PATH__#/bin#g" \
      -e "s#__APPDIR__#/x#g" -e "s#__HOME__#/x#g" -e "s#__DATA__#/x#g" \
      -e "s#__FAKE__#1#g" -e "s#__CLAUDE_BIN__##g" -e "s#__ALLOW_NO_CI__#0#g" \
      -e "s#__DEVICE__#${dev}#g" -e "s#__CAPS__#${caps}#g" \
      -e "s#__HUB_URL__#${hub}#g" -e "s#__TOKEN__#${tok}#g" -e "s#__MODELS__#${models}#g" \
      "$TPL" > "$out"
}

# 1. Fresh install: baked values land in the plist.
DEVICE=pro TOKEN=tok1 HUB_URL=http://mini.local:8765 render
[ "$(plist_env "$out" INBOX_DEVICE)" = pro ]  || { echo "FAIL: fresh device"; exit 1; }
[ "$(plist_env "$out" INBOX_DEVICE_TOKEN)" = tok1 ] || { echo "FAIL: fresh token"; exit 1; }

# 2. Upgrade with an EMPTY-identity pkg (the recommended pair-from-Fleet build): the
#    existing device/token/hub survive instead of being wiped.
DEVICE="" TOKEN="" HUB_URL="" render
[ "$(plist_env "$out" INBOX_DEVICE)" = pro ]  || { echo "FAIL: upgrade wiped device"; exit 1; }
[ "$(plist_env "$out" INBOX_DEVICE_TOKEN)" = tok1 ] || { echo "FAIL: upgrade wiped token"; exit 1; }
[ "$(plist_env "$out" INBOX_HUB)" = http://mini.local:8765 ] || { echo "FAIL: upgrade wiped hub"; exit 1; }

echo PASS
