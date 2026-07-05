import type { ReactNode } from 'react'
import type { Card } from '../types'
import TicketCard from './TicketCard'

/* No column container — just a header row (colored square dot, mono uppercase
   title, count) over a stack of cards. */
export default function Column({
  title,
  dot,
  cards,
  footer,
}: {
  title: string
  dot: string
  cards: Card[]
  footer?: ReactNode
}) {
  return (
    <div className="min-w-0">
      <div className="flex items-center gap-2 px-0.5 pb-2.5">
        <span className="h-[7px] w-[7px] rounded-[2px]" style={{ background: dot }} />
        <h2 className="mono text-[11px] font-semibold uppercase tracking-[0.1em] text-tx2">
          {title}
        </h2>
        <span className="mono text-[11px] text-tx3">{cards.length}</span>
      </div>
      <div className="flex flex-col gap-2.5">
        {cards.map((c, i) => (
          <TicketCard key={c.id} card={c} index={i} />
        ))}
        {cards.length === 0 ? <p className="py-3 text-center text-xs text-tx3">—</p> : null}
      </div>
      {footer ? <div className="mt-2.5 px-0.5">{footer}</div> : null}
    </div>
  )
}
