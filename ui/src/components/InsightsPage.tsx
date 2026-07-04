import { useQuery } from '@tanstack/react-query'
import { fetchInsights, queryKeys } from '../api'
import { CARD, EmptyState, ErrorBanner, PageHeader } from './ui'

function fmtTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${Math.round(n / 1_000)}k`
  return String(n)
}

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className={`card-enter ${CARD} px-4 py-3`}>
      <p className="microlabel">{label}</p>
      <p className="mono mt-1 text-2xl font-semibold tabular-nums tracking-tight text-slate-900">
        {value}
      </p>
      {sub ? <p className="mt-0.5 text-xs text-slate-400">{sub}</p> : null}
    </div>
  )
}

export default function InsightsPage() {
  const { data, isError } = useQuery({
    queryKey: queryKeys.insights(),
    queryFn: fetchInsights,
    refetchInterval: 10_000,
  })

  if (isError) return <ErrorBanner />
  if (!data) return <p className="text-sm text-slate-400">Loading…</p>

  const { days, totals, by_role, by_project } = data
  const maxTok = Math.max(1, ...days.map((d) => d.tokens))
  const maxRole = Math.max(1, ...by_role.map((r) => r.tokens))
  const attempts = totals.done_7d + totals.failed_7d
  const failRate = attempts ? Math.round((totals.failed_7d / attempts) * 100) : 0

  return (
    <div>
      <PageHeader
        title="Insights"
        subtitle="Is the loop healthy, and what is it costing you."
      />

      <div className="mb-5 grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-6">
        <Stat label="tokens · 7d" value={fmtTokens(totals.tokens_7d)} />
        <Stat label="completed · 7d" value={String(totals.done_7d)} />
        <Stat
          label="failure rate · 7d"
          value={`${failRate}%`}
          sub={`${totals.failed_7d} failed / ${attempts} finished`}
        />
        <Stat label="in progress" value={String(totals.active)} />
        <Stat label="blocked" value={String(totals.blocked)} />
        <Stat label="drafts" value={String(totals.drafts)} />
      </div>

      {/* 14-day activity: token bars with completions beneath. CSS only — no chart lib. */}
      <div className={`card-enter ${CARD} mb-5 p-4`}>
        <p className="microlabel mb-3">last 14 days — tokens (bars) · completed (count)</p>
        <div className="flex items-end gap-1.5">
          {days.map((d) => (
            <div key={d.d} className="flex flex-1 flex-col items-center gap-1" title={`${d.d}: ${d.tokens.toLocaleString()} tokens, ${d.done} done`}>
              <span className="mono text-[10px] text-slate-400">
                {d.tokens ? fmtTokens(d.tokens) : ''}
              </span>
              <div className="flex h-28 w-full items-end rounded-sm bg-slate-100/80">
                <i
                  className="block w-full rounded-sm bg-emerald-500/80 transition-[height] duration-500"
                  style={{ height: `${Math.round((d.tokens / maxTok) * 100)}%` }}
                />
              </div>
              <span className={`mono text-[10px] ${d.done ? 'font-semibold text-slate-600' : 'text-slate-300'}`}>
                {d.done || '·'}
              </span>
              <span className="mono text-[9px] text-slate-300">{d.d.slice(8)}</span>
            </div>
          ))}
        </div>
      </div>

      <div className="grid gap-3 md:grid-cols-2">
        <div className={`card-enter ${CARD} p-4`}>
          <p className="microlabel mb-3">tokens by role · 7d</p>
          {by_role.length === 0 ? (
            <p className="text-sm text-slate-400">No agent runs this week.</p>
          ) : (
            <div className="space-y-2">
              {by_role.map((r) => (
                <div key={r.role} className="flex items-center gap-2">
                  <span className="mono w-20 shrink-0 text-xs text-slate-600">{r.role}</span>
                  <div className="h-2 flex-1 overflow-hidden rounded-full bg-slate-100">
                    <i
                      className="block h-full rounded-full bg-sky-500/70"
                      style={{ width: `${Math.round((r.tokens / maxRole) * 100)}%` }}
                    />
                  </div>
                  <span className="mono w-12 shrink-0 text-right text-xs tabular-nums text-slate-500">
                    {fmtTokens(r.tokens)}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className={`card-enter ${CARD} p-4`}>
          <p className="microlabel mb-3">projects · 30d</p>
          {by_project.length === 0 ? (
            <EmptyState glyph="∅" title="No projects yet" />
          ) : (
            <div className="space-y-2">
              {by_project.map((p) => (
                <div key={p.project} className="flex items-center gap-2">
                  <span className="mono w-28 shrink-0 truncate text-xs text-violet-600">
                    {p.project}
                  </span>
                  <div className="h-2 flex-1 overflow-hidden rounded-full bg-slate-100">
                    <i
                      className="block h-full rounded-full bg-violet-500/60"
                      style={{ width: `${Math.round((p.done / Math.max(1, p.total)) * 100)}%` }}
                    />
                  </div>
                  <span className="mono w-16 shrink-0 text-right text-xs tabular-nums text-slate-500">
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
