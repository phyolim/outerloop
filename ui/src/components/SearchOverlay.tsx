import { useDeferredValue, useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { fetchSearch } from '../api'
import { ago } from '../lib'
import { navigate } from '../router'
import { STATE_COLOR, STATUS_LABEL, kindColor } from './ui'

/* Jira-style quick search: an overlay palette, instant results across ALL
   statuses (the board only shows live + recent done). "/" or ⌘K opens it. */

function go(id: number, close: () => void) {
  navigate(`/ticket/${id}`)
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
      className="fixed inset-0 z-50 bg-[rgba(6,8,11,0.7)] p-4 pt-[12vh]"
      onClick={onClose}
      onKeyDown={(e) => {
        if (e.key === 'Escape') onClose()
      }}
    >
      <div
        className="mx-auto max-w-[560px] overflow-hidden rounded-xl border border-white/[0.12] bg-panel shadow-[0_25px_60px_rgba(0,0,0,0.5)]"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-2.5 border-b border-hairline px-3.5">
          <svg viewBox="0 0 16 16" className="h-[15px] w-[15px] shrink-0 text-tx3" fill="none" stroke="currentColor" strokeWidth="1.6">
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
            className="w-full bg-transparent py-[13px] text-[13px] text-tx outline-none placeholder:text-tx3"
          />
          {isFetching ? <span className="mono shrink-0 text-[11px] text-tx3">…</span> : null}
        </div>

        <div className="max-h-[50vh] overflow-y-auto">
          {results.map((t, i) => (
            <button
              key={t.id}
              onClick={() => go(t.id, onClose)}
              onMouseEnter={() => setSel(i)}
              className={`flex w-full items-center gap-2.5 px-3.5 py-2.5 text-left transition-colors ${
                i === sel ? 'bg-white/[0.05]' : 'hover:bg-white/[0.03]'
              }`}
            >
              <span className="mono shrink-0 text-xs text-tx3">#{t.id}</span>
              <span className="min-w-0 flex-1 truncate text-[13px] font-medium text-tx">
                {t.title}
              </span>
              {t.project ? (
                <span className="mono hidden shrink-0 text-[10px] text-proj sm:inline">
                  {t.project}
                </span>
              ) : null}
              <span
                className="mono shrink-0 text-[10px] font-semibold uppercase tracking-[0.08em]"
                style={{ color: kindColor(t.kind_label, t.kind_color) }}
              >
                {t.kind_label}
              </span>
              <span
                className="mono shrink-0 text-[10px] font-semibold"
                style={{ color: STATE_COLOR[t.status] ?? '#5d6470' }}
              >
                ● {STATUS_LABEL[t.status] ?? t.status}
              </span>
              <span className="mono hidden w-14 shrink-0 text-right text-[10px] text-tx3 sm:inline">
                {ago(t.updated_at)}
              </span>
            </button>
          ))}
          {dq.trim() && !isFetching && results.length === 0 ? (
            <p className="px-3.5 py-6 text-center text-[13px] text-tx3">No items match “{dq}”.</p>
          ) : null}
          {!dq.trim() ? (
            <p className="px-3.5 py-6 text-center text-[13px] text-tx3">
              Type to search all items, including done and on-hold.
            </p>
          ) : null}
        </div>

        <div className="mono flex items-center gap-3.5 border-t border-hairline bg-white/[0.02] px-3.5 py-[7px] text-[10px] text-tx3">
          <span>↵ open</span>
          <span>esc close</span>
          {results.length ? <span className="ml-auto">{results.length} result(s)</span> : null}
        </div>
      </div>
    </div>
  )
}
