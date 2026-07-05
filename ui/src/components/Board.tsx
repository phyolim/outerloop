import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { use$ } from '@legendapp/state/react'
import { fetchTickets, queryKeys } from '../api'
import { ui$ } from '../state'
import type { Card } from '../types'
import Column from './Column'
import { ErrorBanner, PageHeader, Select, DEEP, kindColor, stageChip, statusDot } from './ui'
import { LifecycleMeter } from './lifecycle'

/* v2 Board: every ticket in one place. Status is a filter (chips), not a page,
   and the same set renders as a 4-column board or a dense list. Both prefs
   persist in localStorage so the view survives reloads. */

type Filter = 'open' | 'all' | 'backlog' | 'active' | 'blocked' | 'failed' | 'onhold' | 'done'
type View = 'board' | 'list'

// card.status → the filter bucket it belongs to
function bucket(c: Card): Filter {
  if (c.status === 'inbox') return 'backlog'
  if (c.status === 'parked') return 'onhold'
  return c.status as Filter // active | blocked | failed | done
}
// failed counts as open — unresolved work must not hide behind the default filter
const OPEN = new Set<Filter>(['backlog', 'active', 'blocked', 'failed'])

function persisted<T extends string>(key: string, fallback: T): T {
  return (localStorage.getItem(key) as T) || fallback
}

function ListRow({ c }: { c: Card }) {
  const chip = stageChip(c)
  return (
    <a
      href={`/ticket/${c.id}`}
      className="flex items-center gap-3 border-t border-hairline2 px-3.5 py-[9px] first:border-0 transition-colors hover:bg-white/[0.02]"
    >
      <span className="w-2.5 shrink-0 text-[9px]" style={{ color: statusDot(c) }}>
        ●
      </span>
      <span className="mono w-[30px] shrink-0 text-[11px] text-tx3">{c.id}</span>
      <span
        className="mono w-16 shrink-0 text-[10px] font-semibold uppercase tracking-[0.08em]"
        style={{ color: kindColor(c.kind, c.kind_color) }}
      >
        {c.kind_label}
      </span>
      <span className="min-w-0 flex-1 truncate text-[13px] font-medium text-tx">{c.title}</span>
      <span className="mono shrink-0 rounded-[5px] px-[7px] py-0.5 text-[10px]" style={chip.style}>
        {chip.text}
      </span>
      <span className="mono w-[34px] shrink-0 text-right text-[10px] text-tx3">
        {c.score != null ? `▲${Math.round(c.score)}` : ''}
      </span>
      <span className="mono hidden w-[72px] shrink-0 truncate text-[10px] text-proj sm:inline">
        {c.project ?? ''}
      </span>
      <LifecycleMeter t={c} className="w-[62px] shrink-0" />
    </a>
  )
}

// Failed is exceptional: its chip (and board column) only exist while something
// is actually failed, and they read red.
const CHIPS: { key: Filter; label: string; alarm?: boolean }[] = [
  { key: 'open', label: 'Open' },
  { key: 'all', label: 'All' },
  { key: 'backlog', label: 'Backlog' },
  { key: 'active', label: 'In Progress' },
  { key: 'blocked', label: 'Blocked' },
  { key: 'failed', label: 'Failed', alarm: true },
  { key: 'onhold', label: 'On hold' },
  { key: 'done', label: 'Done' },
]
const BOARD_COLS: { title: string; key: Filter; dot: string }[] = [
  { title: 'Backlog', key: 'backlog', dot: '#5b9df9' },
  { title: 'In Progress', key: 'active', dot: '#3ddc84' },
  { title: 'Blocked', key: 'blocked', dot: '#f5b843' },
  { title: 'Done', key: 'done', dot: '#5d6470' },
]
const FAILED_COL = { title: 'Failed', key: 'failed' as Filter, dot: '#f26d6d' }

export default function Board() {
  const project = use$(ui$.project)
  const [filter, setFilter] = useState<Filter>(() => persisted<Filter>('board.filter', 'open'))
  const [view, setView] = useState<View>(() => persisted<View>('board.view', 'board'))
  const setF = (f: Filter) => {
    setFilter(f)
    localStorage.setItem('board.filter', f)
  }
  const setV = (v: View) => {
    setView(v)
    localStorage.setItem('board.view', v)
  }

  const { data, isError } = useQuery({
    queryKey: queryKeys.tickets(project),
    queryFn: () => fetchTickets(project),
  })
  const tickets = data?.tickets ?? []
  const counts = data?.counts
  const projects = data?.projects ?? []

  const chipCount = (k: Filter): number =>
    counts ? (k === 'all' ? counts.all : k === 'open' ? counts.open : counts[k]) : 0

  const rows = tickets.filter((c) => {
    const b = bucket(c)
    return filter === 'all' ? true : filter === 'open' ? OPEN.has(b) : b === filter
  })

  return (
    <div>
      <PageHeader
        title="Board"
        subtitle="Every ticket in one place — status is a filter, not a page."
        right={
          <Select value={project} onChange={(e) => ui$.project.set(e.target.value)}>
            <option value="">All projects</option>
            {projects.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </Select>
        }
      />

      {isError ? <ErrorBanner /> : null}

      <div className="mb-3.5 flex flex-wrap items-center gap-1.5">
        {CHIPS.map((c) => {
          if (c.alarm && chipCount(c.key) === 0) return null // Failed appears only when real
          const active = filter === c.key
          const tint = c.alarm
            ? { bg: 'rgba(242,109,109,0.1)', fg: '#f26d6d', border: 'rgba(242,109,109,0.35)' }
            : { bg: 'rgba(61,220,132,0.1)', fg: '#3ddc84', border: 'rgba(61,220,132,0.35)' }
          return (
            <button
              key={c.key}
              onClick={() => setF(c.key)}
              className="whitespace-nowrap rounded-full border px-[13px] py-1 text-xs font-medium transition-colors"
              style={
                active
                  ? { background: tint.bg, color: tint.fg, borderColor: tint.border }
                  : {
                      background: 'transparent',
                      color: c.alarm ? '#f26d6d' : '#9aa2b1',
                      borderColor: c.alarm ? 'rgba(242,109,109,0.25)' : 'rgba(255,255,255,0.1)',
                    }
              }
            >
              {c.label} <span className="mono text-[10px] opacity-70">{chipCount(c.key)}</span>
            </button>
          )
        })}
        <span className="ml-auto flex rounded-[7px] bg-white/[0.06] p-0.5">
          {(['board', 'list'] as View[]).map((v) => (
            <button
              key={v}
              onClick={() => setV(v)}
              className={`rounded-[5px] px-[11px] py-[3px] text-[11px] font-semibold transition-colors ${
                view === v ? 'bg-[#22262f] text-tx' : 'text-tx2 hover:text-tx'
              }`}
            >
              {v === 'board' ? '▤ Board' : '≡ List'}
            </button>
          ))}
        </span>
      </div>

      {view === 'board' ? (
        <div
          className={`grid grid-cols-1 gap-3.5 md:grid-cols-2 ${
            (counts?.failed ?? 0) > 0 ? 'xl:grid-cols-5' : 'xl:grid-cols-4'
          }`}
        >
          {[...((counts?.failed ?? 0) > 0 ? [FAILED_COL] : []), ...BOARD_COLS].map((col) => (
            <Column
              key={col.key}
              title={col.title}
              dot={col.dot}
              cards={tickets.filter((c) => bucket(c) === col.key)}
            />
          ))}
        </div>
      ) : (
        <div className={`card-enter ${DEEP} overflow-hidden`}>
          {rows.length === 0 ? (
            <p className="py-6 text-center text-xs text-tx3">— nothing here —</p>
          ) : (
            rows.map((c) => <ListRow key={c.id} c={c} />)
          )}
        </div>
      )}
      <p className="mt-2.5 px-0.5 text-[11px] text-tx3">
        Drafts show a muted dot — open one to edit and press Start. On-hold items restore from the
        ticket page.
      </p>
    </div>
  )
}
