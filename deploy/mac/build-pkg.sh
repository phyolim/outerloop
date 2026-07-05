#!/usr/bin/env bash
# Build a double-clickable .pkg for ONE machine. Run on any Mac with the repo.
#
#   ./build-pkg.sh hub --worker hub
#       # Relay (--vps/--ssh-key) and identity (--tokens/--token) are optional: set the
#       # relay and pair the hub itself afterward in the menu-bar Settings. Bake up front:
#       # --vps 1.2.3.4.sslip.io --ssh-key ~/.ssh/id_ed25519 --tokens "hub:$(openssl rand -hex 24)"
#
#   ./build-pkg.sh worker --worker worker-1
#       # --hub, --caps, --token are all optional now: the worker stays idle until you
#       # set the hub in the menu-bar Settings, caps default to ["dev","repos:*","heavy"]
#       # (editable in the Fleet UI), and you pair the token from Fleet -> "Pair a new
#       # worker". Bake them up front instead with --hub <url> --token <tok> if you like.
#
# Add --real to ship with OUTERLOOP_FAKE=0 (only after a FAKE smoke test). Output:
# outerloop-<role>.pkg — copy to the machine, double-click, done.
set -euo pipefail

ROLE="${1:?usage: build-pkg.sh <hub|worker> [opts]}"; shift
REPO="$(cd "$(dirname "$0")/../.." && pwd)"

# defaults
PYTHON="/opt/homebrew/bin/python3"
PATHENV="/opt/homebrew/bin:/usr/bin:/bin"
FAKE="1"
VPS="" SSHKEY="" TUNNEL_USER="tunnel" TOKENS="" WORKER="" CAPS="[]" HUB="" TOKEN="" MODELS=""
HOST="127.0.0.1"   # hub bind address; --lan flips it to all-interfaces for LAN access
ALLOW_NO_CI="0"    # --allow-merge-without-ci: permit merging a repo that has no CI at all
CLAUDE_BIN=""      # --claude-bin: explicit claude path; empty => postinstall auto-resolves it

while [ $# -gt 0 ]; do
  case "$1" in
    --lan) HOST="0.0.0.0"; shift;;   # LAN-only hub: bind all interfaces, reachable at <hub>.local:8765
    --vps) VPS="$2"; shift 2;;
    --ssh-key) SSHKEY="$2"; shift 2;;
    --tunnel-user) TUNNEL_USER="$2"; shift 2;;
    --tokens) TOKENS="$2"; shift 2;;
    --worker) WORKER="$2"; shift 2;;
    --caps) CAPS="$2"; shift 2;;
    --hub) HUB="$2"; shift 2;;
    --token) TOKEN="$2"; shift 2;;
    --models) MODELS="$2"; shift 2;;   # per-runner model overrides, e.g. "author=opus reviewer=opus"
    --python) PYTHON="$2"; shift 2;;
    --claude-bin) CLAUDE_BIN="$2"; shift 2;;   # explicit claude path (else auto-resolved at install)
    --allow-merge-without-ci) ALLOW_NO_CI="1"; shift;;   # merge repos with no CI (failing checks still block)
    --real) FAKE="0"; shift;;
    *) echo "unknown opt: $1"; exit 1;;
  esac
done

# A worker with no --caps still needs a usable seed so it can claim coding tickets out
# of the box; caps stay hub-owned and are overridable live in the Fleet UI. (--hub is
# optional too: a worker with no hub stays idle until you set it in the menu-bar Settings.)
[ "$ROLE" = worker ] && [ "$CAPS" = "[]" ] && CAPS='["dev","repos:*","heavy"]'

# Every build bumps the patch version in-place: worker self-update triggers on
# hub_version != theirs, so a rebuild that keeps the old number never propagates.
VERSION="$(awk -F'"' '/^__version__/{split($2,v,"."); printf "%s.%s.%s", v[1], v[2], v[3]+1}' "$REPO/outerloop/__init__.py")"
sed -i '' "s/^__version__ = .*/__version__ = \"${VERSION}\"/" "$REPO/outerloop/__init__.py"
echo ">> version ${VERSION} (bumped outerloop/__init__.py — commit it so the repo matches the shipped pkg)"

STAGE="$(mktemp -d)/root"; mkdir -p "$STAGE"
SCRIPTS="$(mktemp -d)"
trap 'rm -rf "$(dirname "$STAGE")" "$SCRIPTS"' EXIT

# Build the React board SPA into ui/dist so the hub serves it. Without it the hub
# falls back to the stdlib server-rendered board, so a build box without Node still
# produces a working .pkg (degraded to the fallback UI). Hub role only — workers don't
# serve the UI.
if [ "$ROLE" = hub ] && [ -d "$REPO/ui" ]; then
  if command -v npm >/dev/null 2>&1; then
    ( cd "$REPO/ui" && { npm ci --silent || npm install; } && npm run build )
  else
    echo "WARN: npm not found — shipping the fallback server-rendered UI (no ui/dist)" >&2
  fi
fi

# payload = the app, minus runtime state / vcs / agent dirs. Exclude built installers
# (*.pkg / a top-level dist) too — otherwise each build bakes the previous machines'
# token-carrying .pkgs into this one's payload (cross-machine token leak). `/dist` is
# anchored to the repo root so the built ui/dist IS included; node_modules never is.
( cd "$REPO" && rsync -a --exclude data --exclude .git --exclude .claude \
    --exclude '__pycache__' --exclude '*.pkg' --exclude '/dist' --exclude node_modules \
    --exclude .venv --exclude venv ./ "$STAGE/" )

# compile the menu-bar app into the payload (needs Xcode CLT swiftc on THIS Mac)
bash "$REPO/deploy/mac/menubar/build-app.sh" "$STAGE"

# the dashboard the menu-bar "Open Dashboard" opens: the relay URL for a worker,
# else the hub's own public URL (or loopback if no relay was given).
DASH="${HUB:-}"
[ -z "$DASH" ] && DASH="${VPS:+https://$VPS}"
[ -z "$DASH" ] && DASH="http://127.0.0.1:8765"

# baked machine config the postinstall reads. Values are single-quoted: postinstall
# `source`s this file, so an unquoted space (WORKER_TOKENS, MODELS), glob (CAPS), or
# redirection char (< > in a stray HUB_URL) would break sourcing. None of these values
# contain a single quote, so single-quoting is safe and fully literal.
cat > "$STAGE/deploy.env" <<EOF
ROLE='${ROLE}'
FAKE='${FAKE}'
PYTHON='${PYTHON}'
PATHENV='${PATHENV}'
SSH_KEY='${SSHKEY}'
VPS_HOST='${VPS}'
TUNNEL_USER='${TUNNEL_USER}'
WORKER_TOKENS='${TOKENS}'
WORKER='${WORKER}'
CAPS='${CAPS}'
HUB_URL='${HUB}'
TOKEN='${TOKEN}'
DASH_URL='${DASH}'
MODELS='${MODELS}'
HOST='${HOST}'
ALLOW_NO_CI='${ALLOW_NO_CI}'
CLAUDE_BIN='${CLAUDE_BIN}'
EOF

cp "$REPO/deploy/mac/scripts/postinstall" "$SCRIPTS/postinstall"
# preinstall gates the install on prerequisites (preflight.sh) BEFORE the payload
# lands, so a missing prereq aborts cleanly instead of half-installing. Both need a
# copy of deploy.env here: the payload copy isn't extracted yet when preinstall runs.
cp "$REPO/deploy/mac/scripts/preinstall"   "$SCRIPTS/preinstall"
cp "$REPO/deploy/mac/scripts/preflight.sh" "$SCRIPTS/preflight.sh"
cp "$STAGE/deploy.env"                      "$SCRIPTS/deploy.env"
chmod +x "$SCRIPTS/postinstall" "$SCRIPTS/preinstall" "$SCRIPTS/preflight.sh"

OUT="$REPO/outerloop-${ROLE}${WORKER:+-$WORKER}.pkg"
pkgbuild --root "$STAGE" \
         --scripts "$SCRIPTS" \
         --identifier "com.outerloop.${ROLE}" \
         --version "$VERSION" \
         --install-location /usr/local/outerloop \
         "$OUT"

echo ">> built $OUT"
echo "   copy to the target Mac and double-click (unsigned: right-click > Open the first time)."
