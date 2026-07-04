export type Kind = 'feature' | 'bug' | 'chore' | 'research' | 'ops'

export interface Card {
  id: number
  title: string
  kind: Kind
  kind_label: string
  kind_color: string
  type: 'coding' | 'knowledge' | 'ops'
  status: 'inbox' | 'active' | 'blocked' | 'done'
  sub_stage: string | null
  score: number | null
  breakdown: string
  project: string | null
  draft: boolean
  stale_days: number | null
  wait?: string | null
}

export interface BoardResponse {
  columns: {
    inbox: Card[]
    active: Card[]
    blocked: Card[]
    done: Card[]
  }
  counts: {
    inbox: number
    active: number
    blocked: number
    done: number
    done_total: number
    failed: number
  }
  projects: string[]
}

export interface DoneTicket {
  id: number
  title: string
  kind: Kind
  kind_label: string
  kind_color: string
  type: string
  project: string | null
  updated_at: string
}

export interface DoneResponse {
  tickets: DoneTicket[]
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

export interface Device {
  name: string
  state: 'online' | 'offline' | 'paused' | 'draining'
  capabilities: string[]
  seen_sec: number | null
  current_ticket: number | null
  version: string | null
}

export interface FleetResponse {
  spend: { spent: number; cap: number; halted: boolean; window_hours: number }
  devices: Device[]
}

export interface ParkedTicket {
  id: number
  title: string
  kind_label: string
  kind_color: string
  project: string | null
  park_reason: string | null
  created_at: string
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
  device: string | null
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
  draft: boolean // false = start immediately; true (default) = park as an unsubmitted idea
}

// mirror of the contract's Kinds table; used by the quick-add kind selector
export const KINDS: { kind: Kind; label: string; color: string }[] = [
  { kind: 'feature', label: 'Feature', color: '#1a7f37' },
  { kind: 'bug', label: 'Bug', color: '#b4400a' },
  { kind: 'chore', label: 'Chore', color: '#0a56c2' },
  { kind: 'research', label: 'Research', color: '#5b4bb3' },
  { kind: 'ops', label: 'Ops', color: '#8a6d16' },
]
