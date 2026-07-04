import { useQuery } from '@tanstack/react-query'
import { fetchDecisions, queryKeys } from '../api'
import type { DecisionCard } from '../types'
import { ago } from '../lib'
import KindBadge from './KindBadge'
import { CARD, EmptyState, ErrorBanner, PageHeader, Pill } from './ui'

function reasonTone(reason: string): string {
  if (reason === 'error') return 'red'
  if (reason === 'question') return 'blue'
  return 'amber' // gated action: merge, deploy, …
}

function Item({ t, i }: { t: DecisionCard; i: number }) {
  return (
    <a
      href={`#/ticket/${t.id}`}
      className={`card-enter block ${CARD} p-3.5 transition-shadow hover:shadow-md`}
      style={{ borderLeft: `3px solid ${t.kind_color}`, animationDelay: `${Math.min(i, 8) * 45}ms` }}
    >
      <div className="flex items-start justify-between gap-2">
        <span className="text-sm font-medium leading-snug text-slate-900">
          <span className="mono text-xs text-slate-400">#{t.id}</span> {t.title}
        </span>
        <KindBadge label={t.kind_label} color={t.kind_color} />
      </div>
      {t.preview ? (
        <p className="mt-2 line-clamp-2 text-[13px] leading-relaxed text-slate-600">
          {t.preview}
        </p>
      ) : null}
      <div className="mt-2.5 flex items-center gap-2">
        <Pill tone={reasonTone(t.reason)} dot>
          {t.reason}
        </Pill>
        {t.project ? <span className="mono text-[11px] text-violet-600">{t.project}</span> : null}
        <span className="mono ml-auto text-[11px] text-slate-400">{ago(t.at)}</span>
      </div>
    </a>
  )
}

export default function DecisionsPage() {
  const { data, isError } = useQuery({
    queryKey: queryKeys.decisions(),
    queryFn: fetchDecisions,
    refetchInterval: 4000,
  })
  const tickets = data?.tickets ?? []

  return (
    <div>
      <PageHeader
        title="Approvals"
        subtitle="Items waiting on your approval or reply. Open one to join the discussion."
      />
      {isError ? <ErrorBanner /> : null}
      {tickets.length === 0 ? (
        <EmptyState
          glyph="✓"
          title="No pending approvals"
          hint="The loop is clear — questions and gated actions will appear here."
        />
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {tickets.map((t, i) => (
            <Item key={`${t.id}-${t.reason}`} t={t} i={i} />
          ))}
        </div>
      )}
    </div>
  )
}
