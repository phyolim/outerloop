import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { fetchLog, fetchRequests, queryKeys } from '../api'
import type { LogEvent } from '../types'
import { ago, fmt } from '../lib'
import { CARD, EmptyState, ErrorBanner, PageHeader, Pill } from './ui'

function actorTone(actor: string): string {
  if (actor === 'human') return 'violet'
  if (actor.startsWith('handler') || actor === 'reviewer') return 'blue'
  if (actor === 'recovery' || actor === 'gate') return 'amber'
  return 'slate' // cron / triage / scorer / fleet
}

function prettyDetail(detail: string): string {
  try {
    return JSON.stringify(JSON.parse(detail), null, 2)
  } catch {
    return detail
  }
}

function EventRow({ e }: { e: LogEvent }) {
  const failed = e.action === 'failed'
  return (
    <div className="flex items-start gap-3 border-b border-slate-100 px-3 py-2 text-sm last:border-0 hover:bg-slate-50/70">
      <span className="mono w-16 shrink-0 pt-0.5 text-xs text-slate-400" title={fmt(e.at)}>
        {ago(e.at)}
      </span>
      <span className="w-24 shrink-0 pt-px">
        <Pill tone={actorTone(e.actor)}>{e.actor.replace('handler:', '')}</Pill>
      </span>
      <span
        className={`mono w-32 shrink-0 truncate pt-0.5 text-xs font-semibold ${
          failed ? 'text-red-600' : 'text-slate-700'
        }`}
      >
        {e.action}
      </span>
      <span className="mono w-12 shrink-0 pt-0.5 text-xs">
        {e.ticket_id ? (
          <a href={`#/ticket/${e.ticket_id}`} className="text-sky-700 hover:underline">
            #{e.ticket_id}
          </a>
        ) : null}
      </span>
      <span className="min-w-0 flex-1 text-[13px] text-slate-600">
        {e.reason}
        {e.detail ? (
          <details className="mt-0.5">
            <summary className="cursor-pointer text-xs text-slate-400 hover:text-slate-600">
              details
            </summary>
            <pre className="mono mt-1 max-h-64 overflow-auto rounded-md bg-slate-50 p-2 text-[11px] leading-relaxed text-slate-700 ring-1 ring-slate-200">
              {prettyDetail(e.detail)}
            </pre>
          </details>
        ) : null}
      </span>
    </div>
  )
}

function ActivityFeed() {
  const { data, isError } = useQuery({
    queryKey: queryKeys.log(),
    queryFn: fetchLog,
    refetchInterval: 3000,
  })
  const events = data?.events ?? []
  if (isError) return <ErrorBanner />
  if (events.length === 0)
    return <EmptyState glyph="…" title="No activity yet" hint="Events appear as the loop works." />
  return (
    <div className={`card-enter ${CARD} overflow-hidden`}>
      {events.map((e) => (
        <EventRow key={e.id} e={e} />
      ))}
    </div>
  )
}

function RequestFeed() {
  const { data, isError } = useQuery({
    queryKey: queryKeys.requests(),
    queryFn: fetchRequests,
    refetchInterval: 3000,
  })
  const rows = data?.requests ?? []
  if (isError) return <ErrorBanner />
  if (rows.length === 0)
    return (
      <EmptyState glyph="⇄" title="No API traffic" hint="Worker heartbeats and claims land here." />
    )
  return (
    <div className={`card-enter ${CARD} overflow-hidden`}>
      {rows.map((r) => (
        <div
          key={r.id}
          className="mono flex items-center gap-3 border-b border-slate-100 px-3 py-1.5 text-xs last:border-0 hover:bg-slate-50/70"
        >
          <span className="w-16 shrink-0 text-slate-400" title={fmt(r.at)}>
            {ago(r.at)}
          </span>
          <span className="w-24 shrink-0 truncate font-semibold text-slate-700">
            {r.device || '—'}
          </span>
          <span className="w-12 shrink-0 text-slate-500">{r.method}</span>
          <span className="min-w-0 flex-1 truncate text-slate-600">{r.path}</span>
          <span
            className={`shrink-0 font-semibold ${r.status < 400 ? 'text-emerald-600' : 'text-red-600'}`}
          >
            {r.status}
          </span>
        </div>
      ))}
    </div>
  )
}

export default function LogPage() {
  const [tab, setTab] = useState<'activity' | 'requests'>('activity')
  return (
    <div>
      <PageHeader
        title="Activity"
        subtitle="The append-only audit trail — the why of every action, newest first."
        right={
          <div className="flex rounded-lg bg-slate-200/70 p-0.5">
            {(['activity', 'requests'] as const).map((t) => (
              <button
                key={t}
                onClick={() => setTab(t)}
                className={`rounded-md px-3 py-1 text-xs font-medium capitalize transition-colors ${
                  tab === t ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-700'
                }`}
              >
                {t === 'requests' ? 'raw API' : t}
              </button>
            ))}
          </div>
        }
      />
      {tab === 'activity' ? <ActivityFeed /> : <RequestFeed />}
    </div>
  )
}
