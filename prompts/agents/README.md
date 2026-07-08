# Agent roster

Manage the fleet's agents like a team: one markdown file per **persona** — who the
agent is, what it's good at, and which projects it works on. When a ticket reaches a
pipeline role (groomer, author, reviewer, fixer, shipper, knowledge, ops, triage,
scorer), the best-matching persona's body is prepended to that role's prompt, and its
model choice applies. No matching persona = the stock role prompt, unchanged.

The roster lives in the **hub's data dir** — user config, so upgrades never touch it:

- brew/pkg install: `~/Library/Application Support/outerloop/agents/`
- source checkout: `data/agents/` (or `$OUTERLOOP_HOME/agents`)

This directory in the repo only documents the format and holds a copyable template
(`_example-fintech-ux.md`). Edit the roster **on the hub**: it is hub-owned and ships
to every worker in the heartbeat, like model routing — no release, no restart, the
next agent run picks it up.

## File format

`<name>.md` with frontmatter + a markdown body (the personality, verbatim):

```markdown
---
name: fintech-ux
description: senior product engineer for financial apps
roles: author, fixer, reviewer
projects: banking-*, acme/ledger
model: opus
---
You are a senior product engineer who has shipped consumer fintech for a decade.
You care about trust: numbers are never ambiguous, destructive actions always
confirm, errors state amounts and next steps. You know PCI-DSS boundaries and
never log PANs. Prefer boring, auditable code over clever code.
```

- **name** — how the persona shows up in the audit log (`completed ... as
  'fintech-ux'`). Defaults to the filename.
- **roles** — which pipeline roles this persona may play. Omit = any role.
- **projects** — comma-separated glob patterns, matched case-insensitively against
  the ticket's `project` label, its full `repo_path`, and the bare repo name (so
  `food-*` matches `https://github.com/me/food-orderer`). Omit = generalist: applies
  to any ticket, but always loses to a persona whose `projects` match explicitly.
- **model** — optional tier alias (`haiku`/`sonnet`/`opus`) or full model id. Wins
  over role-level model routing for tickets this persona covers.

## Matching

For each agent run: personas that can play the role are ranked — explicit project
match beats generalist; ties go to the first filename alphabetically. One persona per
run. Files named `README.md` or starting with `_` are ignored (keep templates there).

## Explicit staffing (Projects page / staffing.yml)

On top of glob matching sits `staffing.yml` (next to the `agents/` dir): explicit
per-project role assignments, edited from the dashboard's **Projects** page (the
**Agents** page edits the persona files themselves). Full precedence:

    project staffing  >  persona project-glob  >  generalist  >  stock role prompt

An explicit assignment also overrides the persona's own `roles` list — you said so.
Every agent run's audit line records both the persona and why it was chosen
(`completed … as 'security-hawk' — project staffing: banking-app`).

So "the UX author for the food-ordering app" is one file with
`roles: author` + `projects: food-*`, and your finance specialist is another with
`projects: banking-*` — tickets route to the right specialist by their project label,
exactly like assigning work to the right person on a team.
