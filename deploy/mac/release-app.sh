#!/usr/bin/env bash
# Build, sign, notarize, staple, and publish Outerloop.app as a GitHub Release asset
# for the Homebrew cask. Runs on the build Mac (no CI). One-time setup:
#   1. Developer ID Application cert installed in the login keychain
#      (developer.apple.com -> Certificates; verify: security find-identity -p codesigning)
#   2. xcrun notarytool store-credentials outerloop-notary \
#        --apple-id <appleid> --team-id <TEAMID> --password <app-specific-pw>
#
# Usage: release-app.sh [vX.Y.Z]   (defaults to v<__version__>; tag must exist on origin)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
PROFILE="${NOTARY_PROFILE:-outerloop-notary}"

VER="${1:-v$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' "$REPO/outerloop/__init__.py")}"
VER="${VER#v}"
IDENTITY="$(security find-identity -v -p codesigning | sed -n 's/.*"\(Developer ID Application: .*\)"/\1/p' | head -1)"
[ -n "$IDENTITY" ] || { echo "no 'Developer ID Application' identity in the keychain — create one at developer.apple.com"; exit 1; }
git -C "$REPO" ls-remote --exit-code origin "refs/tags/v$VER" >/dev/null \
  || { echo "tag v$VER not on origin — release the code first (scripts/release.sh)"; exit 1; }

STAGE="$(mktemp -d)"; trap 'rm -rf "$STAGE"' EXIT
bash "$HERE/menubar/build-app.sh" "$STAGE"
APP="$STAGE/Outerloop.app"
ZIP="$STAGE/Outerloop-$VER.zip"

# Hardened runtime is mandatory for notarization.
codesign --force --options runtime --timestamp --sign "$IDENTITY" "$APP"
codesign --verify --strict "$APP"

ditto -c -k --keepParent "$APP" "$ZIP"
xcrun notarytool submit "$ZIP" --keychain-profile "$PROFILE" --wait
xcrun stapler staple "$APP"
# The shipped zip must contain the STAPLED bundle (offline Gatekeeper pass).
rm -f "$ZIP"; ditto -c -k --keepParent "$APP" "$ZIP"

spctl -a -vv "$APP"
gh release view "v$VER" -R phyolim/outerloop >/dev/null 2>&1 \
  || gh release create "v$VER" -R phyolim/outerloop --title "v$VER" --generate-notes
gh release upload "v$VER" "$ZIP" -R phyolim/outerloop --clobber

echo ""
echo ">> published Outerloop-$VER.zip to release v$VER"
echo ">> cask sha256: $(shasum -a 256 "$ZIP" | awk '{print $1}')"
