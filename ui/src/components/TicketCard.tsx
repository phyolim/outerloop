import { useMutation, useQueryClient } from '@tanstack/react-query'
import { startTicket } from '../api'
import type { Card } from '../types'
import KindBadge from './KindBadge'

export default function TicketCard({ card, index = 0 }: { card: Card; index?: number }) {
  const qc = useQueryClient()
  const isDraft = card.status === 'inbox' && card.draft
  const start = useMutation({
    mutationFn: () => startTicket(card.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['board'] }),
  })

  const chip = card.status === 'blocked'
    ? { text: `waiting: ${card.wait ?? '?'}`, cls: 'bg-amber-100 text-amber-800' }
    : isDraft
      ? { text: 'draft', cls: 'bg-amber-50 text-amber-700' }
      : { text: card.sub_stage ?? 'new', cls: 'bg-slate-100 text-slate-600' }

  return (
    <a
      href={`#/ticket/${card.id}`}
      className="card-enter block rounded-lg border border-slate-200 bg-white p-3 shadow-sm transition-shadow hover:shadow-md"
      style={{
        borderLeft: `3px solid ${card.kind_color}`,
        animationDelay: `${Math.min(index, 8) * 40}ms`,
      }}
    >
      <div className="flex items-start justify-between gap-2">
        <span className="text-sm font-medium leading-snug text-slate-900">
          <span className="mono text-xs text-slate-400">#{card.id}</span> {card.title}
        </span>
        <KindBadge label={card.kind_label} color={card.kind_color} />
      </div>
      <div className="mt-2 flex items-center gap-2">
        <span className={`mono rounded px-1.5 py-0.5 text-[11px] ${chip.cls}`}>
          {chip.text}
        </span>
        {isDraft ? (
          // The card itself is a link; Start must not navigate.
          <button
            onClick={(e) => {
              e.preventDefault()
              start.mutate()
            }}
            disabled={start.isPending}
            className="rounded border border-slate-300 px-1.5 py-0.5 text-[11px] font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-40"
          >
            ▶ Start
          </button>
        ) : card.score != null ? (
          // Compact on the card; the full I×U×C/E formula lives in the tooltip.
          <span className="mono text-[11px] text-slate-400" title={card.breakdown}>
            ▲{Math.round(card.score)}
          </span>
        ) : (
          <span className="text-[11px] italic text-slate-300">unscored</span>
        )}
        {card.stale_days != null ? (
          <span
            className="mono rounded bg-red-50 px-1.5 py-0.5 text-[11px] font-medium text-red-600"
            title={`No activity for ${card.stale_days} day(s)`}
          >
            stuck {card.stale_days}d
          </span>
        ) : null}
        {card.project ? (
          <span className="mono ml-auto truncate text-[11px] text-violet-600">
            {card.project}
          </span>
        ) : null}
      </div>
    </a>
  )
}
