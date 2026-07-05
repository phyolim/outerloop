#!/usr/bin/env bash
# Cut a tagged release: bump the version, commit, tag, push. That is ALL this
# does — publishing the GitHub Release + bumping the Homebrew tap is manual
# (steps printed at the end; see DEPLOY.md "Releasing").
# Single source of truth for the version is outerloop/__init__.py.
# Usage: scripts/release.sh <new-version>   e.g. 0.1.3
# Tagged releases go through here; deploy/mac/build-pkg.sh's auto-bump is for
# ad-hoc pkg builds only and does NOT tag.
set -euo pipefail
cd "$(dirname "$0")/.."

ver="${1:?usage: scripts/release.sh <version>  (e.g. 0.1.3)}"
[[ "$ver" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "version must be X.Y.Z"; exit 1; }
[[ -z "$(git status --porcelain)" ]] || { echo "working tree not clean"; exit 1; }
[[ "$(git rev-parse --abbrev-ref HEAD)" == "main" ]] || { echo "release from main"; exit 1; }

python3 - "$ver" <<'PY'
import re, sys, pathlib
p = pathlib.Path("outerloop/__init__.py")
p.write_text(re.sub(r'__version__ = ".*"', f'__version__ = "{sys.argv[1]}"', p.read_text()))
PY

git commit -am "release: v$ver"
git tag "v$ver"
git push origin main "v$ver"
cat <<EOF
pushed v$ver — publishing is manual from here (no CI). See DEPLOY.md "Releasing":
  1. scripts/build-release-tarball.sh
       -> dist/outerloop-full-$ver.tar.gz (+ prints its sha256)
  2. gh release create v$ver dist/outerloop-full-$ver.tar.gz --title v$ver --generate-notes
       (deploy/mac/release-app.sh builds/notarizes Outerloop-$ver.zip and uploads it for the cask)
  3. bump url + sha256 in phyolim/homebrew-tap Formula/outerloop.rb
     (brew upgrade only sees the release after this)
EOF
