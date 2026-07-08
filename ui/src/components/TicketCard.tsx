import { useMutation, useQueryClient } from '@tanstack/react-query'
import { startTicket } from '../api'
import type { Card } from '../types'
import { kindColor, stageChip, CHIP } from './ui'
import { LifecycleMeter } from './lifecycle'

export default function TicketCard({ card, index = 0 }: { card: Card; index?: number }) {
  const qc = useQueryClient()
  const isDraft = card.status === 'inbox' && card.draft
  const start = useMutation({
    mutationFn: () => startTicket(card.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['tickets'] }),
  })
  const chip = stageChip(card)

  return (
    <a
      href={`/ticket/${card.id}`}
      className="card-enter block rounded-[10px] border border-hairline bg-panel p-3 pb-2.5 transition-colors hover:border-white/[0.16] hover:bg-[#1a1e25]"
      style={{ animationDelay: `${Math.min(index, 8) * 40}ms` }}
    >
      <div className="mb-[7px] flex items-center gap-2">
        <span className="mono text-[11px] text-tx3">#{card.id}</span>
        <span
          className="mono text-[10px] font-semibold uppercase tracking-[0.08em]"
          style={{ color: kindColor(card.kind, card.kind_color) }}
        >
          {card.kind_label}
        </span>
        {card.project ? (
          <span className="mono ml-auto truncate text-[10px] text-tx3">{card.project}</span>
        ) : null}
      </div>
      <p className="mb-2.5 text-[13px] font-medium leading-[1.45] text-tx">{card.title}</p>
      <div className="mb-2 flex items-center gap-2">
        <span className="mono rounded-[5px] px-[7px] py-0.5 text-[10px]" style={chip.style}>
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
            className="rounded-[5px] border border-white/[0.14] px-[7px] py-0.5 text-[10px] font-medium text-[#c6ccd8] transition-colors hover:bg-white/5 disabled:opacity-40"
          >
            ▶ Start
          </button>
        ) : card.score != null ? (
          <span className="mono text-[10px] text-tx3" title={card.breakdown}>
            ▲{Math.round(card.score)}
          </span>
        ) : null}
        {card.stale_days != null ? (
          <span
            className="mono rounded-[5px] px-[7px] py-0.5 text-[10px]"
            style={CHIP.bad}
            title={`No activity for ${card.stale_days} day(s)`}
          >
            stuck {card.stale_days}d
          </span>
        ) : null}
        {card.worker ? (
          <span
            className="mono ml-auto truncate text-[10px] text-acc"
            title={`running on ${card.worker}`}
          >
            ▸ {card.worker}
          </span>
        ) : null}
      </div>
      <LifecycleMeter t={card} />
    </a>
  )
}
