#!/usr/bin/env bash
# Cut a tagged release. Single source of truth for the version is
# inbox/__init__.py. Usage: scripts/release.sh <new-version>   e.g. 0.1.3
# Tagged releases go through here; deploy/mac/build-pkg.sh's auto-bump is for
# ad-hoc pkg builds only and does NOT tag.
set -euo pipefail
cd "$(dirname "$0")/.."

ver="${1:?usage: scripts/release.sh <version>  (e.g. 0.1.3)}"
[[ "$ver" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "version must be X.Y.Z"; exit 1; }
[[ -z "$(git status --porcelain)" ]] || { echo "working tree not clean"; exit 1; }
[[ "$(git rev-parse --abbrev-ref HEAD)" == "master" ]] || { echo "release from master"; exit 1; }

python3 - "$ver" <<'PY'
import re, sys, pathlib
p = pathlib.Path("inbox/__init__.py")
p.write_text(re.sub(r'__version__ = ".*"', f'__version__ = "{sys.argv[1]}"', p.read_text()))
PY

git commit -am "release: v$ver"
git tag "v$ver"
git push origin master "v$ver"
echo "pushed v$ver — release.yml will build the GitHub Release and bump the tap"
