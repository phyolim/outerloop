import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { fetchInsights, fetchLog, fetchRequests, queryKeys } from '../api'
import type { LogEvent } from '../types'
import { ago, fmt } from '../lib'
import { DEEP, EmptyState, ErrorBanner, PageHeader, PANEL } from './ui'

/* Activity — the audit trail and the health of the loop, side by side. Top-right
   segmented switches Activity ⇄ Insights (the old Activity + Insights pages);
   a Raw API toggle nests inside the Activity tab. */

function actorColor(actor: string): string {
  if (actor === 'human') return '#a78bfa'
  if (actor.startsWith('handler') || actor === 'reviewer') return '#5eb1f7'
  if (actor === 'recovery' || actor === 'gate') return '#f5b843'
  return '#9aa2b1' // cron / triage / scorer / fleet
}

function prettyDetail(detail: string): string {
  try {
    return JSON.stringify(JSON.parse(detail), null, 2)
  } catch {
    return detail
  }
}

function EventRow({ e }: { e: LogEvent }) {
  const failed = e.action === 'failed' || e.action === 'fail'
  return (
    <div className="mono flex items-baseline gap-3.5 border-t border-hairline2 px-3.5 py-2 text-xs first:border-0 hover:bg-white/[0.02]">
      <span className="w-[58px] shrink-0 text-[11px] text-tx3" title={fmt(e.at)}>
        {ago(e.at)}
      </span>
      <span className="w-[72px] shrink-0 truncate text-[11px] font-semibold" style={{ color: actorColor(e.actor) }}>
        {e.actor.replace('handler:', '')}
      </span>
      <span className={`w-[120px] shrink-0 truncate font-semibold ${failed ? 'text-bad' : 'text-[#c6ccd8]'}`}>
        {e.action}
      </span>
      <span className="w-10 shrink-0">
        {e.ticket_id ? (
          <a href={`/ticket/${e.ticket_id}`} className="text-info hover:text-[#8ecbfa]">
            #{e.ticket_id}
          </a>
        ) : null}
      </span>
      <span className="min-w-0 flex-1 font-sans text-[12.5px] text-tx2">
        {e.reason}
        {e.detail ? (
          <details className="mt-0.5">
            <summary className="cursor-pointer text-xs text-tx3 hover:text-tx2">details</summary>
            <pre className="mono mt-1 max-h-64 overflow-auto rounded-md bg-well p-2 text-[11px] leading-relaxed text-[#c6ccd8] ring-1 ring-white/10">
              {prettyDetail(e.detail)}
            </pre>
          </details>
        ) : null}
      </span>
    </div>
  )
}

function ActivityFeed() {
  const { data, isError } = useQuery({ queryKey: queryKeys.log(), queryFn: fetchLog })
  const events = data?.events ?? []
  if (isError) return <ErrorBanner />
  if (events.length === 0)
    return <EmptyState glyph="…" title="No activity yet" hint="Events appear as the loop works." />
  return (
    <div className={`card-enter ${DEEP} overflow-hidden`}>
      {events.map((e) => (
        <EventRow key={e.id} e={e} />
      ))}
    </div>
  )
}

function RequestFeed() {
  const { data, isError } = useQuery({ queryKey: queryKeys.requests(), queryFn: fetchRequests })
  const rows = data?.requests ?? []
  if (isError) return <ErrorBanner />
  if (rows.length === 0)
    return <EmptyState glyph="⇄" title="No API traffic" hint="Worker heartbeats and claims land here." />
  return (
    <div className={`card-enter ${DEEP} overflow-hidden`}>
      {rows.map((r) => (
        <div
          key={r.id}
          className="mono flex items-center gap-3.5 border-t border-hairline2 px-3.5 py-1.5 text-xs first:border-0 hover:bg-white/[0.02]"
        >
          <span className="w-[58px] shrink-0 text-[11px] text-tx3" title={fmt(r.at)}>
            {ago(r.at)}
          </span>
          <span className="w-[72px] shrink-0 truncate text-[11px] font-semibold text-[#c6ccd8]">
            {r.worker || '—'}
          </span>
          <span className="w-12 shrink-0 text-tx2">{r.method}</span>
          <span className="min-w-0 flex-1 truncate text-tx2">{r.path}</span>
          <span className={`shrink-0 font-semibold ${r.status < 400 ? 'text-acc' : 'text-bad'}`}>{r.status}</span>
        </div>
      ))}
    </div>
  )
}

function fmtTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${Math.round(n / 1_000)}k`
  return String(n)
}

function InsightsTab() {
  const { data, isError } = useQuery({ queryKey: queryKeys.insights(), queryFn: fetchInsights })
  if (isError) return <ErrorBanner />
  if (!data) return <p className="text-[13px] text-tx3">Loading…</p>
  const { days, totals, by_role, by_project } = data
  const maxTok = Math.max(1, ...days.map((d) => d.tokens))
  const maxRole = Math.max(1, ...by_role.map((r) => r.tokens))
  const attempts = totals.done_7d + totals.failed_7d
  const failRate = attempts ? Math.round((totals.failed_7d / attempts) * 100) : 0
  const tiles = [
    { label: 'tokens · 7d', value: fmtTokens(totals.tokens_7d), color: '#e8eaf0' },
    { label: 'completed · 7d', value: String(totals.done_7d), color: '#3ddc84' },
    { label: 'failure rate', value: `${failRate}%`, color: '#e8eaf0' },
    { label: 'in progress', value: String(totals.active), color: '#e8eaf0' },
    { label: 'blocked', value: String(totals.blocked), color: totals.blocked ? '#f5b843' : '#e8eaf0' },
    { label: 'drafts', value: String(totals.drafts), color: '#e8eaf0' },
  ]
  return (
    <div className="card-enter">
      <div className="mb-4 grid grid-cols-2 gap-2.5 sm:grid-cols-3 xl:grid-cols-6">
        {tiles.map((s) => (
          <div key={s.label} className={`${PANEL} px-3.5 py-3`}>
            <p className="microlabel !tracking-[0.1em]">{s.label}</p>
            <p className="mono mt-1 text-[22px] font-semibold tabular-nums" style={{ color: s.color }}>
              {s.value}
            </p>
          </div>
        ))}
      </div>
      <div className={`${PANEL} mb-4 p-4`}>
        <p className="microlabel mb-3.5">last 14 days — tokens (bars) · completed (count)</p>
        <div className="flex items-end gap-[7px]">
          {days.map((d) => (
            <div
              key={d.d}
              className="flex flex-1 flex-col items-center gap-[5px]"
              title={`${d.d}: ${d.tokens.toLocaleString()} tokens, ${d.done} done`}
            >
              <span className="mono text-[9px] text-tx3">{d.tokens ? fmtTokens(d.tokens) : ''}</span>
              <div className="flex h-[100px] w-full items-end rounded-[3px] bg-white/[0.03]">
                <i
                  className="block w-full rounded-[3px] transition-[height] duration-500"
                  style={{ background: 'linear-gradient(180deg,#3ddc84,#27a35f)', height: `${Math.round((d.tokens / maxTok) * 100)}%` }}
                />
              </div>
              <span className={`mono text-[10px] font-semibold ${d.done ? 'text-tx2' : 'text-[#3a3f4a]'}`}>
                {d.done || '·'}
              </span>
              <span className="mono text-[9px] text-[#3a3f4a]">{d.d.slice(8)}</span>
            </div>
          ))}
        </div>
      </div>
      <div className="grid gap-2.5 md:grid-cols-2">
        <div className={`${PANEL} p-4`}>
          <p className="microlabel mb-3">tokens by role · 7d</p>
          {by_role.length === 0 ? (
            <p className="text-[13px] text-tx3">No agent runs this week.</p>
          ) : (
            <div className="flex flex-col gap-[9px]">
              {by_role.map((r) => (
                <div key={r.role} className="flex items-center gap-2.5">
                  <span className="mono w-[72px] shrink-0 text-[11px] text-tx2">{r.role}</span>
                  <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-white/5">
                    <i className="block h-full rounded-full bg-info" style={{ width: `${Math.round((r.tokens / maxRole) * 100)}%` }} />
                  </div>
                  <span className="mono w-[42px] shrink-0 text-right text-[11px] tabular-nums text-tx3">
                    {fmtTokens(r.tokens)}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
        <div className={`${PANEL} p-4`}>
          <p className="microlabel mb-3">projects · 30d</p>
          {by_project.length === 0 ? (
            <EmptyState glyph="∅" title="No projects yet" />
          ) : (
            <div className="flex flex-col gap-[9px]">
              {by_project.map((p) => (
                <div key={p.project} className="flex items-center gap-2.5">
                  <span className="mono w-[88px] shrink-0 truncate text-[11px] text-proj">{p.project}</span>
                  <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-white/5">
                    <i className="block h-full rounded-full bg-proj" style={{ width: `${Math.round((p.done / Math.max(1, p.total)) * 100)}%` }} />
                  </div>
                  <span className="mono w-16 shrink-0 text-right text-[11px] tabular-nums text-tx3">
                    {p.done}/{p.total} done
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

export default function LogPage() {
  const [tab, setTab] = useState<'activity' | 'insights'>('activity')
  const [raw, setRaw] = useState(false)
  return (
    <div>
      <PageHeader
        title="Activity"
        subtitle="The audit trail and the health of the loop, side by side."
        right={
          <div className="flex rounded-[7px] bg-white/[0.06] p-0.5">
            {(['activity', 'insights'] as const).map((t) => (
              <button
                key={t}
                onClick={() => setTab(t)}
                className={`rounded-[5px] px-3 py-1 text-[11px] font-semibold capitalize transition-colors ${
                  tab === t ? 'bg-[#22262f] text-tx' : 'text-tx2 hover:text-tx'
                }`}
              >
                {t}
              </button>
            ))}
          </div>
        }
      />
      {tab === 'activity' ? (
        <>
          <div className="mb-3 flex justify-end">
            <div className="flex rounded-[7px] bg-white/[0.06] p-0.5">
              {[
                { k: false, label: 'Log' },
                { k: true, label: 'Raw API' },
              ].map((o) => (
                <button
                  key={o.label}
                  onClick={() => setRaw(o.k)}
                  className={`rounded-[5px] px-[11px] py-[3px] text-[11px] font-semibold transition-colors ${
                    raw === o.k ? 'bg-[#22262f] text-tx' : 'text-tx2 hover:text-tx'
                  }`}
                >
                  {o.label}
                </button>
              ))}
            </div>
          </div>
          {raw ? <RequestFeed /> : <ActivityFeed />}
        </>
      ) : (
        <InsightsTab />
      )}
    </div>
  )
}
