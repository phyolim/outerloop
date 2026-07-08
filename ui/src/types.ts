export type Kind = 'feature' | 'bug' | 'chore' | 'research' | 'ops'

export interface Card {
  id: number
  title: string
  kind: Kind
  kind_label: string
  kind_color: string
  type: 'coding' | 'knowledge' | 'ops'
  status: 'inbox' | 'active' | 'blocked' | 'parked' | 'failed' | 'done'
  sub_stage: string | null
  score: number | null
  breakdown: string
  project: string | null
  worker: string | null // machine working on it (active tickets only)
  draft: boolean
  stale_days: number | null
  wait?: string | null
}

// Board v2: every ticket in one flat list; status is a client-side filter.
export interface TicketsResponse {
  tickets: Card[]
  counts: {
    backlog: number
    active: number
    blocked: number
    onhold: number
    failed: number
    done: number
    open: number
    all: number
  }
  projects: string[]
  repos: string[]
}

// Inbox v2: the two sections decisions.json doesn't cover.
export interface RunningTicket {
  id: number
  title: string
  kind_label: string
  kind_color: string
  type: string
  sub_stage: string | null
  score: number | null
  // running = a worker holds the lease; queued = waiting for a free capable worker;
  // unclaimable = NO online worker's caps cover `requires` (needs operator attention).
  state: 'running' | 'queued' | 'unclaimable'
  requires: string[]
  worker: string | null
  since: string
  last_line: string | null
}
export interface DigestEntry {
  id: number
  title: string
  dot: 'ok' | 'bad' | 'muted'
  what: string
  at: string
}
export interface InboxResponse {
  running: RunningTicket[]
  digest: DigestEntry[]
  drafts: number // drafts waiting to be started (they live on the Board, not here)
}

export interface DecisionCard {
  id: number // ticket id
  title: string
  kind_label: string
  kind_color: string
  project: string | null
  reason: string // 'question' | 'error' | 'merge' | 'deploy' | ...
  preview: string
  at: string
  decision_id?: number // present for pending decisions — enables inline answer
  context?: DecisionContext
}

export interface DecisionsResponse {
  tickets: DecisionCard[]
}

export interface DecisionContext {
  pr_url?: string
  diff_stat?: string
  checks?: string
  checks_green?: boolean
  findings?: string[]
}

export interface ThreadComment {
  author: 'claude' | 'you' | 'system'
  kind?: string
  verdict?: 'approved' | 'rejected' | 'rework'
  body: string
  context?: DecisionContext
  at: string
}

export interface AgentEvent {
  role: string
  kind: string // 'text' | 'tool' | 'tool_result'
  body: string
  at: string
}

export interface AgentRun {
  role: string
  model: string | null
  tokens_in: number
  tokens_out: number
  exit_code: number | null
  at: string
}

export interface Factors {
  impact: number | null
  urgency: number | null
  confidence: number | null
  effort: number | null
}

export interface TicketThread {
  ticket: {
    id: number
    title: string
    body: string
    kind: Kind
    kind_label: string
    kind_color: string
    status: string
    sub_stage: string | null
    project: string | null
    repo_path: string | null
    worker: string | null // machine working on it (active tickets only)
    draft: boolean
  }
  factors: Factors
  score: number | null
  breakdown: string
  comments: ThreadComment[]
  pending: { decision_id: number; kind: string; context: DecisionContext } | null
  failed: boolean
  steps: { action: string; reason: string }[]
  runs: AgentRun[]
  events: AgentEvent[]
}

export interface InsightsResponse {
  days: { d: string; tokens: number; done: number }[]
  totals: {
    tokens_7d: number
    done_7d: number
    failed_7d: number
    active: number
    blocked: number
    drafts: number
  }
  by_role: { role: string; tokens: number }[]
  by_project: { project: string; total: number; done: number }[]
}

export interface Worker {
  name: string
  state: 'online' | 'offline' | 'paused' | 'draining'
  capabilities: string[]
  seen_sec: number | null
  current_ticket: number | null
  version: string | null
}

export interface FleetResponse {
  spend: { spent: number; cap: number; halted: boolean; window_hours: number }
  kill_switch: boolean
  known_caps: string[] // seed defaults ∪ workers' caps ∪ live tickets' requires
  version: string
  workers: Worker[]
}

export interface PairRequest {
  request_id: string
  name: string
  host_info: string
  ip: string
  expires_in: number
  attempts_left: number
}

export interface LogEvent {
  id: number
  at: string
  actor: string
  action: string
  ticket_id: number | null
  reason: string
  detail: string | null
}

export interface RawRequest {
  id: number
  at: string
  worker: string | null
  method: string
  path: string
  status: number
}

export interface SearchResult {
  id: number
  title: string
  kind_label: string
  kind_color: string
  project: string | null
  status: string
  updated_at: string
}

export interface AddPayload {
  title: string
  kind: Kind
  body: string
  project: string
  repo_path: string
  draft: boolean // false (default) = start immediately; true = park as an unsubmitted idea
}

// mirror of the contract's Kinds table; used by the quick-add kind selector
export const KINDS: { kind: Kind; label: string; color: string }[] = [
  { kind: 'feature', label: 'Feature', color: '#1a7f37' },
  { kind: 'bug', label: 'Bug', color: '#b4400a' },
  { kind: 'chore', label: 'Chore', color: '#0a56c2' },
  { kind: 'research', label: 'Research', color: '#5b4bb3' },
  { kind: 'ops', label: 'Ops', color: '#8a6d16' },
]
