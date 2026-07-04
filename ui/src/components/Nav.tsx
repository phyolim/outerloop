import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { fetchDecisions, queryKeys, runTick } from '../api'
import CreateModal from './CreateModal'
import SearchOverlay from './SearchOverlay'

/* The console bar: dark, monospace wordmark, live heartbeat. The one fixed
   landmark every page shares — navigation IS the app's identity. */

const TABS = [
  { href: '#/', label: 'Board', match: (p: string) => p === '/' || p.startsWith('/ticket/') },
  { href: '#/decisions', label: 'Approvals', match: (p: string) => p === '/decisions' },
  { href: '#/fleet', label: 'Fleet', match: (p: string) => p === '/fleet' },
  { href: '#/log', label: 'Activity', match: (p: string) => p === '/log' },
  { href: '#/insights', label: 'Insights', match: (p: string) => p === '/insights' },
]
const ARCHIVE = [
  { href: '#/parked', label: 'On hold', match: (p: string) => p === '/parked' },
  { href: '#/done', label: 'Done', match: (p: string) => p === '/done' },
]

const isMac = navigator.platform.startsWith('Mac')

export default function Nav({ path }: { path: string }) {
  const qc = useQueryClient()
  const [createOpen, setCreateOpen] = useState(false)
  const [searchOpen, setSearchOpen] = useState(false)
  const { data, isError } = useQuery({
    queryKey: queryKeys.decisions(),
    queryFn: fetchDecisions,
    refetchInterval: 4000,
  })
  const need = data?.tickets.length ?? 0

  const tick = useMutation({
    mutationFn: runTick,
    onSuccess: () => qc.invalidateQueries(),
  })

  // Jira-familiar shortcuts: "/" or ⌘K → search, "c" → create.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const el = e.target as HTMLElement
      const typing =
        el.tagName === 'INPUT' ||
        el.tagName === 'TEXTAREA' ||
        el.tagName === 'SELECT' || // native selects type-to-jump
        el.isContentEditable
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault()
        setSearchOpen(true)
      } else if (!typing && e.key === '/') {
        e.preventDefault()
        setSearchOpen(true)
      } else if (!typing && e.key === 'c' && !e.metaKey && !e.ctrlKey && !e.altKey) {
        setCreateOpen(true)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const link = (t: { href: string; label: string; match: (p: string) => boolean }, muted = false) => {
    const active = t.match(path)
    return (
      <a
        key={t.href}
        href={t.href}
        className={`relative whitespace-nowrap rounded-md px-2.5 py-1 text-[13px] font-medium transition-colors ${
          active
            ? 'bg-white/15 text-white'
            : muted
              ? 'text-slate-500 hover:bg-white/5 hover:text-slate-300'
              : 'text-slate-400 hover:bg-white/5 hover:text-slate-200'
        }`}
      >
        {t.label}
        {t.label === 'Approvals' && need > 0 ? (
          <span className="ml-1.5 rounded-full bg-amber-400 px-1.5 py-px text-[10px] font-bold text-slate-900">
            {need}
          </span>
        ) : null}
      </a>
    )
  }

  return (
    <>
      <nav className="mb-6 flex flex-wrap items-center gap-1 gap-y-1.5 rounded-xl bg-slate-900 px-3 py-2 shadow-md shadow-slate-900/10">
        <a href="#/" className="mr-3 flex items-center gap-2 pl-1">
          <i
            className={`h-2 w-2 rounded-full ${isError ? 'bg-red-500' : 'bg-emerald-400 pulse-dot'}`}
            title={isError ? 'hub unreachable' : 'hub connected'}
          />
          <span className="mono text-sm font-semibold tracking-tight text-white">
            outer<span className="text-emerald-400">loop</span>
          </span>
        </a>

        {TABS.map((t) => link(t))}
        <span className="mx-1.5 h-4 w-px bg-white/10" />
        {ARCHIVE.map((t) => link(t, true))}

        <button
          onClick={() => setSearchOpen(true)}
          className="ml-auto flex items-center gap-2 rounded-md border border-white/10 px-2.5 py-1 text-[12px] text-slate-400 transition-colors hover:border-white/25 hover:text-slate-200"
        >
          <svg
            viewBox="0 0 16 16"
            className="h-3.5 w-3.5"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.6"
          >
            <circle cx="7" cy="7" r="4.5" />
            <path d="M10.5 10.5L14 14" strokeLinecap="round" />
          </svg>
          <span className="hidden sm:inline">Search</span>
          <kbd className="mono hidden rounded bg-white/10 px-1 text-[10px] sm:inline">
            {isMac ? '⌘K' : 'Ctrl K'}
          </kbd>
        </button>

        <button
          onClick={() => setCreateOpen(true)}
          className="whitespace-nowrap rounded-md bg-emerald-500 px-3 py-1 text-[13px] font-semibold text-white transition-colors hover:bg-emerald-400"
        >
          + Create
        </button>

        <button
          onClick={() => tick.mutate()}
          disabled={tick.isPending}
          title="Run a scheduler tick now"
          className="mono rounded-md border border-white/15 px-2.5 py-1 text-[12px] text-slate-300 transition-colors hover:border-emerald-400/50 hover:text-emerald-300 disabled:opacity-40"
        >
          {tick.isPending ? 'ticking…' : '▸ tick'}
        </button>
      </nav>

      <CreateModal open={createOpen} onClose={() => setCreateOpen(false)} />
      <SearchOverlay open={searchOpen} onClose={() => setSearchOpen(false)} />
    </>
  )
}
