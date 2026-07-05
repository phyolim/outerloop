#!/bin/bash
# Verify EVERY prerequisite for a outerloop install. Runs two ways:
#   * automatically as the pkg's `preinstall` — a non-zero exit aborts the install
#     BEFORE the payload lands, so a missing prereq never leaves a half-installed
#     /usr/local/outerloop + cryptic postinstall Code=112;
#   * standalone, to check a machine's readiness before building/shipping:
#       ROLE=worker FAKE=0 ./preflight.sh      (or drop a deploy.env beside it)
#
# It VERIFIES ONLY — never installs anything (a double-clicked pkg silently pulling
# Homebrew/gh/claude over the network fails a dozen ways). Each blocker prints a
# copy-pasteable fix. Role/mode-aware: the check set depends on ROLE / FAKE / VPS.
set -uo pipefail   # NOT -e: run ALL checks so one report lists every problem at once

HERE="$(cd "$(dirname "$0")" && pwd)"
# baked machine config, if the pkg build bundled it beside this script; harmless if not
# shellcheck disable=SC1091
[ -f "$HERE/deploy.env" ] && . "$HERE/deploy.env"

ROLE="${ROLE:-}"; FAKE="${FAKE:-1}"
PATHENV="${PATHENV:-/opt/homebrew/bin:/usr/bin:/bin}"
PYTHON="${PYTHON:-/opt/homebrew/bin/python3}"
VPS_HOST="${VPS_HOST:-}"; SSH_KEY="${SSH_KEY:-}"; TUNNEL_USER="${TUNNEL_USER:-tunnel}"

fails=0; warns=0
if [ -t 1 ]; then G=$'\033[32m'; R=$'\033[31m'; Y=$'\033[33m'; Z=$'\033[0m'; else G=""; R=""; Y=""; Z=""; fi
ok()   { printf '  %s✓%s %s\n' "$G" "$Z" "$1"; }
bad()  { printf '  %s✗%s %s\n' "$R" "$Z" "$1"; [ $# -gt 1 ] && printf '      ↳ %s\n' "$2"; fails=$((fails+1)); }
soft() { printf '  %s!%s %s\n' "$Y" "$Z" "$1"; [ $# -gt 1 ] && printf '      ↳ %s\n' "$2"; warns=$((warns+1)); }

echo "preflight: role=${ROLE:-?} fake=${FAKE} host=$(hostname -s 2>/dev/null)"

# --- ALWAYS: a desktop/console session (launchd agents load into the GUI Aqua domain;
#     over SSH with nobody logged in there is no gui/$uid target to bootstrap into) ---
CUSER="$(stat -f %Su /dev/console 2>/dev/null || true)"
case "$CUSER" in
  root|loginwindow|"") bad "no desktop session (console user='${CUSER:-none}')" \
      "log into the Mac's desktop and run the installer there, not over SSH";;
  *) ok "desktop session: $CUSER";;
esac
CHOME="$(dscl . -read "/Users/$CUSER" NFSHomeDirectory 2>/dev/null | sed 's/^NFSHomeDirectory: //')"

# Run a snippet AS the console user with the SAME PATH the launchd jobs use, so
# presence/auth reflect exactly what the running worker/hub will see. As root
# (preinstall) that means sudo; run standalone as yourself it's a plain shell.
AS_SUDO=0; [ "$(id -u)" = 0 ] && [ -n "$CUSER" ] && [ "$CUSER" != root ] && AS_SUDO=1
as_ok()  { if [ "$AS_SUDO" = 1 ]; then sudo -u "$CUSER" env PATH="$PATHENV" /bin/sh -c "$1" >/dev/null 2>&1
           else env PATH="$PATHENV" /bin/sh -c "$1" >/dev/null 2>&1; fi; }
as_out() { if [ "$AS_SUDO" = 1 ]; then sudo -u "$CUSER" env PATH="$PATHENV" /bin/sh -c "$1" 2>/dev/null
           else env PATH="$PATHENV" /bin/sh -c "$1" 2>/dev/null; fi; }

# --- ALWAYS: python >= 3.9, resolved the SAME way postinstall does (baked path first,
#     then autodetect) — a mismatch here is exactly what broke the hub install ---
PYOK=""
for cand in "$PYTHON" /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3 \
            "$(command -v python3 2>/dev/null || true)"; do
  [ -n "$cand" ] && [ -x "$cand" ] \
    && "$cand" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3,9) else 1)' 2>/dev/null \
    && { PYOK="$cand"; break; }
done
[ -n "$PYOK" ] && ok "python ≥3.9: $PYOK ($("$PYOK" -V 2>&1))" \
  || bad "no python3 ≥3.9 found (looked in /opt/homebrew, /usr/local, /usr/bin, PATH)" \
         "install Xcode CLT: xcode-select --install  (or: brew install python)"

# --- HUB + RELAY (--vps): the tunnel launchd job runs /usr/bin/ssh -i <key> to the relay ---
if [ "$ROLE" = hub ] && [ -n "$VPS_HOST" ]; then
  [ -x /usr/bin/ssh ] && ok "ssh present" || bad "/usr/bin/ssh missing (unexpected on macOS)"
  if [ -n "$SSH_KEY" ] && as_ok "test -r '$SSH_KEY'"; then
    ok "relay ssh key readable: $SSH_KEY"
  else
    bad "relay ssh key not readable by $CUSER: ${SSH_KEY:-<unset>}" \
        "point --ssh-key at a key file the console user can read"
  fi
  # Reachability is network-dependent — warn, don't block. KeepAlive + ssh keepalives
  # reconnect a transiently-down relay; a persistent failure is the thing to chase.
  if as_ok "ssh -o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new -i '$SSH_KEY' '$TUNNEL_USER@$VPS_HOST' true"; then
    ok "relay reachable: $TUNNEL_USER@$VPS_HOST"
  else
    soft "relay not reachable right now: $TUNNEL_USER@$VPS_HOST" \
         "transient is fine (tunnel retries); persistent = check key auth on the relay / firewall"
  fi
fi

# --- --real (FAKE=0): the worker shells out to git/gh/claude. All must be present on
#     the launchd PATH and authenticated, or a real run dies mid-ticket. FAKE skips all. ---
if [ "$FAKE" = 0 ]; then
  if as_ok "command -v git"; then
    ok "git: $(as_out 'command -v git')"
    [ -n "$(as_out 'git config --get user.email')" ] && ok "git commit identity set" \
      || bad "git user.email not set for $CUSER (agent commits will fail)" \
             "sudo -u $CUSER git config --global user.email you@example.com  (and user.name)"
  else bad "git not on the launchd PATH ($PATHENV)" "install Xcode CLT / git"; fi

  if as_ok "command -v gh"; then
    ok "gh: $(as_out 'command -v gh')"
    if as_ok "gh auth status"; then
      ok "gh authenticated"
    elif [ "$AS_SUDO" = 1 ]; then
      # A root preinstall (sudo) has no login-keychain access, where `gh auth login`
      # stores its token — so "not authenticated" here is unreliable, not authoritative.
      # The worker runs in the user's Aqua session (launchd gui/$uid) where the keychain
      # IS unlocked, so gh works at runtime. Warn instead of false-blocking the install.
      soft "gh auth not verifiable from the installer (login keychain needs your GUI session)" \
           "if a real run hits gh 401s, run as $CUSER: gh auth login"
    else
      bad "gh not authenticated for $CUSER (PR create/view will fail)" "gh auth login"
    fi
  else bad "gh not on the launchd PATH ($PATHENV)" "brew install gh"; fi

  # claude must be runnable by the launchd job. It usually is NOT on the launchd PATH
  # (the Claude Code CLI self-installs to a per-user ~/.local/bin, added by an interactive
  # rc that launchd never sources), so the postinstall resolves it and bakes an ABSOLUTE
  # OUTERLOOP_CLAUDE_BIN. This check therefore PASSES if claude is resolvable ANYWHERE the
  # postinstall would find it, and fails only if it's truly absent.
  cbin="${CLAUDE_BIN:-}"
  if [ -z "$cbin" ] || [ ! -x "$cbin" ]; then
    if as_ok "command -v claude"; then
      cbin="$(as_out 'command -v claude')"
    else
      cbin=""
      for c in "$CHOME/.local/bin/claude" "$CHOME/.claude/local/claude" \
               "$CHOME/.npm-global/bin/claude" "$CHOME/.bun/bin/claude" \
               /opt/homebrew/bin/claude /usr/local/bin/claude; do
        [ -x "$c" ] && { cbin="$c"; break; }
      done
      [ -z "$cbin" ] && cbin="$(as_out 'zsh -lic "command -v claude"' | tr -d '[:space:]')"
    fi
  fi
  if [ -n "$cbin" ] && [ -x "$cbin" ]; then
    ok "claude: $cbin (installer bakes this as OUTERLOOP_CLAUDE_BIN)"
  else
    bad "claude CLI not found for $CUSER" "install the Claude Code CLI (required for real mode)"
  fi
  # claude auth has no clean offline probe (OAuth/keychain/API-key) — check for a
  # credential signal and WARN if absent rather than false-blocking on a flaky test.
  if [ -n "$CHOME" ] && as_ok "test -f '$CHOME/.claude/.credentials.json' -o -f '$CHOME/.claude.json'"; then
    ok "claude credentials present (full auth not verifiable offline)"
  else
    soft "could not confirm claude auth for $CUSER" \
         "log into claude once interactively as $CUSER, or set ANTHROPIC_API_KEY in the worker plist"
  fi
fi

echo
if [ "$fails" -gt 0 ]; then
  printf '%spreflight FAILED%s: %d blocker(s), %d warning(s) — fix the ✗ above and retry\n' "$R" "$Z" "$fails" "$warns" >&2
  exit 1
fi
printf '%spreflight OK%s (%d warning(s))\n' "$G" "$Z" "$warns"
exit 0
