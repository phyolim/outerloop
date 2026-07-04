import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { fetchParked, queryKeys, reviveTicket } from '../api'
import type { ParkedTicket } from '../types'
import { ago } from '../lib'
import KindBadge from './KindBadge'
import { BTN, CARD, EmptyState, ErrorBanner, PageHeader } from './ui'

function Row({ t, i }: { t: ParkedTicket; i: number }) {
  const qc = useQueryClient()
  const revive = useMutation({
    mutationFn: () => reviveTicket(t.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.parked() })
      qc.invalidateQueries({ queryKey: ['board'] })
    },
  })

  return (
    <div
      className={`card-enter ${CARD} flex items-center gap-3 p-3`}
      style={{ animationDelay: `${Math.min(i, 10) * 35}ms`, borderLeft: `3px solid ${t.kind_color}` }}
    >
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <a
            href={`#/ticket/${t.id}`}
            className="truncate text-sm font-medium text-slate-900 hover:underline"
          >
            <span className="mono text-slate-400">#{t.id}</span> {t.title}
          </a>
          <KindBadge label={t.kind_label} color={t.kind_color} />
          {t.project ? (
            <span className="mono text-[11px] text-violet-600">{t.project}</span>
          ) : null}
        </div>
        <p className="mt-1 truncate text-xs text-slate-500">
          {t.park_reason || 'no reason recorded'}
          <span className="mono ml-2 text-slate-400">parked {ago(t.created_at)}</span>
        </p>
      </div>
      <button
        onClick={() => revive.mutate()}
        disabled={revive.isPending}
        className={`${BTN.subtle} shrink-0`}
      >
        Restore
      </button>
    </div>
  )
}

export default function ParkedPage() {
  const { data, isError } = useQuery({
    queryKey: queryKeys.parked(),
    queryFn: fetchParked,
    refetchInterval: 5000,
  })
  const tickets = data?.tickets ?? []

  return (
    <div>
      <PageHeader
        title="On hold"
        subtitle="Items the triage screener set aside. Restore one to send it back to the backlog."
      />
      {isError ? <ErrorBanner /> : null}
      {tickets.length === 0 ? (
        <EmptyState glyph="∅" title="Nothing on hold" hint="Items screened out by triage land here." />
      ) : (
        <div className="space-y-2">
          {tickets.map((t, i) => (
            <Row key={t.id} t={t} i={i} />
          ))}
        </div>
      )}
    </div>
  )
}
