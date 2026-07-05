#!/usr/bin/env bash
# Build the "full" release tarball that ships the built React board.
#
# `brew install` fetches a tag's auto-generated *source* tarball, which excludes
# ui/dist (a gitignored build artifact) — and the SPA is the ONLY UI (the
# server-rendered fallback is gone), so a source tarball has no dashboard at all.
# This packages the tracked source tree PLUS a freshly built ui/dist into
# dist/outerloop-full-<version>.tar.gz, which the formula points `url` at
# instead. Extracting it yields REPO_ROOT/ui/dist, which outerloop/config.py
# resolves and web.py serves.
#
# Idempotent: rebuilds ui/dist and overwrites the tarball each run.
set -euo pipefail
cd "$(dirname "$0")/.."

ver=$(python3 -c "import re,pathlib; print(re.search(r'__version__ = \"(.*)\"', pathlib.Path('outerloop/__init__.py').read_text()).group(1))")
name="outerloop-full-$ver"
tarball="dist/$name.tar.gz"

# Build the React SPA.
( cd ui && npm ci && npm run build )
[[ -f ui/dist/index.html ]] || { echo "ui build produced no dist/index.html" >&2; exit 1; }

# Stage the tracked source under a versioned prefix, then drop ui/dist in.
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
git archive --format=tar --prefix="$name/" HEAD | tar -x -C "$stage"
mkdir -p "$stage/$name/ui"
cp -R ui/dist "$stage/$name/ui/dist"

mkdir -p dist
rm -f "$tarball"
tar -czf "$tarball" -C "$stage" "$name"

echo "built $tarball"
shasum -a 256 "$tarball"
