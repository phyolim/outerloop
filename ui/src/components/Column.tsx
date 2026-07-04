import type { ReactNode } from 'react'
import type { Card } from '../types'
import TicketCard from './TicketCard'

export default function Column({
  title,
  cards,
  footer,
  accent,
}: {
  title: string
  cards: Card[]
  footer?: ReactNode
  accent?: boolean
}) {
  return (
    <div
      className={`flex flex-col rounded-xl p-2 ring-1 ring-inset ${
        accent && cards.length > 0
          ? 'bg-amber-50/50 ring-amber-200/70'
          : 'bg-slate-100/70 ring-slate-200/60'
      }`}
    >
      <div className="mb-2 flex items-center justify-between px-1.5 pt-0.5">
        <h2 className="microlabel !text-slate-500">{title}</h2>
        <span className="mono rounded-full bg-white px-2 text-xs tabular-nums text-slate-500 ring-1 ring-slate-200">
          {cards.length}
        </span>
      </div>
      <div className="flex flex-1 flex-col gap-2">
        {cards.map((c, i) => (
          <TicketCard key={c.id} card={c} index={i} />
        ))}
        {cards.length === 0 ? (
          <p className="px-1.5 py-3 text-center text-xs text-slate-400">—</p>
        ) : null}
      </div>
      {footer ? <div className="mt-2 px-1 pb-0.5">{footer}</div> : null}
    </div>
  )
}
