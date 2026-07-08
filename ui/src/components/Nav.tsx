import { useEffect, useState, type ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { fetchDecisions, fetchFleet, queryKeys, runTick, setKillSwitch } from '../api'
import CreateModal from './CreateModal'
import SearchOverlay from './SearchOverlay'

/* The Mission Control shell: sticky left rail (nav + budget + kill switch) and
   a blurred top status strip. The one fixed landmark every page shares —
   navigation IS the app's identity. */

// v2 IA: 6 rails organized around the operator's job. Approvals folds into Inbox
// (home); On hold / Done / Insights fold into Board / Activity as filters + tabs.
// Projects and Agents are the staffing pair: who works on what, and who "who" is.
const TABS = [
  { href: '/', label: 'Inbox', glyph: '◉', match: (p: string) => p === '/' },
  { href: '/board', label: 'Board', glyph: '▤', match: (p: string) => p === '/board' || p.startsWith('/ticket/') },
  { href: '/projects', label: 'Projects', glyph: '▦', match: (p: string) => p.startsWith('/projects') },
  { href: '/agents', label: 'Agents', glyph: '✦', match: (p: string) => p.startsWith('/agents') },
  { href: '/fleet', label: 'Fleet', glyph: '⌗', match: (p: string) => p === '/fleet' },
  { href: '/log', label: 'Activity', glyph: '∿', match: (p: string) => p === '/log' },
]

const isMac = navigator.platform.startsWith('Mac')

function fmtTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${Math.round(n / 1_000)}k`
  return String(n)
}

function RailLink({
  t,
  path,
  badge,
  live,
}: {
  t: (typeof TABS)[number]
  path: string
  badge?: number
  live?: string
}) {
  const active = t.match(path)
  return (
    <a
      href={t.href}
      className={`mb-0.5 flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-[13px] font-medium transition-colors ${
        active ? 'bg-acc/[0.08] text-tx' : 'text-tx2 hover:bg-white/5 hover:text-tx'
      }`}
    >
      <span className={`mono w-3.5 text-center text-xs ${active ? 'text-acc' : 'text-tx3'}`}>
        {t.glyph}
      </span>
      {t.label}
      {badge ? (
        <span className="mono ml-auto rounded-[5px] bg-warn/[0.16] px-1.5 text-[11px] font-semibold text-warn">
          {badge}
        </span>
      ) : live ? (
        <span className="mono ml-auto text-[10px] text-acc">{live}</span>
      ) : null}
    </a>
  )
}

function BudgetWidget() {
  const { data } = useQuery({ queryKey: queryKeys.fleet(), queryFn: fetchFleet })
  const qc = useQueryClient()
  const kill = useMutation({
    mutationFn: setKillSwitch,
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.fleet() }),
  })
  const spend = data?.spend
  const pct = spend?.cap ? Math.min(100, Math.round((spend.spent / spend.cap) * 100)) : 0
  const on = data?.kill_switch ?? false

  return (
    <div className="mt-auto flex flex-col gap-2.5">
      <div className="rounded-[10px] border border-hairline bg-well px-3 py-2.5">
        <div className="mb-1.5 flex items-baseline justify-between">
          <span className="microlabel whitespace-nowrap !tracking-[0.1em]">
            tokens {spend?.window_hours ?? 24}h
          </span>
          <span className="mono text-[11px] text-tx2">{pct}%</span>
        </div>
        <div className="h-1 overflow-hidden rounded-full bg-white/[0.08]">
          <i
            className={`block h-full rounded-full transition-[width] duration-500 ${spend?.halted ? 'bg-bad' : 'bg-acc'}`}
            style={{ width: `${pct}%` }}
          />
        </div>
        <p className="mono mt-1.5 text-[10px] text-tx3">
          {spend ? `${fmtTokens(spend.spent)} / ${fmtTokens(spend.cap)}` : '—'}
          {spend?.halted ? <span className="ml-1.5 font-semibold text-bad">halted</span> : null}
        </p>
      </div>
      <button
        onClick={() => kill.mutate(!on)}
        disabled={kill.isPending}
        title={on ? 'Workers claim nothing while this is on — click to resume' : 'Stop the fleet from claiming any new work'}
        className={`flex items-center justify-center gap-2 rounded-lg border py-[7px] text-xs font-semibold transition-colors disabled:opacity-40 ${
          on
            ? 'border-bad/60 bg-bad/15 text-bad hover:bg-bad/25'
            : 'border-bad/25 text-bad hover:bg-bad/10'
        }`}
      >
        ◍ {on ? 'Kill switch ON — resume' : 'Kill switch'}
      </button>
    </div>
  )
}

export default function Shell({ path, children }: { path: string; children: ReactNode }) {
  const qc = useQueryClient()
  const [createOpen, setCreateOpen] = useState(false)
  const [searchOpen, setSearchOpen] = useState(false)
  const { data: decisions, isError } = useQuery({
    queryKey: queryKeys.decisions(),
    queryFn: fetchDecisions,
  })
  const { data: fleet } = useQuery({ queryKey: queryKeys.fleet(), queryFn: fetchFleet })
  const need = decisions?.tickets.length ?? 0
  const busy = fleet?.workers.filter((w) => w.current_ticket != null).length ?? 0

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

  return (
    <div className="flex min-h-screen">
      <aside className="sticky top-0 flex h-screen w-[212px] shrink-0 flex-col border-r border-hairline px-2.5 pb-3.5 pt-4">
        <a href="/" className="flex items-center gap-2 px-2 pb-4">
          <i
            className={`h-2 w-2 rounded-full ${isError ? 'bg-bad' : 'bg-acc pulse-dot'}`}
            title={isError ? 'hub unreachable' : 'hub connected'}
          />
          <span className="mono text-sm font-semibold tracking-tight text-tx">
            outer<span className="text-acc">loop</span>
          </span>
        </a>

        {TABS.map((t) => (
          <RailLink
            key={t.href}
            t={t}
            path={path}
            badge={t.label === 'Inbox' ? need : 0}
            live={t.label === 'Fleet' && busy > 0 ? `${busy} busy` : undefined}
          />
        ))}

        <BudgetWidget />
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-10 flex h-12 shrink-0 items-center gap-4 border-b border-hairline bg-ink/90 px-5 backdrop-blur-md">
          <span className="mono text-xs text-tx2">
            <span className={isError ? 'text-bad' : 'text-acc'}>●</span>{' '}
            {isError ? 'hub unreachable' : 'hub connected'}
            {/* the prototype's "epoch" slot — filled with a real datum (hub version) */}
            {fleet?.version ? (
              <>
                <span className="mx-2 text-tx3">·</span>v{fleet.version}
              </>
            ) : null}
            <span className="mx-2 text-tx3">·</span>
            {busy} worker{busy === 1 ? '' : 's'} busy
          </span>
          <span className="ml-auto flex items-center gap-2">
            <button
              onClick={() => setSearchOpen(true)}
              className="flex items-center gap-2 rounded-[7px] border border-white/10 px-2.5 py-[5px] text-xs text-tx2 transition-colors hover:border-white/25 hover:text-tx"
            >
              <svg viewBox="0 0 16 16" className="h-[13px] w-[13px]" fill="none" stroke="currentColor" strokeWidth="1.6">
                <circle cx="7" cy="7" r="4.5" />
                <path d="M10.5 10.5L14 14" strokeLinecap="round" />
              </svg>
              <span className="hidden sm:inline">Search</span>
              <kbd className="mono hidden rounded bg-white/[0.08] px-1 text-[10px] sm:inline">
                {isMac ? '⌘K' : 'Ctrl K'}
              </kbd>
            </button>
            <button
              onClick={() => tick.mutate()}
              disabled={tick.isPending}
              title="Run a scheduler tick now"
              className="mono rounded-[7px] border border-white/10 px-2.5 py-[5px] text-xs text-tx2 transition-colors hover:border-acc/40 hover:text-acc disabled:opacity-40"
            >
              {tick.isPending ? 'ticking…' : '▸ tick'}
            </button>
            <button
              onClick={() => setCreateOpen(true)}
              className="whitespace-nowrap rounded-[7px] bg-acc px-3.5 py-[5px] text-xs font-semibold text-ink transition-colors hover:bg-[#5ee79a]"
            >
              + Create
            </button>
          </span>
        </header>

        <div className="mx-auto w-full max-w-[1200px] flex-1 px-6 pb-10 pt-6">
          {/* key on path so every route change replays the enter animation */}
          <main key={path} className="page-enter">
            {children}
          </main>
        </div>
      </div>

      <CreateModal open={createOpen} onClose={() => setCreateOpen(false)} />
      <SearchOverlay open={searchOpen} onClose={() => setSearchOpen(false)} />
    </div>
  )
}
