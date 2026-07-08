# outerloop

**Vibe coding is fast — and amnesiac.** You chat, code appears, it ships. Six months
later the repo can't tell you what changed where, why a feature exists, or which
requirement a commit served. The chat log that held the "why" is gone; the git history
that survives says nothing. Software teams solved this decades ago with the **outer
loop**: requirement → ticket → branch → review → PR → merge. Chat-driven coding skips
it — or makes *you* drive it by hand, re-pasting context, nagging the bot to open a PR,
copying reviews back and forth.

**outerloop automates the outer loop for Claude Code.** You file a ticket — that's the
requirement. The ticket triggers everything else: an agent writes the code on its own
branch with the ticket as context, a second agent reviews it, a PR opens that cites the
ticket. Every commit traces to a ticket, every ticket holds its requirement, discussion,
and review thread. The history writes itself; you just decide what merges. Nothing
ships without you.

It runs on the Macs you already own. One always-on Mac is the **hub** (queue, dashboard,
scheduler); any other Mac can join as a **worker**. A single Mac is a complete fleet —
the hub does its own work too. Machines find each other on your LAN by Bonjour, or
through a $5 SSH relay when you're away. No VPN, no OAuth, no cloud service.

## How you use it

Everything happens in the web dashboard at `http://<hub>.local:8765`:

- **Inbox** — your home. Whatever is waiting on you (approvals, questions, failures)
  sits at the top; what the fleet is doing right now is below it.
- **Board** — every ticket, Jira-style. Create tickets here (press `c`), filter by
  status, drill into any ticket's thread.
- **Fleet** — your machines: online/offline, what each is running, pause/resume,
  pairing, budget.
- **Activity** — the full log; every action is recorded with a why.

A coding ticket walks this path on its own:

```
you file it → triaged & prioritized → agent writes code on a branch
→ a second agent reviews it (≤3 rounds) → PR opens once it's stable
→ you approve the merge → merged, done
```

Along the way the agent may pause to **ask you a question** — answer in the ticket
thread and work resumes with your answer. At the **merge gate** you see the PR link,
diff stat, and CI status; **Approve & merge**, **Request changes** (your note goes
straight to the agent for another pass), or **Reject** to stop. You can also add a note
to any ticket at any time to steer its next run.

## Why it's safe to leave running

- **You approve every merge.** Agents only propose; the decision queue is the sole path
  to anything irreversible. Failing CI blocks a merge even after you click approve.
- **The author never reviews its own work**, and the reviewer has no way to merge.
- **Nothing runs away.** Per-ticket and fleet-wide token budgets, per-run timeouts, a
  hard cap on review rounds, and a one-click **kill switch** that stops all new work.
- **Get pinged, not surprised.** Set a notify URL (`outerloop config notify_url
  https://ntfy.sh/<topic>`) and your phone buzzes for every decision, failure, and
  junk-parked ticket.
- A LAN-exposed hub always locks itself: worker auth on, and a dashboard password
  (self-generated and shown once if you don't set one — `outerloop status` recalls it).

## Try it in two minutes (nothing to install)

FAKE mode — the default from a clone — simulates the agents and GitHub, so the whole
loop runs end to end with nothing installed but a Mac's own Python:

```sh
git clone https://github.com/phyolim/outerloop && cd outerloop
python3 -m outerloop init
python3 -m outerloop serve            # dashboard at http://127.0.0.1:8765
python3 -m outerloop tick             # advance the loop (second shell; run repeatedly)
```

File a ticket, tick a few times, watch it reach the merge gate, approve it, tick again —
merged. That's the whole product in miniature.

## Install for real

Every Mac installs the same way:

```sh
brew install phyolim/tap/outerloop
```

Then tell that Mac what it is. Two cases:

**Your first (or only) Mac — the hub.** Run this on the one always-on Mac that holds the
queue and dashboard. It also does coding work itself, which is what you want on a single
machine:

```sh
outerloop setup-both                  # hub + its own worker
brew services start outerloop
```

Dashboard is now live at `http://<hub>.local:8765`; `outerloop status` prints the
password.

**Every other Mac — a worker.** Run this on each additional Mac you want to do coding
work, then pair it to the hub (see "Add another Mac" below):

```sh
outerloop local role worker
outerloop local hub_url http://<hub>.local:8765
brew services start outerloop
```

The menu-bar app makes this one click instead — see below. Either way: **one hub, any
number of workers.** (Rare: a hub that only coordinates and never codes — `outerloop
setup-hub` instead of `setup-both`.)

Any Mac doing coding work also needs `claude` (Claude Code CLI, logged in), `gh`
(authed), and `git` (commit identity set) — `outerloop doctor` checks all of it. Python
≥ 3.9 (the built-in `/usr/bin/python3`) is enough; zero pip installs.

Prefer clicking to typing? The optional menu-bar app (signed + notarized) does the same
setup — install it and pick **Hub**, **Hub + Worker**, or **Worker**:

```sh
brew trust phyolim/tap                # one-time, required for the cask
brew install --cask outerloop-app
```

### Add another Mac

A worker needs a token from the hub to join. Easiest with the app: the worker's menu-bar
popover lists hubs on your network — click **Join**, it shows a 6-character code, type
that code on the hub's **Fleet** page. Done.

Pure CLI (no app): on the hub, `outerloop token <worker-name> <secret>`; on the worker,
`outerloop local worker <worker-name>` and `outerloop local token <secret>`, then restart
it.

Either way, what each worker is allowed to work on is set by its **capabilities**, edited
live on the hub's Fleet page.

### Away from home

The hub can dial out to a cheap VPS over SSH so you can reach the dashboard from
anywhere (HTTPS + password) — no port opened at home. That walkthrough, plus the
zero-touch `.pkg` installers for a managed fleet, lives in
**[deploy/README.md](deploy/README.md)**.

## Good to know

- **Models:** cheap models do the trivial work, capable ones the deep work (triage →
  Haiku, review → Sonnet, author → Opus). Override with `--models "author=opus"` or
  `OUTERLOOP_MODEL_<ROLE>`.
- **Personas:** give agents identities and specialties — like staffing a team. Drop a
  markdown file in the hub's `agents/` data dir ("a fintech-savvy author for
  `banking-*` projects", "a food-delivery UX reviewer for `food-*`") and tickets
  route to the matching specialist by project. See `prompts/agents/README.md`.
- **State** lives in `~/Library/Application Support/outerloop` — one store shared by the
  CLI, the daemon, and the app. Run **one hub per machine**, and don't mix a brew-services
  hub with a `.pkg` hub on the same box.
- **Upgrade:** `brew upgrade outerloop && brew services restart outerloop`.
- **Uninstall:**

  ```sh
  brew services stop outerloop
  brew uninstall outerloop
  brew uninstall --zap --cask outerloop-app
  rm -rf ~/Library/Application\ Support/outerloop   # state — only if you want it gone
  ```
