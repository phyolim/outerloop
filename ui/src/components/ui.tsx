import type { ReactNode, SelectHTMLAttributes } from 'react'

/* The shared vocabulary every page speaks: one header shape, one pill shape,
   one empty state, one error banner. Consistency IS the design. */

export function PageHeader({
  title,
  subtitle,
  right,
}: {
  title: ReactNode
  subtitle?: ReactNode
  right?: ReactNode
}) {
  return (
    <header className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-slate-900">{title}</h1>
        {subtitle ? <p className="mt-0.5 text-sm text-slate-500">{subtitle}</p> : null}
      </div>
      {right ? <div className="flex items-center gap-2">{right}</div> : null}
    </header>
  )
}

/* Status semantics, used identically for tickets AND devices:
   green = running/online, amber = needs a human, red = failed,
   blue = claude/worker voice, slate = idle/off. */
const PILL: Record<string, string> = {
  green: 'bg-emerald-50 text-emerald-700 ring-emerald-200',
  amber: 'bg-amber-50 text-amber-700 ring-amber-200',
  red: 'bg-red-50 text-red-700 ring-red-200',
  blue: 'bg-sky-50 text-sky-700 ring-sky-200',
  slate: 'bg-slate-100 text-slate-500 ring-slate-200',
  violet: 'bg-violet-50 text-violet-700 ring-violet-200',
}
const DOT: Record<string, string> = {
  green: 'bg-emerald-500',
  amber: 'bg-amber-500',
  red: 'bg-red-500',
  blue: 'bg-sky-500',
  slate: 'bg-slate-400',
  violet: 'bg-violet-500',
}

export type PillTone = keyof typeof PILL

export function Pill({
  tone,
  children,
  dot,
  pulse,
}: {
  tone: string
  children: ReactNode
  dot?: boolean
  pulse?: boolean
}) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-semibold uppercase tracking-[0.05em] ring-1 ring-inset ${PILL[tone] ?? PILL.slate}`}
    >
      {dot ? (
        <i
          className={`h-1.5 w-1.5 rounded-full ${DOT[tone] ?? DOT.slate} ${pulse ? 'pulse-dot' : ''}`}
        />
      ) : null}
      {children}
    </span>
  )
}

/* Status vocabulary — familiar Jira/Monday terms, one source of truth. */
export const STATUS: Record<string, { label: string; tone: string }> = {
  inbox: { label: 'Backlog', tone: 'blue' },
  active: { label: 'In Progress', tone: 'green' },
  blocked: { label: 'Blocked', tone: 'amber' },
  done: { label: 'Done', tone: 'slate' },
  failed: { label: 'Failed', tone: 'red' },
  parked: { label: 'On hold', tone: 'slate' },
  draft: { label: 'Draft', tone: 'slate' },
}

export function StatusPill({ status, pulse }: { status: string; pulse?: boolean }) {
  const s = STATUS[status] ?? { label: status, tone: 'slate' }
  return (
    <Pill tone={s.tone} dot pulse={pulse}>
      {s.label}
    </Pill>
  )
}

export function toneForDevice(state: string): string {
  if (state === 'online') return 'green'
  if (state === 'paused') return 'amber'
  if (state === 'draining') return 'blue'
  return 'slate' // offline
}

export function ErrorBanner() {
  return (
    <div className="mb-4 flex items-center gap-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
      <i className="h-2 w-2 rounded-full bg-red-500" />
      Can't reach the hub — retrying…
    </div>
  )
}

export function EmptyState({ glyph, title, hint }: { glyph: string; title: string; hint?: string }) {
  return (
    <div className="card-enter rounded-xl border border-dashed border-slate-300 bg-white/60 px-6 py-10 text-center">
      <div className="mono text-2xl text-slate-300">{glyph}</div>
      <p className="mt-2 text-sm font-medium text-slate-600">{title}</p>
      {hint ? <p className="mt-1 text-xs text-slate-400">{hint}</p> : null}
    </div>
  )
}

/* One card surface everywhere. */
export const CARD = 'rounded-xl border border-slate-200 bg-white shadow-sm'

export const BTN = {
  primary:
    'rounded-lg bg-slate-900 px-3.5 py-1.5 text-sm font-medium text-white transition-colors hover:bg-slate-700 disabled:opacity-40',
  subtle:
    'rounded-lg bg-white px-3 py-1.5 text-sm font-medium text-slate-600 ring-1 ring-inset ring-slate-300 transition-colors hover:bg-slate-50 disabled:opacity-40',
  go: 'rounded-lg bg-emerald-600 px-3.5 py-1.5 text-sm font-medium text-white transition-colors hover:bg-emerald-500 disabled:opacity-40',
  danger:
    'rounded-lg bg-white px-3 py-1.5 text-sm font-medium text-red-600 ring-1 ring-inset ring-red-200 transition-colors hover:bg-red-50 disabled:opacity-40',
}

export const INPUT =
  'rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm outline-none transition-colors focus:border-slate-500'

/* Native select with proper chrome: appearance-none + our own chevron with
   breathing room (the UA default sits flush against the right edge). */
export function Select({
  className = '',
  children,
  ...props
}: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <span className={`relative inline-flex ${className}`}>
      <select {...props} className={`${INPUT} w-full appearance-none pr-9`}>
        {children}
      </select>
      <svg
        viewBox="0 0 16 16"
        className="pointer-events-none absolute right-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
      >
        <path d="M4 6l4 4 4-4" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </span>
  )
}
