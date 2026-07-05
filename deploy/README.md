# Deploy the fleet — per-machine installers (no Tailscale, no Cloudflare, no Google)

> **The `.pkg` and `brew install outerloop` now share one state dir**
> (`~/Library/Application Support/outerloop`), so the menu-bar app and the `brew` CLI
> cooperate on one store. The one rule: **run a single hub daemon per machine** — don't
> `brew services start` a hub on a box whose `.pkg` launchd hub is already running, or
> two schedulers hit the same DB. This `.pkg` path is the full managed-Mac install (see
> the main [README](../README.md#install)). The menu-bar app is no longer pkg-only:
> `brew install --cask outerloop-app` ships the same GUI, signed + notarized.

Put a worker on every Mac and reach the hub from anywhere with a per-machine `.pkg` you
double-click. The only thing you bring is one cheap public box (AWS Lightsail
$5/mo, or free-tier EC2 for a year) that relays traffic. No mesh VPN account, no
Cloudflare, no OAuth.

```
 phone / remote worker ──HTTPS──▶  AWS box (Caddy)  ──┬─ /api/*  → app Bearer token
   (cellular, anywhere)                               └─ /*      → browser login (basic-auth)
                                                              │
                                            AWS localhost:8765 ──ssh -R (encrypted)──▶ hub:8765 (hub)
```

Why a public box at all: your hub sits behind home NAT — nothing on the
internet can reach it unless *some* machine with a public IP forwards to it. That
box is the relay. The hub dials **out** to it (`ssh -R`), so your home router
needs **no** port opened and no dynamic DNS.

---

## Recommended install order (staged — verify one surface at a time)

Don't stand the whole fleet up at once; each step proves one new thing so a failure
is isolated. Relay setup is **step 3** — you don't need the VPS to start.

### Step 1 — hub hub, local only, no relay (lowest risk)
Proves the `.pkg` → root `postinstall` → launchd → tick path on real hardware.
Build with **no `--vps`** (the postinstall then skips the tunnel; hub binds
`127.0.0.1:8765`). Ship FAKE first to prove the plumbing before spending on `claude`.
```sh
cd deploy/mac
./build-pkg.sh hub --tokens "hub:$(openssl rand -hex 24)"   # FAKE, no relay
```
Install the `.pkg` on the hub **while logged into its desktop** (not over SSH — the
postinstall refuses a headless session, by design). Then check:
- menu-bar **● green**; **Open Dashboard** → `http://127.0.0.1:8765` loads;
- file a ticket in the UI, click **Run tick**, watch it advance; `/fleet` shows `hub`.

Flip to real once the plumbing works: rebuild with `--real` and reinstall.
```sh
./build-pkg.sh hub --tokens "hub:<same-token>" --real
```

### Step 2 — the hub's own worker (already installed by the hub role)
The hub `.pkg` also loads a loopback worker. Prove the fleet claim/lease/epoch path:
file a **coding** ticket pinned to a throwaway repo and watch it reach the merge gate
in `/decisions`. (This is the exact flow already smoke-tested.)

### Step 3 — stand up the relay, add remote access
Do the "One time: stand up the relay box" section below, then set the relay in the hub's
menu-bar **Settings…** (**VPS host** `<ip>.sslip.io`, **SSH key** `~/.ssh/id_ed25519`) —
the tunnel reconnects immediately, no rebuild. `https://<ip>.sslip.io/` now works from
anywhere. (You can still bake it at build with `--vps <ip>.sslip.io --ssh-key <key>`.)

### Step 4 — remote workers (laptops)
Build a worker `.pkg` per machine — `./build-pkg.sh worker --worker <name>` is enough.
Install on each, then in that Mac's menu-bar **Settings…** set the **Hub URL**
(`https://<ip>.sslip.io`) and pair it: on the hub's `/fleet`, **Pair a new worker** →
paste the name + token into the worker's Settings. `/fleet` shows all workers online.
(Or bake it up front with `--hub https://<ip>.sslip.io --token <its-token>`.)

### Step 5 — widen
Enable more ticket types (knowledge/ops are structurally ready but only coding is
real-proven), raise the tick cadence, leave it unattended.

---

## LAN-only (no relay, no VPS, no Cloudflare)

If every machine lives on the same home network, you don't need the relay at all —
it only exists to cross the internet. Build the hub with `--lan` and workers find it
by its **Bonjour `.local` name**, so a changing DHCP IP never matters.

```sh
cd deploy/mac
# HUB (hub): binds all interfaces (0.0.0.0), auth on, no relay
./build-pkg.sh hub --lan --tokens "hub:$(openssl rand -hex 24) worker-1:$(openssl rand -hex 24)"

# WORKER: --hub, --caps, and --token are ALL optional now. The simplest build is just a
# worker name — one generic worker pkg works for any machine:
./build-pkg.sh worker --worker worker-1
# The menu-bar app asks for the Hub URL on first launch (stored in settings.json; change
# it anytime in menu-bar Settings…). Caps default to ["dev","repos:*","heavy"]. Pair the
# token from the hub's /fleet → "Pair a new worker", or pre-seed with --token <worker-1-token>.
```

A worker with **no hub configured stays idle** — it exits cleanly rather than spinning on
loopback, and launchd leaves it stopped until you set a Hub URL in Settings, which
restarts it. The **hub URL is runtime config, not baked** — the worker reads
`settings.json` first (`config.local_setting("hub_url")`), then `OUTERLOOP_HUB`. That's why an
IP or hostname change never needs a rebuild: set it once in the menu bar.

**How it answers the obvious questions:**
- **Do I pick a LAN IP?** No. The hub binds `0.0.0.0` (every interface), so there's no
  IP to choose. Workers address it by **name** (`<hub>.local`), not number.
- **What if the IP changes?** Nothing breaks. Bonjour re-resolves `<hub>.local` to the
  new IP automatically — that's the whole reason to use the name over a baked IP.
- **Two orchestrators on the same LAN?** Fine — each has its own `.local` name and is a
  separate fleet; a worker joins whichever hub its `--hub` names. Bonjour auto-dedupes
  colliding names (`hub.local`, `hub-2.local`); port 8765 per-host never clashes.
- **View the dashboard:** `http://<hub>.local:8765` from any worker on the LAN.

**Two caveats, stated plainly:**
- The dashboard UI has **no password** without Caddy (the app only token-gates `/api`).
  On a trusted single-user home LAN that may be fine; otherwise add app-level UI auth
  before relying on it — anyone on the Wi-Fi can otherwise approve a merge.
- `.local` needs mDNS, which some networks block (guest Wi-Fi "client isolation",
  isolated VLANs). On a normal home network it just works.
- A machine that leaves the LAN (a roaming laptop) can't reach the hub until it's back
  — that's when you'd add the relay (step 3 above).

## One time: stand up the relay box (~5 min)

1. **AWS** → Lightsail → create instance → Ubuntu 22.04, the **$5/mo** plan
   (free-tier EC2 `t3.micro` works too). In its firewall, **open inbound 22, 80,
   443**. Note the public IP.
2. On the **hub**, make an SSH key if you don't have one:
   `ssh-keygen -t ed25519` → copy `~/.ssh/id_ed25519.pub`.
3. SSH into the box and run the setup, passing a dashboard username and that pubkey:
   ```sh
   sudo bash vps-setup.sh myname "ssh-ed25519 AAAA...the-hub-pubkey"
   ```
   It makes a locked-down `tunnel` user, installs Caddy, and prompts for the
   **browser password**. It prints your URL: `https://<ip>.sslip.io/`
   (`sslip.io` just maps the IP to a hostname so Caddy can get a real cert — no
   domain to buy).

## Build the installers (on any Mac, from the repo)

```sh
cd deploy/mac

# HUB pkg (the hub). Identity + relay are all optional — pair the hub itself and set the
# VPS/key afterward in its menu-bar Settings…:
./build-pkg.sh hub --worker hub
# ...or bake it all up front (tokens the hub registers, plus the relay):
./build-pkg.sh hub --worker hub \
  --vps <ip>.sslip.io --ssh-key ~/.ssh/id_ed25519 \
  --tokens "hub:$(openssl rand -hex 24) worker-1:$(openssl rand -hex 24) worker-2:$(openssl rand -hex 24)"

# WORKER pkg per other Mac. Simplest form — set hub + pair the token after install:
./build-pkg.sh worker --worker worker-1
# ...or bake identity up front (reuse a token from the hub's --tokens string):
./build-pkg.sh worker --worker worker-1 --hub https://<ip>.sslip.io --token <the-worker-1-token>
```

Pairing after install (hub `/fleet` → **Pair a new worker** → paste name + token into that
machine's menu-bar **Settings…**) means **no** token has to be baked — for the hub *or* a
worker; `--token`/`--tokens` just pre-seed it.

## Install (each Mac)

Copy its `.pkg` over, **double-click**, Continue → Install. The installer lays the
app under `/usr/local/outerloop`, turns on auth, and loads the launchd
agents for your user. The hub also starts the hub + the outbound tunnel.

(Unsigned pkg: first time, **right-click → Open** to get past Gatekeeper.)

Two things the installer does so it "just works" on any Mac:
- **Preflight gate (preinstall).** Before any file is written it checks prerequisites and
  aborts cleanly on a blocker (no half-install): a desktop login session (it refuses a
  headless SSH install — launchd agents need the GUI domain), Python ≥ 3.9, and for a
  `--real` build the `git` / `gh` / `claude` toolchain. Run it by hand to check a target's
  readiness: `ROLE=worker FAKE=0 deploy/mac/scripts/preflight.sh`.
- **Path auto-resolution (postinstall).** It resolves *this machine's* `python3` and
  `claude` and bakes absolute paths into the launchd plists — so a system
  `/usr/bin/python3` (no Homebrew Python) and a per-user `~/.local/bin/claude` (not on
  the launchd `PATH`) both work with no hand-editing.

**Check:** open `https://<ip>.sslip.io/` from your phone on cellular → browser
login → the dashboard. `/fleet` shows each Mac online.

## Menu-bar indicator + quick settings

Every pkg also installs a tiny menu-bar app (`Outerloop.app`, a background
`LSUIElement` — no Dock icon) that starts at login:

- **● green** = all this machine's agents running · **○ grey** = something's down (a
  worker with no hub configured shows grey — it's idle by design) · **◍ red** = work
  halted (the hub's "Pause all work").
- Dropdown: **Open Dashboard**, **Settings…**, **Start/Stop** (this machine's agents —
  labelled *hub* or *worker*), and on the hub **Pause/Resume all work** (the global halt).
  On a worker, **Start** opens Settings first if the hub URL / name / token aren't set yet.
- **Settings…** (worker) — edit the **Hub URL** (saved to `settings.json`), and the
  **Worker** name + **Token** to pair/re-pair this machine with the hub (mint the token on
  the hub's `/fleet` → **Pair a new worker**). Capabilities are hub-owned, shown read-only
  with an **Edit in Fleet** jump. Saving restarts the worker.
- **Settings…** (hub) — two sections: the **relay** (**VPS host** / **tunnel user** /
  **SSH key path**, saved to `settings.json`; the tunnel wrapper reads it at launch —
  empty host = tunnel idle), and **this machine's worker** (**Worker** + **Token**), so
  the hub's own loopback worker is pairable from the UI just like a remote worker. You
  still generate the relay keypair and authorize its `.pub` on the VPS out-of-band.

It's a thin control surface — it shells out to `launchctl`, writes `settings.json` (hub
URL, relay) and the worker plist (worker + token), and toggles the `KILL` file. Building
a pkg needs Xcode Command Line
Tools (`swiftc`) on the build Mac (`xcode-select --install` if `build-app.sh` can't find
it); the app is built as a **universal binary (arm64 + x86_64)**, so one pkg runs on both
Apple Silicon and Intel Macs.

---

## Model selection per task

Cheap models do the trivial work, capable models do the deep work. Defaults live in
[config.py](../../outerloop/config.py) (`ROLE_MODEL_DEFAULTS`):

| Role | Default | Why |
|---|---|---|
| triage, scorer | **Haiku** | one-shot classify / estimate |
| groomer, reviewer, knowledge, ops | **Sonnet** | bounded reasoning |
| author, fixer | **Opus** | deep coding / architecture |

Measured: routing the four non-author roles off Opus cut their cost ~**49%** for
identical work; Opus stays reserved for writing code.

**Override per runner** (a value is a tier alias `haiku`/`sonnet`/`opus` or a full
model id). Precedence: `OUTERLOOP_MODEL_<ROLE>` > `OUTERLOOP_MODELS` > `OUTERLOOP_MODEL` > default.

```sh
# bake into a machine's .pkg — the hub runs triage/scorer, workers run author/reviewer
./build-pkg.sh worker --worker worker-1 --caps '["dev"]' --hub https://<host> --token <t> \
    --models "author=opus reviewer=opus"
```

Or set `OUTERLOOP_MODEL_AUTHOR=opus` (etc.) in any runner's launchd env / shell.

## Notes / what's deliberately minimal

- **Any number of machines: 1 hub + N workers** (N ≥ 0). The hub pkg also runs a loopback
  worker, so a single Mac is a complete fleet. `hub`/`worker-1`/`worker-2` here are just example
  worker names — use whatever you like, one `--worker` per worker pkg.
- **Capabilities are editable live in the Fleet UI** (`/fleet`), not fixed at build time.
  A worker's `--caps` is only the seed at first registration; the hub owns them after
  that (a heartbeat never overwrites what you set). Edit the comma-separated field per
  worker to change what work it claims — no rebuild.
- **Merging a repo with no CI** is flagged on the merge card and merges only on your
  explicit approval. `--allow-merge-without-ci` (bakes `OUTERLOOP_ALLOW_MERGE_WITHOUT_CI=1`)
  additionally marks no-CI as green on the card; actually-failing checks still block.
- Ships in **FAKE mode** (`OUTERLOOP_FAKE=1`). Rebuild any pkg with `--real` once a
  FAKE smoke passes — same as the existing deploy flow.
- The browser UI's only guard is the one Caddy password; the worker `/api` keeps
  its per-worker Bearer tokens. Both ride HTTPS, so nothing crosses the internet
  in cleartext.
- The relay box is a public door. It's a dumb forwarder (no app secrets on it),
  but keep it patched and keep the dashboard password strong.
- Hub updates = rebuild the pkg and reinstall; postinstall re-loads the agents.
- **Worker self-update:** to ship an update, edit code + update **only the hub**
  (`build-pkg.sh` auto-bumps the patch version in `outerloop/__init__.py` on every build —
  commit the bump so the repo matches the shipped pkg); every worker pulls the new code
  and restarts within one poll interval (`config.WORKER_POLL_SEC`), keeping its pairing
  token and settings.
- ponytail: no autossh — plain `ssh` + launchd `KeepAlive` respawns a dropped
  tunnel. Upgrade to autossh only if you see flapping in `tunnel.log`.
