#!/usr/bin/env bash
# Compile the menu-bar app into Outerloop.app with plain swiftc — no Xcode
# project. Output dir defaults to this folder; build-pkg.sh points it at a staging dir.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="${1:-$HERE}"
APP="$OUT/Outerloop.app"

# Prefer a full Xcode toolchain when one is installed: the beta Command Line Tools
# lack the x86_64 Swift-compat libs, so the universal (Intel) slice fails to link
# under bare CLT. Harmless when only CLT exists.
XC="$(ls -d /Applications/Xcode*.app 2>/dev/null | head -1)"
[ -n "$XC" ] && export DEVELOPER_DIR="$XC/Contents/Developer"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
cp "$HERE/Info.plist" "$APP/Contents/Info.plist"

# Stamp the bundle version from the single source of truth (outerloop/__init__.py)
# so the app tracks releases instead of the template's placeholder.
VER="$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' "$HERE/../../../outerloop/__init__.py")"
[ -n "$VER" ] && /usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $VER" "$APP/Contents/Info.plist"

# Universal binary (arm64 + x86_64) so one .pkg runs on both Apple Silicon and Intel
# Macs — swiftc only targets the build host, which otherwise ships an arch-only app
# that dies with "can't use this version of the application" on the other arch.
# macos12 matches LSMinimumSystemVersion in Info.plist.
BIN="$APP/Contents/MacOS/Outerloop"
swiftc -O -target arm64-apple-macos12  "$HERE/main.swift" -o "$BIN.arm64"
swiftc -O -target x86_64-apple-macos12 "$HERE/main.swift" -o "$BIN.x86_64"
lipo -create "$BIN.arm64" "$BIN.x86_64" -output "$BIN"
rm -f "$BIN.arm64" "$BIN.x86_64"

echo ">> built $APP ($(lipo -archs "$BIN"))"
