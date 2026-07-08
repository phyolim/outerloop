# DEPLOY

Deploy the fleet with **[deploy/README.md](deploy/README.md)** — per-machine `.pkg`
installers for the hub and each worker, LAN (Bonjour `<hub>.local`) or an SSH
reverse-tunnel relay for remote access. **No Tailscale, no Cloudflare, no OAuth** (that
stack was retired).

Quick path:

```sh
cd deploy/mac
# hub (always-on Mac), LAN-only, ship FAKE first:
./build-pkg.sh hub --lan --tokens "hub:$(openssl rand -hex 24) laptop:$(openssl rand -hex 24)"
# then reinstall --real once the FAKE smoke passes; add a worker per other Mac.
```

Install each `.pkg` **while logged into that Mac's desktop** (the installer's preflight
refuses a headless SSH session — launchd agents load into the GUI domain). The preflight
also checks Python ≥ 3.9 and, for real mode, the `git`/`gh`/`claude` toolchain, before
anything is written.

---

## Releasing

Cutting a release is **two parts, and only the first is automated**. There is no CI in
this repo — nothing runs on a tag push; every release is published by hand.

**1. Version + tag** — `scripts/release.sh <ver>` (clean `main` only): bumps
`outerloop/__init__.py`, commits `release: v<ver>`, tags, pushes `main` + tag. Nothing else.

**2. Publish the artifacts** — a finished release (see v0.1.9) carries two assets:

```sh
# CLI/service tarball WITH built ui/dist — this is what `brew install outerloop` fetches:
scripts/build-release-tarball.sh          # -> dist/outerloop-full-<ver>.tar.gz + sha256
gh release create v<ver> dist/outerloop-full-<ver>.tar.gz --title v<ver> --generate-notes

# Signed + notarized menu-bar app for the cask (needs Developer ID cert + notary profile;
# creates the release itself if step above hasn't run, and prints the cask sha256):
deploy/mac/release-app.sh
```

**3. Bump the tap** — in the separate repo `phyolim/homebrew-tap`, update `url` + `sha256`
in `Formula/outerloop.rb` to the new tarball (and the cask to the new app zip).
`brew upgrade outerloop` only delivers the release after this lands.

---

## Operating notes (any deployment)

- **Keep the hub awake — the #1 silent unattended failure** (it owns the DB, API, UI,
  scheduler): `sudo pmset -a sleep 0 disablesleep 1`, enable the headless-Mac energy
  setting, and confirm with a real overnight test. Consider an external heartbeat (a
  worker curling `/fleet`) that alerts when the hub goes dark.
- **Tokens & pairing:** a worker no longer needs a baked token — mint one on the hub's
  `/fleet` → **Pair a new worker** and paste it into the worker's menu-bar **Settings…**
  (name + token), which writes its launchd env and restarts it. Rotate the same way
  (re-pair issues a fresh token; the old one stops working). If you *do* bake tokens
  (`--token` / `--tokens`), they live in the launchd plist + `deploy.env`: keep worker
  perms tight and exclude built `*.pkg`s from backups. Give each worker least-privilege
  capabilities (a report-only screener must not be able to merge).
- **Merge safety:** failing CI is a hard block, independent of your approval click. A
  repo with no CI at all is flagged on the merge card and merges only on your explicit
  approval (`--allow-merge-without-ci` / `OUTERLOOP_ALLOW_MERGE_WITHOUT_CI=1` additionally
  marks no-CI as green on the card).
- **Kill switch:** a `KILL` file in the data dir or `kill_switch=on` halts all execution
  before any side effect — keep a way to reach it independent of the UI. Per-worker
  pause / drain (Resume to bring a worker back) is on the `/fleet` page.
- **Remote access:** stand up the relay box with `deploy/relay/vps-setup.sh` (locked-down
  `tunnel` user + Caddy basic-auth + real cert). Point the hub at it either at build
  (`--vps <ip>.sslip.io --ssh-key <key>`) or at runtime in the hub's menu-bar **Settings…**
  (VPS host + tunnel user + SSH key path, written to `settings.json`); the tunnel wrapper
  reads it at launch, so no rebuild to add/change/disable remote access. The tunnel is an
  outbound `ssh -R`, so no home port is opened.
- **Never bind the hub's own port to a public IP.** The code refuses a routable public
  bind by default (`is_safe_bind` in [outerloop/auth.py](outerloop/auth.py); override
  with `OUTERLOOP_ALLOW_PUBLIC_BIND=1`). A LAN-bound hub does lock both doors on its
  own — bearer tokens on the worker `/api`, a password on the dashboard — but that
  cookie gate is a LAN-grade lock, not an internet-facing one. The relay is the
  supported way to expose the hub beyond your LAN: Caddy's basic-auth + HTTPS sit in
  front of *every* route, so nothing unauthenticated reaches the hub at all.
