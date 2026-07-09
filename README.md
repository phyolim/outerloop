# outerloop

![Screenshot 2026-07-08 at 6.02.25 PM.png](docs/Screenshot%202026-07-08%20at%206.02.25%E2%80%AFPM.png)
Outerloop turns the Macs you already own into a small fleet of Claude Code workers,
managed from one web dashboard. One always-on Mac is the **hub**: it holds the ticket
queue, the scheduler, and the dashboard. Any other Mac joins as a **worker**. A single
Mac is a complete fleet — the hub does its own work too.

The problem it solves: the machine that can do the work is usually not the machine in
front of you. The desk Mac has Claude Code logged in, the repos cloned, git/gh
configured. When you think of a fix from your phone or another laptop, the options were
remoting in — screen sharing or SSH into an interactive session, slow and awkward from a
phone — or writing it down to copy-paste later. With outerloop you file a ticket from
wherever you are and a capable machine at home picks it up. You come back to a PR.

The unit of work is a ticket, and the ticket is also the record: an agent writes code on
a branch using the ticket as context, a second agent reviews it (author and reviewer are
separate; the reviewer cannot merge), and the PR references the ticket. Merges require
manual approval in the dashboard, and failing CI blocks a merge even after approval.

Machines find each other on your LAN by Bonjour, or through an outbound SSH tunnel to a
cheap VPS when you're away. No VPN, no OAuth, no cloud service.

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
thread and work resumes with your answer. If a run needs a tool permission it doesn't
have, that lands in the thread too as an **Allow / Deny** decision (instead of being
silently auto-denied on the worker); no answer within a few minutes denies it and the
run moves on. At the **merge gate** you see the PR link,
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

## Install for real

Install the menu-bar app — it's the smoothest way in. It's signed + notarized, and it
does the whole setup for you (dashboard password, service start, pairing):

```sh
brew tap phyolim/tap
brew trust phyolim/tap                # one-time, required for the cask
brew install outerloop               # CLI + service
brew install --cask outerloop-app    # the menu-bar app
```

Open **Outerloop** from the menu bar and pick what this Mac is:

- **Hub + Worker** — the usual choice on your first or only Mac: it holds the queue and
  dashboard *and* does coding work itself.
- **Worker** — every other Mac. Its popover lists hubs on your network — click **Join**,
  it shows a 6-character code, type that code on the hub's **Fleet** page. Paired.
- **Hub** — rare: a hub that only coordinates and never codes.

That's it. The dashboard opens from the menu bar, or at `http://<hub>.local:8765`.
**One hub, any number of workers.**

Any Mac doing coding work also needs `claude` (Claude Code CLI, logged in), `gh`
(authed), and `git` (commit identity set) — the app checks all of it on first launch.
Python ≥ 3.9 (the built-in `/usr/bin/python3`) is enough; zero pip installs.

<details>
<summary>Prefer the terminal? (headless hubs, no GUI)</summary>

Skip the cask; after `brew install outerloop`, tell the Mac its role yourself:

```sh
# first / only Mac — hub that also codes:
outerloop setup-both && brew services start outerloop   # `setup-hub` = coordinate-only

# every other Mac — a worker:
outerloop local role worker
outerloop local hub_url http://<hub>.local:8765
brew services start outerloop
```

`outerloop status` prints the dashboard password; `outerloop doctor` checks the
`claude`/`gh`/`git` toolchain. Pair a CLI worker from the hub: `outerloop token
<worker-name> <secret>`, then on the worker `outerloop local worker <worker-name>` and
`outerloop local token <secret>`, and restart it.
</details>

### Add another Mac

Install the app on it, pick **Worker**, and use its **Join** button (above) — that's the
whole pairing. What each worker is allowed to work on is set by its **capabilities**,
edited live on the hub's Fleet page.

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
- **Upgrade:** the menu-bar app updates itself; for the CLI/service,
  `brew upgrade outerloop && brew services restart outerloop`.
- **Uninstall:**

  ```sh
  brew services stop outerloop
  brew uninstall outerloop
  brew uninstall --zap --cask outerloop-app
  rm -rf ~/Library/Application\ Support/outerloop   # state — only if you want it gone
  ```

## Try it without installing anything

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
