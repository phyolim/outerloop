import type {
  AddPayload,
  DecisionsResponse,
  FleetResponse,
  LogEvent,
  Factors,
  InboxResponse,
  InsightsResponse,
  PairRequest,
  RawRequest,
  SearchResult,
  TicketsResponse,
  TicketThread,
} from './types'

export const queryKeys = {
  tickets: (project: string) => ['tickets', project] as const,
  inbox: () => ['inbox'] as const,
  decisions: () => ['decisions'] as const,
  ticket: (id: number) => ['ticket', id] as const,
  fleet: () => ['fleet'] as const,
  pair: () => ['pair'] as const,
  log: () => ['log'] as const,
  requests: () => ['requests'] as const,
  insights: () => ['insights'] as const,
}

function projectQuery(project: string): string {
  return project ? `?project=${encodeURIComponent(project)}` : ''
}

async function getJSON<T>(url: string): Promise<T> {
  const res = await fetch(url)
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json() as Promise<T>
}

export function fetchDecisions(): Promise<DecisionsResponse> {
  return getJSON<DecisionsResponse>('/ui/decisions.json')
}

export function fetchTickets(project: string): Promise<TicketsResponse> {
  return getJSON<TicketsResponse>(`/ui/tickets.json${projectQuery(project)}`)
}

export function fetchInbox(): Promise<InboxResponse> {
  return getJSON<InboxResponse>('/ui/inbox.json')
}

export async function fetchTicket(id: number): Promise<TicketThread | null> {
  // 404 = no such ticket — distinct from "hub unreachable" (which throws).
  const res = await fetch(`/ui/ticket.json?id=${id}`)
  if (res.status === 404) return null
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json() as Promise<TicketThread>
}

async function postJSON<T>(url: string, payload: unknown): Promise<T> {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    // Surface the server's {"error": "..."} body when present — e.g. /ui/edit's
    // "a worker is acting on this ticket right now" beats a bare "409 Conflict".
    const msg = await res
      .json()
      .then((b: { error?: string }) => b.error)
      .catch(() => undefined)
    throw new Error(msg || `${res.status} ${res.statusText}`)
  }
  return res.json() as Promise<T>
}

export function addTicket(payload: AddPayload): Promise<{ id: number }> {
  return postJSON<{ id: number }>('/ui/add', payload)
}

export function answerDecision(payload: {
  decision_id: number
  action: 'approve' | 'reject' | 'rework'
  note: string
}): Promise<{ ok: true }> {
  return postJSON('/ui/answer', payload)
}

export function closeTicket(ticket_id: number): Promise<{ ok: true }> {
  return postJSON('/ui/close', { ticket_id })
}

export function retryTicket(ticket_id: number): Promise<{ ok: true }> {
  return postJSON('/ui/retry', { ticket_id })
}

export function dismissTicket(ticket_id: number): Promise<{ ok: true }> {
  return postJSON('/ui/dismiss', { ticket_id })
}

export function fetchFleet(): Promise<FleetResponse> {
  return getJSON<FleetResponse>('/ui/fleet.json')
}

export function fetchLog(): Promise<{ events: LogEvent[] }> {
  return getJSON('/ui/log.json')
}

export function fetchRequests(): Promise<{ cap: number; requests: RawRequest[] }> {
  return getJSON('/ui/requests.json')
}

export function fetchSearch(q: string): Promise<{ tickets: SearchResult[] }> {
  return getJSON(`/ui/search.json?q=${encodeURIComponent(q)}`)
}

export function reviveTicket(ticket_id: number): Promise<{ ok: true }> {
  return postJSON('/ui/revive', { ticket_id })
}

export function pauseTicket(ticket_id: number): Promise<{ ok: true }> {
  return postJSON('/ui/pause', { ticket_id })
}

export function resumeTicket(ticket_id: number): Promise<{ ok: true }> {
  return postJSON('/ui/resume', { ticket_id })
}

export function workerControl(payload: {
  worker: string
  action: 'pause' | 'resume' | 'drain'
}): Promise<{ ok: true }> {
  return postJSON('/ui/worker-control', payload)
}

export function workerCaps(payload: {
  worker: string
  capabilities: string
}): Promise<{ ok: true; capabilities: string[] }> {
  return postJSON('/ui/worker-caps', payload)
}

export function workerRename(payload: {
  worker: string
  new_name: string
}): Promise<{ ok: true; worker: string }> {
  return postJSON('/ui/worker-control', { ...payload, action: 'rename' })
}

export function workerDelete(worker: string): Promise<{ ok: true }> {
  return postJSON('/ui/worker-control', { worker, action: 'delete' })
}

export function workerPair(worker: string): Promise<{ worker: string; token: string }> {
  return postJSON('/ui/worker-pair', { worker })
}

export function fetchPairRequests(): Promise<{ requests: PairRequest[]; seed_caps: string[] }> {
  return getJSON('/ui/pair.json')
}

export async function pairConfirm(payload: {
  request_id: string
  code: string
}): Promise<{ ok: true }> {
  // 400 carries the human-readable reason (wrong code, tries left) — surface it.
  const res = await fetch('/ui/pair-confirm', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  const j = await res.json()
  if (!res.ok) throw new Error(j.error ?? `${res.status}`)
  return j
}

export function pairIgnore(request_id: string): Promise<{ ok: true }> {
  return postJSON('/ui/pair-ignore', { request_id })
}

export function runTick(): Promise<{ ok: true }> {
  return postJSON('/ui/run-tick', {})
}

export function setKillSwitch(on: boolean): Promise<{ ok: true; on: boolean }> {
  return postJSON('/ui/kill-switch', { on })
}

export function startTicket(id: number): Promise<{ ok: boolean }> {
  return postJSON('/ui/start', { id })
}

export function commentTicket(payload: { ticket_id: number; note: string }): Promise<{ ok: true }> {
  return postJSON('/ui/comment', payload)
}

export function editTicket(payload: {
  ticket_id: number
  title: string
  kind: string
  body: string
  project: string
  repo_path: string
}): Promise<{ ok: true }> {
  return postJSON('/ui/edit', payload)
}

export function saveFactors(
  payload: { ticket_id: number } & Record<keyof Factors, number>,
): Promise<{ ok: true }> {
  return postJSON('/ui/factors', payload)
}

export function fetchInsights(): Promise<InsightsResponse> {
  return getJSON('/ui/insights.json')
}
