#!/usr/bin/env bash
# Compile the menu-bar app into Outerloop.app with plain swiftc — no Xcode
# project. Output dir defaults to this folder; build-pkg.sh points it at a staging dir.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="${1:-$HERE}"
APP="$OUT/Outerloop.app"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
cp "$HERE/Info.plist" "$APP/Contents/Info.plist"

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
