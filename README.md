# outerloop

**outerloop** is a personal **agent fleet**: run Claude Code agents across the
machines you already own. One always-on **hub** owns the queue; a **worker** on each
Mac claims capability-matched work and runs exactly one bounded stage at a time.
Machines find each other over LAN Bonjour or a $5 `ssh -R` relay — no VPN, no
Cloudflare, no OAuth, no cloud service.

You feed the fleet by dropping **tickets** — ideas, bugs, chores, some junk — into
one inbox. A scheduler triages junk, prioritizes with a legible rubric, and advances
the most important work. Anything **irreversible or high-impact** (every merge, every
deploy, every external send) stops and waits in a **decision queue** for you, instead
of happening autonomously.

A single Mac is a complete fleet (the hub runs its own worker) — add machines when
you want more throughput.

## The fleet

One always-on **hub** (`outerloop hub`) owns the SQLite DB + JSON API + web UI +
a background scheduler + the screener producer. Any number of **workers**
(`outerloop worker`) — one per machine — poll the hub over HTTP, claim capability-matched
tickets, run exactly one bounded stage locally, and write back over an epoch-fenced
`POST /api/op`. The hub machine also runs its own loopback worker. It's **1 hub + N
workers**, N ≥ 0 — for a single box, drive the loop directly with `python3 -m outerloop
serve` + `python3 -m outerloop tick`.

**Networking — no VPN, no Cloudflare, no OAuth.**
- **LAN:** the hub binds `0.0.0.0`; workers reach it by Bonjour name (`<hub>.local:8765`),
  so a changing DHCP IP never matters.
- **Remote:** the hub dials *out* to a cheap public box over `ssh -R` (a reverse tunnel),
  which fronts it with Caddy (browser password + HTTPS). No home port is opened.

Two separate locks, one per audience — **both default on for an exposed (LAN-bound) hub**:
- the **worker API** (`/api/*`) is guarded by **per-worker bearer tokens** (`auth on`);
- the **human dashboard** (`/` and `/ui/*`) is guarded by a password (`outerloop config
  ui_token <secret>`; a hub self-generates and prints one if you don't set it).

A loopback hub leaves both open (trusted-local default); the moment it binds the LAN it
turns auth on and ensures a dashboard password, so `0.0.0.0` never exposes a passwordless
board. The bind guard refuses a routable public address unless `OUTERLOOP_ALLOW_PUBLIC_BIND=1`.

**Capabilities decide what each worker claims** and are edited **live in the Fleet UI**
(`/fleet`) — the hub owns them, so a worker's baked `--caps` is only the seed at first
registration; a heartbeat never overwrites what you set. A ticket's `requires` tags must
all be covered by the worker's caps (`repos:*` matches any `repos:…`). Coding tickets
require `["dev"]`; the screener's tickets require `["market-data","analysis"]`.

## What a ticket goes through

Coding tickets run a full delivery lifecycle:

```
seed → groomed → implemented → reviewing ⇄ fixing → opening_pr → merge_gate → merging → merged → done
                    (may pause     (≤3 rounds)      (PR opens once    (you approve)   (deploy is manual
                 to ask you a Q)                    it's stable)                       in v0 — no gate)
```

Review runs on the local branch diff, so the **PR opens only once the work is reviewed
and stable** — never as a half-done draft. Mid-work the author can pause to ask you a
question (a `clarification` block under **Decisions**); answer it and the work resumes
with your answer threaded in.

## Why it's safe to leave running

Unattended agents on your own machines need harder guarantees than a demo:

- **Author ≠ reviewer is structural.** Each agent run is *assigned* its own session
  UUID; the reviewer runs on a later tick, sees only the branch diff (never the author's
  transcript), and has no code path to merge.
- **The gate is the only way to a side effect.** Handlers *propose*; `gate.require()`
  blocks the ticket until you answer. Reversible + low-impact → auto + logged;
  irreversible *or* high-impact → decision queue. Merge is always gated (so will
  deploy be, once a real deploy executor exists — v0 deploys are manual).
  The author/fixer agents get a shell (`Edit,Write,Bash`) to iterate — run tests, spin
  up a dev server — but their prompts forbid `git commit/push`/`gh`: the orchestrator
  owns commit, push, PR, and merge, so every side effect still routes through the gate.
- **Failing CI is a hard block** on merge, independent of your click. A repo with no CI
  at all is flagged on the merge card ("no CI checks configured") and merges only on
  your explicit approval (`OUTERLOOP_ALLOW_MERGE_WITHOUT_CI=1` additionally marks no-CI
  as green on the card).
- **Nothing runs away.** One bounded stage per tick; the review↔fix loop hard-caps at
  3 rounds; per-ticket attempt + cumulative-**token** ceilings fail a stuck ticket; each
  agent run is bounded by a wall-clock timeout (the process group is killed on timeout,
  a worst-case token charge recorded). A hub-wide token ceiling halts the whole fleet
  for a rolling window. Accounting is tokens + model per run (USD is legacy — a
  subscription reports $0, so dollar ceilings would never trigger).
- **Overlapping ticks can't double-run.** A heartbeat-based tick-lock + per-ticket
  leases (TTL-primary recovery, not PID-trust) with a monotonic `claim_epoch` fence
  guard concurrency. Every action is written to an **append-only audit log** (enforced
  by a SQLite trigger) with a *why*.
- **Kill switch:** `settings.kill_switch='on'` or a `KILL` file aborts a tick before any
  side effect; it's re-checked between tickets. Per-worker pause/drain from the UI.

## Stack & requirements

Python 3 **stdlib only** (Python ≥ 3.9 — the macOS Command Line Tools `/usr/bin/python3`
is enough; zero pip installs) + one SQLite file (WAL) + a tiny `http.server` UI. The
actual work is done by headless `claude -p` + `git worktree` + `gh`; the orchestrator is
the glue around them.

For **real mode** (`OUTERLOOP_FAKE=0`), any machine that runs coding tickets also needs:
`claude` (Claude Code CLI, logged in), `gh` (`gh auth status` green), and `git` (with a
commit identity set). Installed as a launchd agent, it needs a **desktop login session**
(the agents load into the GUI domain). The installer's preflight checks all of this
before it touches disk. On an installed box, `outerloop doctor` re-runs those real-mode
checks (nonzero exit if blocked) and `outerloop status` shows this box's role/mode, whether
the service is running, and — on a worker — hub reachability.

## Install

Two install paths. Both keep state in the **same** dir
(`~/Library/Application Support/outerloop`), so the CLI and the menu-bar app operate on
one store — but **only one hub daemon should run per machine** (don't `brew services
start` a hub on a box whose `.pkg` launchd hub is already running, or you get two
schedulers on one DB).

| | `brew` (CLI + service) | `.pkg` (full managed Mac) |
|---|---|---|
| Ships | CLI, hub, worker + optional menu-bar app (cask, signed) | menu-bar app, launchd auto-install, tunnel |
| Service | `brew services` (runs this box's role) | launchd agents |
| State | `~/Library/Application Support/outerloop` | `~/Library/Application Support/outerloop` |
| Best for | most Macs — hub or worker | zero-touch fleet installs with baked tokens + relay tunnel |

**Homebrew** (no repo clone needed):

```sh
brew tap phyolim/tap
brew install outerloop

# the menu-bar GUI (signed + notarized). Homebrew requires trusting a third-party
# tap before installing its casks — one time:
brew trust phyolim/tap
brew install --cask outerloop-app       # pulls the `outerloop` formula automatically
```

**Fresh install, no terminal:** open **Outerloop.app** — on first launch it asks
**Hub**, **Hub + Worker**, or **Worker** for this Mac.

- **Hub** finishes everything: it sets a dashboard password (shown once and copied to
  your clipboard) and starts the daemon — a real LAN hub with auth on, reachable at
  `<hub>.local:8765`. No `doctor`, no other command.
- **Hub + Worker** is the same hardened hub, but this Mac **also does work itself** — one
  box, one service. The co-located worker (and its token) are provisioned automatically on
  start; nothing else to pair.
- **Worker** prompts for its hub URL; pair it (name + token from the hub's Fleet page)
  in the app's Settings, then click **Start worker**.

Forgot the password? `outerloop status` shows it. Prefer the terminal, or choosing the
password yourself? Either works:

```sh
outerloop local role hub                # same as picking "Hub" (daemon generates the password)
outerloop local role both               # same as "Hub + Worker" — hub + a co-located worker
outerloop setup-hub  <dashboard-password> # role=hub  + bind + real + auth + your password, then doctor
outerloop setup-both <dashboard-password> # role=both + bind + real + auth + your password, then doctor
brew services start outerloop           # runs the chosen role (hub | worker | both)
# worker: outerloop local role worker; local hub_url http://hub.local:8765; local token <tok>
```

A **combined node** (`role=both`) runs the hub in the main process and a co-located worker
in a background thread that reaches the hub over loopback — the same epoch-fenced `/api/op`
path a remote worker uses. On a LAN-bound hub (auth on) the co-located worker is minted a
token automatically, so `outerloop local role both` + a restart is the whole setup.

State lives in `~/Library/Application Support/outerloop` (override with `OUTERLOOP_HOME`) —
one store shared by the CLI, the daemon, and the menu-bar app. **A box is FAKE-safe and
loopback-only until it's told it's a hub** (app picker or `role hub`), which flips it to
real mode, LAN bind, and auth by default (an exposed hub self-generates a dashboard
password if you don't set one). Real mode additionally needs `claude` (logged in), `gh`
(authed), and `git` (identity set) on machines that run coding tickets; workers inherit
real/FAKE from the hub.

**Upgrading** is `brew upgrade outerloop` (and `brew upgrade --cask outerloop-app`), then
`brew services restart outerloop`. Upgrading a LAN hub from ≤0.1.8: the first restart
turns auth on and generates a dashboard password if you never set one — run
`outerloop status` to see it.

**Uninstalling:**

```sh
brew services stop outerloop
brew uninstall outerloop                # CLI + daemon
brew uninstall --zap --cask outerloop-app   # the app (+ its prefs/caches)
# state (DB, tokens, settings) is never auto-deleted; to wipe it:
rm -rf ~/Library/Application\ Support/outerloop
```

The `.pkg` remains the zero-touch path for a managed fleet (baked identity/tokens, relay
tunnel, preflight gate) — see
[Build & install the hub and workers](#build--install-the-hub-and-workers).

## Quickstart (FAKE mode — no external deps)

From a clone (source install). With Homebrew, drop the `cd` and use `outerloop` in
place of `python3 -m outerloop`.

```sh
cd ~/Github/outerloop
python3 -m outerloop init                 # create the SQLite db under data/
python3 -m outerloop serve                # UI at http://127.0.0.1:8765  (add tickets here)
python3 -m outerloop tick                 # one scheduler tick (in another shell)
```

`FAKE` mode (the default) uses canned agents and a simulated GitHub, so the entire state
machine — triage, scoring, the coding lifecycle, the gate, leasing, audit — runs end to
end with nothing installed. Add a coding ticket, run `tick` repeatedly, watch it walk to
`merge_gate`; approve the merge under **Decisions**, run `tick` again, and it merges.

## Build & install the hub and workers

Per-machine `.pkg` installers build from this repo (`deploy/mac/build-pkg.sh`) and
double-click to install. On install the **preinstall preflight** verifies prerequisites
(desktop session, Python ≥ 3.9, and for real mode `git`/`gh`/`claude`) *before* laying
anything down; the **postinstall** provisions the DB + tokens and loads the launchd
agents, auto-resolving this machine's `python3` and `claude` paths (so a system
`/usr/bin/python3` or a per-user `~/.local/bin/claude` off the launchd `PATH` both work).

Building needs only **Xcode Command Line Tools** (`swiftc`, to compile the menu-bar app)
on the build Mac — `xcode-select --install` if missing. The menu-bar app compiles as a
**universal binary (arm64 + x86_64)**, so one `.pkg` runs on both Apple Silicon and Intel
Macs no matter which you build on. Build once, copy each `.pkg` to its machine. Full
LAN/relay walkthrough: **[deploy/README.md](deploy/README.md)**.

**1 — Build the hub pkg** (the always-on Mac). The hub also runs its own loopback worker.
Ship FAKE first; add `--real` once a FAKE smoke passes.

```sh
cd deploy/mac
# LAN-only — workers reach it at <hub>.local:8765:
./build-pkg.sh hub --lan --worker hub --caps '["dev","repos:*"]'
# remote instead of --lan: --vps <ip>.sslip.io --ssh-key <key>
```

Nothing about identity has to be baked: after install, open `http://<hub>.local:8765/fleet`,
**Pair a new worker** named `hub`, and paste the token into the hub's menu-bar **Settings…**
(it now has Worker + Token fields alongside the relay). Relay host/key go there too. Prefer
baking it? Add `--tokens "hub:$TOK" --token "$TOK"` (and `--vps/--ssh-key`) at build.

**2 — Build a worker pkg per other Mac.** `--hub`, `--caps`, and `--token` are all
**optional** — the simplest worker build is just:

```sh
./build-pkg.sh worker --worker laptop
```

A worker with no hub configured **stays idle** (it doesn't spin) until you point it at
one. After install, the easiest path is **LAN pairing**: the unpaired worker's menu-bar
popover lists hubs it discovers on the network (Bonjour `_outerloop._tcp`) — click
**Join**, and it displays a 6-character code. Type that code on the hub (Fleet page
banner, or the hub's own menu-bar popover) and the worker receives its token (seeded
with the default caps `dev · repos:* · heavy`, edited live in Fleet), restarts
its daemon, and shows up in Fleet. The code never crosses the network — typing it on
the hub is the proof of physical control; requests expire after 2 minutes, allow 5
wrong-code tries, and are refused on the relay path.

Not on the hub's LAN? The manual flow still works: menu-bar **Settings…** → set the
**Hub URL**, then on the hub's **Fleet** page use **Pair manually with a token** and
paste the name + token back into the worker's Settings. Caps are edited live in Fleet.
(Prefer baking identity up front? Add `--hub http://<hub>.local:8765 --token
<its-token>`, registering that token in the hub's `--tokens` string.)

**3 — Install each `.pkg` on its Mac, logged into that Mac's desktop** (not over SSH — the
preflight refuses a headless session). Copy it over, **right-click → Open** the first time
(unsigned → Gatekeeper), Continue → Install. The hub starts serving at
`http://<hub>.local:8765`; `/fleet` shows each worker as it checks in (or after you pair
it).

| flag | meaning |
|---|---|
| `--lan` | hub binds `0.0.0.0` (LAN); workers address it by `<hub>.local` |
| `--vps <host> --ssh-key <key>` | (hub) relay for remote access; optional — also settable at runtime in the hub's **Settings…** |
| `--real` | ship real mode (`OUTERLOOP_FAKE=0`); omit to ship FAKE |
| `--worker <name>` | this machine's fleet identity |
| `--token <tok>` | bearer token; optional — pair from `/fleet` + **Settings…** instead (hub or worker) |
| `--tokens "a:… b:…"` | (hub only) worker tokens to register up front; optional |
| `--caps '["dev","repos:*"]'` | seed capabilities; worker default `["dev","repos:*","heavy"]`, editable in `/fleet` |
| `--hub <url>` | (worker) hub URL; optional, set it in the menu-bar **Settings…** later |
| `--models "author=opus …"` | per-role model overrides |
| `--allow-merge-without-ci` | permit merging repos with no CI (failing checks still block) |

`--caps` is only the seed at first registration — after that, edit each worker's
capabilities live on the **Fleet** page. Any worker's identity (name + token) — **hub or
worker** — can be set/repaired anytime in that machine's menu-bar **Settings…**; the hub
mints the token on its Fleet page, so you never rebuild to add or re-pair a machine.
Updates = rebuild the pkg and reinstall; the postinstall reloads the agents idempotently. (`--python` /
`--claude-bin` can override the auto-resolved paths, but you rarely need them.)

## Model selection per role

Cheap models do the trivial work, capable ones do the deep work (`config.py`,
`ROLE_MODEL_DEFAULTS`): triage/scorer → Haiku, groomer/reviewer/knowledge/ops → Sonnet,
author/fixer → Opus. Override per runner with `--models "author=opus reviewer=opus"` or
`OUTERLOOP_MODEL_<ROLE>` in the launchd env.

## Deferred by design (v0)

Auto-merge of green PRs, a real deploy executor, and live email/calendar sends are
intentionally **deferred** — the seams exist, v0 keeps them gated/stubbed.

## Layout

| path | purpose |
|---|---|
| `schema.sql` | tables + the append-only audit trigger |
| `outerloop/db.py` | WAL connection, `append_audit`, `BEGIN IMMEDIATE`, settings |
| `outerloop/leasing.py` `claim.py` | heartbeat tick-lock, per-ticket leases + epoch fence, capability-matched claim |
| `outerloop/scoring.py` `triage.py` | legible `(I·U·C)/E` rubric; junk parking; `requires` inference |
| `outerloop/gate.py` | the decision gate (the single choke point) |
| `outerloop/agent.py` | the headless-claude boundary (FAKE + real) |
| `outerloop/git_ops.py` | worktree + gh wrappers, green-CI check, worktree reaper |
| `outerloop/handlers/` | `base` interface + `coding` / `knowledge` / `ops` |
| `outerloop/context.py` | `Ctx` (local) + `RemoteCtx` (worker → hub `POST /api/op`) seam |
| `outerloop/hub.py` `api.py` | the hub: HTTP server, JSON API, background scheduler |
| `outerloop/worker.py` `client.py` | the worker daemon + its tiny HTTP client |
| `outerloop/auth.py` | per-worker bearer tokens + bind-address guard |
| `outerloop/web.py` | the web UI (inbox, decisions, fleet, parked, log) |
| `outerloop/tick.py` `screener.py` | single-box pipeline; screener producer |
| `deploy/` | per-machine `.pkg` build, launchd templates, relay setup |
