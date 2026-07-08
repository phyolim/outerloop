# Hub JSON contract for the React board SPA

The SPA is served BY the Python hub (`outerloop/web.py`). In production the hub serves the
built `ui/dist/` as static files at `/` and exposes these JSON routes under `/ui/*`
(same trust zone as the existing HTML UI — no bearer token; the browser is protected by
the LAN/Caddy boundary). In dev, Vite proxies `/ui/*` to `http://localhost:8765`.

All responses are JSON. Timestamps are SQLite `datetime('now')` strings (UTC,
`"YYYY-MM-DD HH:MM:SS"`).

## GET /ui/board.json?project=<name>

`project` optional; omitted or empty = all projects.

```jsonc
{
  "columns": {
    "inbox":   [Card, ...],   // status='inbox', unscored first then score desc
    "active":  [Card, ...],   // status='active' (label: "In progress")
    "blocked": [Card, ...],   // status='blocked' (label: "Needs you"); each has "wait"
    "done":    [Card, ...]    // status='done', updated in last 7 days, max 15, newest first
  },
  "counts": {
    "inbox": 3, "active": 1, "blocked": 2,
    "done": 4,          // cards shown in the done column (windowed)
    "done_total": 137,  // all-time done count (drives the "all N done ->" link)
    "failed": 1         // drives a "N errored tickets need you" banner -> link to /decisions
  },
  "projects": ["orchestrator", "trading", ...]  // distinct non-null project names, sorted
}
```

### Card
```jsonc
{
  "id": 42,
  "title": "Fix login redirect loop",
  "kind": "bug",                 // one of feature|bug|chore|research|ops
  "kind_label": "Bug",
  "kind_color": "#b4400a",
  "type": "coding",              // handler-routing type (coding|knowledge|ops)
  "status": "active",
  "sub_stage": "implementing",   // may be null -> render "new"
  "score": 18.0,                 // may be null -> render "unscored"
  "breakdown": "(I3 x U3 x C4)/E2 = 18",  // human score string, may be "" 
  "project": "orchestrator",     // may be null
  "draft": false,                // true only on inbox cards not yet submitted (render "draft" + Start)
  "wait": "clarification"        // ONLY on blocked cards: pending decision kind; else absent/null
}
```

## GET /ui/done.json?project=<name>

Full done history (newest first, max 200).
```jsonc
{ "tickets": [ { "id", "title", "kind", "kind_label", "kind_color", "type",
                 "project", "updated_at" }, ... ] }
```

## POST /ui/add   (JSON body)
```jsonc
// request
{ "title": "…", "kind": "feature", "body": "", "project": "", "repo_path": "", "draft": true }
// repo_path is stored only when kind maps to type 'coding'; ignored otherwise.
// response
{ "id": 51, "draft": true }
```
`title` required (non-empty). `kind` must be one of the five; invalid/missing -> "feature".
New tickets land in `status='inbox'` as DRAFTS by default: triage/scoring skip them until
started. Pass `"draft": false` to enter the pipeline immediately.

## POST /ui/start   (JSON body)
```jsonc
// request
{ "id": 51 }
// response: 200 {"ok": true} — or 409 {"error": "not a draft"} if it isn't an inbox draft
```
Submits a draft: the next scheduler pass triages it like any new ticket.

## Kinds (mirror of outerloop/taxonomy.py — source of truth is the server)
| kind     | label    | color    | type      |
|----------|----------|----------|-----------|
| feature  | Feature  | #1a7f37  | coding    |
| bug      | Bug      | #b4400a  | coding    |
| chore    | Chore    | #0a56c2  | coding    |
| research | Research | #5b4bb3  | knowledge |
| ops      | Ops      | #8a6d16  | ops       |

`repo_path` is only meaningful for coding kinds (feature/bug/chore).

## GET /ui/agents.json  (persona roster)

```jsonc
{
  "agents": [{
    "name": "fintech-ux", "description": "finance specialist",
    "roles": ["author", "fixer"],          // [] = plays any role
    "projects": ["banking-*"],             // [] = generalist
    "model": "opus",                       // "" = role default
    "body": "You are…",                    // persona text (frontmatter stripped)
    "file": "fintech-ux.md",               // filename inside `dir`
    "content": "---\n…full raw file…",     // what the editor edits
    "last_at": "2026-07-08 01:00:00",      // last agent_run as this persona, or null
    "pairings": [{ "project": "banking-app", "role": "author" }]  // staffing slots filled
  }],
  "dir": "/…/agents",                      // where the files live (hub data dir)
  "roles": ["groomer","author","reviewer","fixer","shipper"],
  "tiers": ["haiku","opus","sonnet"]
}
```

## POST /ui/agent-save
`{ "file": "<slug>.md", "content": "<full file text>" }` → `{ "ok": true, "loaded": bool }`
(400 on a non-slug filename, README.md, or empty content; `loaded:false` = saved but
the roster skips it, e.g. empty body.)

## GET /ui/projects.json  (staffing matrix)

One row per project (ticket labels ∪ staffing.yml keys):
```jsonc
{
  "projects": [{
    "name": "banking-app", "repo": "https://github.com/…", "open": 3,
    "staffing": { "author": "fintech-ux" },          // explicit staffing.yml entries
    "resolution": {                                  // per role: what WOULD run + why
      "author":  { "persona": "fintech-ux", "model": "opus", "why": "project staffing: banking-app" },
      "reviewer":{ "persona": "generalist", "model": null,   "why": "generalist" },
      "groomer": { "persona": null,          "model": null,  "why": "no persona" }
    },
    "coverage": true      // false = amber "no coverage": nothing specialist matches
  }],
  "roles": ["groomer","author","reviewer","fixer","shipper"],
  "staffing_file": "/…/staffing.yml",
  "agents": ["fintech-ux", "security-hawk"]          // roster names, for the assign picker
}
```
Resolution precedence (server-side, same code the agents run):
**project staffing > persona project-glob > generalist > stock prompt.**

## POST /ui/staffing-set
`{ "project": "banking-app", "role": "author", "persona": "fintech-ux" }` → `{ "ok": true }`
Empty `persona` clears the slot. 400 on unknown role or persona name.

## Routing
The SPA owns every page at its real path — `/`, `/ticket/<id>`, `/decisions`, `/done`,
`/fleet`, `/parked`, `/log`, `/insights` — via history-API routing (`ui/src/router.ts`;
plain `<a href="/x">` links, intercepted app-wide). The hub serves `index.html` for any
non-`/ui/` path (SPA fallback), so deep links and reloads work. There are no
server-rendered pages anymore; old `/#/x` hash URLs redirect to `/x` on load.
