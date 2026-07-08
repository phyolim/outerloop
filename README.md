# outerloop

**outerloop** turns the Macs you already own into a personal **agent fleet**. You drop
tickets — ideas, bugs, chores — into one inbox; Claude Code agents groom them, write the
code on an isolated branch, review it, and open a PR once it passes review. Anything
irreversible — every merge — waits for **your approval** in a decision queue. Nothing
ships without you.

One always-on Mac is the **hub** (queue, dashboard, scheduler); any other Mac can join
as a **worker** and pick up work it's capable of. A single Mac is a complete fleet — the
hub does its own work too. Machines find each other on your LAN by Bonjour name, or
through a $5 SSH relay when you're away. No VPN, no Cloudflare, no OAuth, no cloud
service.

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

## What you need

- A Mac with Python ≥ 3.9 — the system `/usr/bin/python3` is enough; zero pip installs.
- On machines that do real coding work: `claude` (Claude Code CLI, logged in), `gh`
  (authed), and `git` (commit identity set). `outerloop doctor` verifies all of it.

## Install

```sh
brew tap phyolim/tap
brew install outerloop                # CLI + service
brew trust phyolim/tap                # one-time, required for the cask below
brew install --cask outerloop-app     # optional menu-bar app (signed + notarized)
```

Open **Outerloop.app** and pick what this Mac is: **Hub**, **Hub + Worker** (one box
that also does the work — the usual choice), or **Worker**. The app finishes the setup —
dashboard password (shown once and copied to your clipboard), tokens, service start —
then the dashboard is live at `http://<hub>.local:8765`.

Prefer the terminal?

```sh
outerloop local role both             # this Mac = hub + its own worker
brew services start outerloop
outerloop status                      # role, mode, health, dashboard password
```

### Add another Mac

Install the same way and pick **Worker**. Its menu-bar popover lists hubs it discovers
on your network — click **Join** and it shows a 6-character code; type that code on the
hub's Fleet page. That's the whole pairing. What a worker is allowed to claim is set by
its **capabilities**, edited live on the Fleet page.

### Away from home

The hub can dial out to a cheap VPS over SSH so you can reach the dashboard from
anywhere (HTTPS + password) — no port opened at home. That walkthrough, plus the
zero-touch `.pkg` installers for a managed fleet, lives in
**[deploy/README.md](deploy/README.md)**.

## Try it first (nothing required)

FAKE mode — the default from a clone — simulates the agents and GitHub, so the whole
loop runs end to end with nothing installed:

```sh
python3 -m outerloop init
python3 -m outerloop serve            # dashboard at http://127.0.0.1:8765
python3 -m outerloop tick             # advance the loop (second shell; run repeatedly)
```

File a ticket, tick a few times, watch it reach the merge gate, approve it, tick again —
merged.

## Good to know

- **Models:** cheap models do the trivial work, capable ones the deep work (triage →
  Haiku, review → Sonnet, author → Opus). Override with `--models "author=opus"` or
  `OUTERLOOP_MODEL_<ROLE>`.
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
