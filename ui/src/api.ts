import type {
  AddPayload,
  BoardResponse,
  DecisionsResponse,
  DoneResponse,
  FleetResponse,
  LogEvent,
  Factors,
  InsightsResponse,
  ParkedTicket,
  RawRequest,
  SearchResult,
  TicketThread,
} from './types'

export const queryKeys = {
  board: (project: string) => ['board', project] as const,
  done: (project: string) => ['done', project] as const,
  decisions: () => ['decisions'] as const,
  ticket: (id: number) => ['ticket', id] as const,
  fleet: () => ['fleet'] as const,
  parked: () => ['parked'] as const,
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

export function fetchBoard(project: string): Promise<BoardResponse> {
  return getJSON<BoardResponse>(`/ui/board.json${projectQuery(project)}`)
}

export function fetchDone(project: string): Promise<DoneResponse> {
  return getJSON<DoneResponse>(`/ui/done.json${projectQuery(project)}`)
}

export function fetchDecisions(): Promise<DecisionsResponse> {
  return getJSON<DecisionsResponse>('/ui/decisions.json')
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
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
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

export function fetchParked(): Promise<{ tickets: ParkedTicket[] }> {
  return getJSON('/ui/parked.json')
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

export function deviceControl(payload: {
  device: string
  action: 'pause' | 'resume' | 'drain'
}): Promise<{ ok: true }> {
  return postJSON('/ui/device-control', payload)
}

export function deviceCaps(payload: {
  device: string
  capabilities: string
}): Promise<{ ok: true; capabilities: string[] }> {
  return postJSON('/ui/device-caps', payload)
}

export function devicePair(device: string): Promise<{ device: string; token: string }> {
  return postJSON('/ui/device-pair', { device })
}

export function runTick(): Promise<{ ok: true }> {
  return postJSON('/ui/run-tick', {})
}

export function startTicket(id: number): Promise<{ ok: boolean }> {
  return postJSON('/ui/start', { id })
}

export function commentTicket(payload: { ticket_id: number; note: string }): Promise<{ ok: true }> {
  return postJSON('/ui/comment', payload)
}

export function editDraft(payload: {
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
