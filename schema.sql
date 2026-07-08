-- outerloop schema. Applied idempotently on `python3 -m outerloop init`.
-- One SQLite file is the single source of truth AND the coordination primitive.

CREATE TABLE IF NOT EXISTS ticket (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    title         TEXT NOT NULL,
    body          TEXT NOT NULL DEFAULT '',
    type          TEXT NOT NULL CHECK(type IN ('coding','knowledge','ops')),
    kind          TEXT CHECK(kind IN ('feature','bug','chore','research','ops')),  -- user-facing taxonomy; type is derived from it (see outerloop/taxonomy.py)
    status        TEXT NOT NULL DEFAULT 'inbox'
                       CHECK(status IN ('inbox','parked','active','blocked','done','failed')),
    sub_stage     TEXT,                         -- handler-specific stage (NULL until active)
    impact        INTEGER,                      -- 1..5, stored RAW so score is recomputable
    urgency       INTEGER,                      -- 1..5
    confidence    INTEGER,                      -- 1..5
    effort        INTEGER,                      -- 1..5 (the divisor)
    score         REAL,                         -- cached compute_score(); shown in UI
    reversibility TEXT CHECK(reversibility IN ('reversible','irreversible')),
    park_reason   TEXT,
    repo_path     TEXT,                         -- target repo for coding tickets (NULL ok in FAKE)
    project       TEXT,                         -- human grouping label, any type (NULL = unfiled)
    handler_state TEXT NOT NULL DEFAULT '{}',   -- JSON owned by the handler
    attempts      INTEGER NOT NULL DEFAULT 0,   -- consecutive ticks with NO state change (stall guard)
    last_stage    TEXT,                         -- bookkeeping for the stall guard
    requires      TEXT NOT NULL DEFAULT '[]',   -- JSON capability tags a worker MUST have to claim
    prefer        TEXT,                         -- soft worker hint (tie-break)
    pin           TEXT,                         -- hard worker requirement (NULL = any capable worker)
    assigned_worker TEXT,                       -- worker that currently holds it (fleet view + audit)
    claim_epoch   INTEGER NOT NULL DEFAULT 0,   -- monotonic per-ticket fence; bumped on every claim
    dedup_key     TEXT,                         -- producer idempotency key (screener)
    draft         INTEGER NOT NULL DEFAULT 0,   -- 1 = idea not yet submitted; triage/scoring skip it
    blocked_by_decision_id INTEGER REFERENCES decision(id),
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_ticket_select ON ticket(status, score DESC);
CREATE INDEX IF NOT EXISTS idx_ticket_type   ON ticket(type);

-- Per-ticket atomic claim. PRIMARY KEY(ticket_id) => at most one lease per ticket.
CREATE TABLE IF NOT EXISTS lease (
    ticket_id   INTEGER PRIMARY KEY REFERENCES ticket(id),
    owner       TEXT NOT NULL,                  -- tick_id holding the lease
    pid         INTEGER NOT NULL,               -- optimization only; never sole authority
    boot_uuid   TEXT NOT NULL,                  -- per-process uuid; survives PID reuse
    acquired_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at  TEXT NOT NULL,                  -- TTL is the PRIMARY recovery mechanism
    epoch       INTEGER NOT NULL DEFAULT 0,     -- fence value handed to the current holder
    worker      TEXT                            -- claiming worker name (distributed)
);

-- The fleet: one row per worker machine. capabilities here are the TRUSTED source
-- for routing (must-fix #5) — never the claim request body.
CREATE TABLE IF NOT EXISTS worker (
    name           TEXT PRIMARY KEY,
    capabilities   TEXT NOT NULL DEFAULT '[]',  -- JSON array of capability tags
    status         TEXT NOT NULL DEFAULT 'online'  -- online | paused | draining (offline = stale heartbeat)
                        CHECK(status IN ('online','draining','paused','offline')),
    target_ticket  INTEGER,                     -- "run this ticket on this worker now"
    current_ticket INTEGER,                     -- what it's running (fleet view)
    version        TEXT,
    token_hash     TEXT,                        -- per-worker bearer token (auth; stage 8)
    last_seen      TEXT                         -- hub clock; offline detection
);

-- The human decision queue. A pending row pointed at by ticket.blocked_by_decision_id stalls the ticket.
CREATE TABLE IF NOT EXISTS decision (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id    INTEGER NOT NULL REFERENCES ticket(id),
    kind         TEXT NOT NULL,                 -- merge|deploy|review_exhausted|irreversible_action|high_impact|budget_exceeded
    question     TEXT NOT NULL,
    context      TEXT NOT NULL DEFAULT '{}',    -- JSON: pr_url, diff stat, checks, drafted payload...
    status       TEXT NOT NULL DEFAULT 'pending'
                      CHECK(status IN ('pending','approved','rejected')),
    resume_stage TEXT,                          -- sub_stage to re-enter on approve
    consumed     INTEGER NOT NULL DEFAULT 0,    -- guards double-consume of an answered decision
    rework       INTEGER NOT NULL DEFAULT 0,    -- rejected+rework=1: send back to the worker with the note, don't stop
    answer_note  TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    answered_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_decision_pending ON decision(status, consumed);

-- Append-only audit log. The "why" of every action. Enforced append-only by triggers below.
CREATE TABLE IF NOT EXISTS audit (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id  INTEGER REFERENCES ticket(id),
    tick_id    TEXT,
    actor      TEXT NOT NULL,                   -- cron|triage|scorer|handler:coding|reviewer|gate|human|recovery
    action     TEXT NOT NULL,
    from_stage TEXT,
    to_stage   TEXT,
    reason     TEXT NOT NULL,                   -- plain-English WHY (the legibility requirement)
    detail     TEXT NOT NULL DEFAULT '{}',      -- JSON: exact argv, exit code, cost_usd, score breakdown
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_audit_ticket ON audit(ticket_id, id);

CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit
BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit
BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END;

-- Every claude -p invocation. id == the session_id we ASSIGN via --session-id.
-- Accounting is in TOKENS + model (subscription usage has no meaningful USD figure);
-- tokens_in = input + cache_creation (cache READS excluded: near-free, would swamp
-- the signal). cost_usd is legacy — kept for old rows, no longer used for budgets.
CREATE TABLE IF NOT EXISTS agent_run (
    id            TEXT PRIMARY KEY,             -- == session_id (assigned uuid)
    ticket_id     INTEGER NOT NULL REFERENCES ticket(id),
    role          TEXT NOT NULL
                       CHECK(role IN ('groomer','author','reviewer','fixer','knowledge','ops','triage','scorer')),
    tick_id       TEXT NOT NULL,
    session_id    TEXT NOT NULL,
    prompt        TEXT NOT NULL,
    model         TEXT,                         -- exact model id this run used
    worktree_path TEXT,
    exit_code     INTEGER,
    timed_out     INTEGER NOT NULL DEFAULT 0,
    output_json   TEXT,
    tokens_in     INTEGER NOT NULL DEFAULT 0,
    tokens_out    INTEGER NOT NULL DEFAULT 0,
    persona       TEXT,                         -- roster identity this run embodied (may be NULL)
    cost_usd      REAL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_agent_run_ticket ON agent_run(ticket_id);

-- Live feed of an agent run's visible steps (assistant text, tool calls, tool
-- results), streamed in WHILE claude works so the ticket page can show work in
-- flight. Rolling window (self-pruned to context.AGENT_EVENT_CAP), not append-only:
-- agent_run.output_json is the durable record.
CREATE TABLE IF NOT EXISTS agent_event (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id  INTEGER NOT NULL REFERENCES ticket(id),
    session_id TEXT NOT NULL,
    role       TEXT NOT NULL,
    kind       TEXT NOT NULL,              -- 'text' | 'tool' | 'tool_result'
    body       TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_agent_event_ticket ON agent_event(ticket_id);

-- Rolling raw HTTP request log for the hub API (every /api/* message + its response
-- status). NOT append-only and self-pruned to db.REQUEST_LOG_CAP by db.log_request():
-- idle workers heartbeat every ~2s, so this is liveness-level debug visibility, not a
-- durable record — the audit table is the durable "why of every action".
CREATE TABLE IF NOT EXISTS request_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    worker     TEXT,
    method     TEXT NOT NULL,
    path       TEXT NOT NULL,
    status     INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- One row per cron tick. Doubles as the GLOBAL tick-lock (heartbeat-based staleness).
CREATE TABLE IF NOT EXISTS tick_run (
    id               TEXT PRIMARY KEY,
    pid              INTEGER NOT NULL,
    boot_uuid        TEXT NOT NULL,
    started_at       TEXT NOT NULL DEFAULT (datetime('now')),
    heartbeat_at     TEXT NOT NULL DEFAULT (datetime('now')),  -- liveness; staleness measured from HERE
    finished_at      TEXT,
    status           TEXT NOT NULL DEFAULT 'running'
                          CHECK(status IN ('running','finished','crashed')),
    tickets_advanced INTEGER NOT NULL DEFAULT 0,
    tokens           INTEGER NOT NULL DEFAULT 0,
    cost_usd         REAL NOT NULL DEFAULT 0,   -- legacy; unused since token accounting
    note             TEXT
);

-- Global knobs as rows. Worker reads at tick start.
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
