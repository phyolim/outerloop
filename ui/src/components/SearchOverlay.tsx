import { useDeferredValue, useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { fetchSearch } from '../api'
import { ago } from '../lib'
import KindBadge from './KindBadge'
import { StatusPill } from './ui'

/* Jira-style quick search: an overlay palette, instant results across ALL
   statuses (the board only shows live + recent done). "/" or ⌘K opens it. */

function go(id: number, close: () => void) {
  window.location.hash = `#/ticket/${id}`
  close()
}

export default function SearchOverlay({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [q, setQ] = useState('')
  const [sel, setSel] = useState(0) // arrow-key selection index
  const dq = useDeferredValue(q) // cheap debounce — queries trail fast typing
  const inputRef = useRef<HTMLInputElement>(null)

  const { data, isFetching } = useQuery({
    queryKey: ['search', dq],
    queryFn: () => fetchSearch(dq),
    enabled: open && dq.trim().length > 0,
    staleTime: 10_000,
  })
  const results = dq.trim() ? (data?.tickets ?? []) : []

  useEffect(() => {
    if (open) {
      setQ('')
      setSel(0)
      // focus after the element renders
      requestAnimationFrame(() => inputRef.current?.focus())
    }
  }, [open])
  useEffect(() => setSel(0), [dq]) // new results → selection back to top

  if (!open) return null

  return (
    <div
      className="fixed inset-0 z-50 bg-slate-900/50 p-4 pt-[12vh]"
      onClick={onClose}
      onKeyDown={(e) => {
        if (e.key === 'Escape') onClose()
      }}
    >
      <div
        className="mx-auto max-w-xl overflow-hidden rounded-xl border border-slate-200 bg-white shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-2 border-b border-slate-100 px-3">
          <svg viewBox="0 0 16 16" className="h-4 w-4 shrink-0 text-slate-400" fill="none" stroke="currentColor" strokeWidth="1.6">
            <circle cx="7" cy="7" r="4.5" />
            <path d="M10.5 10.5L14 14" strokeLinecap="round" />
          </svg>
          <input
            ref={inputRef}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && results.length) go(results[Math.min(sel, results.length - 1)].id, onClose)
              else if (e.key === 'ArrowDown') {
                e.preventDefault()
                setSel((s) => Math.min(s + 1, results.length - 1))
              } else if (e.key === 'ArrowUp') {
                e.preventDefault()
                setSel((s) => Math.max(s - 1, 0))
              }
            }}
            placeholder="Search items — title, description, project, or #id"
            className="w-full bg-transparent py-3 text-sm outline-none placeholder:text-slate-400"
          />
          {isFetching ? <span className="mono shrink-0 text-[11px] text-slate-300">…</span> : null}
        </div>

        <div className="max-h-[50vh] overflow-y-auto">
          {results.map((t, i) => (
            <button
              key={t.id}
              onClick={() => go(t.id, onClose)}
              onMouseEnter={() => setSel(i)}
              className={`flex w-full items-center gap-2.5 px-3 py-2.5 text-left transition-colors ${
                i === sel ? 'bg-slate-100/80' : 'hover:bg-slate-50'
              }`}
            >
              <span className="mono shrink-0 text-xs text-slate-400">#{t.id}</span>
              <span className="min-w-0 flex-1 truncate text-sm font-medium text-slate-900">
                {t.title}
              </span>
              {t.project ? (
                <span className="mono hidden shrink-0 text-[11px] text-violet-600 sm:inline">
                  {t.project}
                </span>
              ) : null}
              <KindBadge label={t.kind_label} color={t.kind_color} />
              <StatusPill status={t.status} />
              <span className="mono hidden w-14 shrink-0 text-right text-[11px] text-slate-400 sm:inline">
                {ago(t.updated_at)}
              </span>
            </button>
          ))}
          {dq.trim() && !isFetching && results.length === 0 ? (
            <p className="px-3 py-6 text-center text-sm text-slate-400">
              No items match “{dq}”.
            </p>
          ) : null}
          {!dq.trim() ? (
            <p className="px-3 py-6 text-center text-sm text-slate-400">
              Type to search all items, including done and on-hold.
            </p>
          ) : null}
        </div>

        <div className="mono flex items-center gap-3 border-t border-slate-100 bg-slate-50/60 px-3 py-1.5 text-[11px] text-slate-400">
          <span>↵ open</span>
          <span>esc close</span>
          {results.length ? <span className="ml-auto">{results.length} result(s)</span> : null}
        </div>
      </div>
    </div>
  )
}
