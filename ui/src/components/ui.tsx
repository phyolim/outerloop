import { useRef, useState } from 'react'
import type { CSSProperties, ReactNode, SelectHTMLAttributes } from 'react'

import { uploadAttachment } from '../api'

/* The shared vocabulary every page speaks — the Mission Control voice:
   hairline-bordered panels, monospace data, colored mono status text.
   Consistency IS the design. */

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
        <h1 className="text-[19px] font-semibold tracking-[-0.02em] text-tx">{title}</h1>
        {subtitle ? <p className="mt-0.5 text-[13px] text-tx2">{subtitle}</p> : null}
      </div>
      {right ? <div className="flex items-center gap-2">{right}</div> : null}
    </header>
  )}

/* Kind hues brightened for the dark bg (originals in types.ts KINDS are the
   light-theme/server palette). Keyed by kind or label, case-insensitive. */
export const KIND_BRIGHT: Record<string, string> = {
  feature: '#4cc272',
  bug: '#f0824f',
  chore: '#5b9df9',
  research: '#a48ff2',
  ops: '#d9b13f',
}
export function kindColor(label: string, fallback?: string): string {
  return KIND_BRIGHT[label.toLowerCase()] ?? fallback ?? '#9aa2b1'
}

/* Status semantics as colored mono text — used identically for tickets AND
   workers: green = running/online, amber = needs a human, red = failed,
   blue = backlog/claude voice, muted = idle/off. */
export const STATE_COLOR: Record<string, string> = {
  active: '#3ddc84',
  online: '#3ddc84',
  blocked: '#f5b843',
  paused: '#f5b843',
  failed: '#f26d6d',
  inbox: '#5b9df9',
  draining: '#5eb1f7',
  done: '#5d6470',
  offline: '#5d6470',
  parked: '#5d6470',
  draft: '#5d6470',
}
export const STATUS_LABEL: Record<string, string> = {
  inbox: 'backlog',
  active: 'in progress',
  parked: 'on hold',
}

/* Stage-chip tints: running green / waiting amber / idle white-6% / draft dim. */
export const CHIP = {
  run: { background: 'rgba(61,220,132,0.12)', color: '#3ddc84' },
  wait: { background: 'rgba(245,184,67,0.14)', color: '#f5b843' },
  idle: { background: 'rgba(255,255,255,0.06)', color: '#9aa2b1' },
  draft: { background: 'rgba(255,255,255,0.06)', color: '#5d6470' },
  bad: { background: 'rgba(242,109,109,0.12)', color: '#f26d6d' },
} as const

type ChipCard = { status: string; sub_stage: string | null; draft: boolean; wait?: string | null }
/* The stage chip shown on every ticket card/row — one rule everywhere. */
export function stageChip(card: ChipCard): { text: string; style: CSSProperties } {
  if (card.status === 'blocked')
    return { text: `waiting: ${card.wait === 'clarification' ? 'question' : (card.wait ?? '?')}`, style: CHIP.wait }
  if (card.status === 'inbox' && card.draft) return { text: 'draft', style: CHIP.draft }
  if (card.status === 'parked') return { text: 'on hold', style: CHIP.idle }
  if (card.status === 'failed') return { text: 'failed', style: CHIP.bad }
  if (card.status === 'active') return { text: card.sub_stage ?? 'new', style: CHIP.run }
  if (card.status === 'done') return { text: card.sub_stage ?? 'merged', style: CHIP.idle }
  return { text: card.sub_stage ?? 'new', style: CHIP.idle }
}

/* Status dot color for list rows (draft backlog reads muted, not blue). */
export function statusDot(card: { status: string; draft: boolean }): string {
  if (card.status === 'active') return '#3ddc84'
  if (card.status === 'blocked') return '#f5b843'
  if (card.status === 'failed') return '#f26d6d'
  if (card.status === 'inbox') return card.draft ? '#5d6470' : '#5b9df9'
  if (card.status === 'done') return '#3a3f4a'
  return '#5d6470' // parked / on hold
}

export function ErrorBanner() {
  return (
    <div className="mb-4 flex items-center gap-2 rounded-[10px] border border-bad/30 bg-bad/5 px-3 py-2 text-[13px] text-bad">
      <i className="h-2 w-2 rounded-full bg-bad" />
      Can't reach the hub — retrying…
    </div>
  )
}

export function EmptyState({ glyph, title, hint }: { glyph: string; title: string; hint?: string }) {
  return (
    <div className="card-enter rounded-[10px] border border-dashed border-white/10 px-6 py-10 text-center">
      <div className="mono text-2xl text-tx3">{glyph}</div>
      <p className="mt-2 text-[13px] font-medium text-tx2">{title}</p>
      {hint ? <p className="mt-1 text-xs text-tx3">{hint}</p> : null}
    </div>
  )}

/* One card surface everywhere; DEEP for log tables / terminals. */
export const PANEL = 'rounded-[10px] border border-hairline bg-panel'
export const DEEP = 'rounded-[10px] border border-hairline bg-deep'

export const BTN = {
  // green fill, dark text — the go/approve voice
  primary:
    'rounded-[7px] bg-acc px-3.5 py-1.5 text-[13px] font-semibold text-ink transition-colors hover:bg-[#5ee79a] disabled:opacity-40',
  go: 'rounded-[7px] bg-acc px-3.5 py-1.5 text-[13px] font-semibold text-ink transition-colors hover:bg-[#5ee79a] disabled:opacity-40',
  subtle:
    'rounded-[7px] border border-white/[0.14] bg-transparent px-3 py-1.5 text-[13px] font-medium text-[#c6ccd8] transition-colors hover:bg-white/5 disabled:opacity-40',
  danger:
    'rounded-[7px] border border-bad/30 bg-transparent px-3 py-1.5 text-[13px] font-medium text-bad transition-colors hover:bg-bad/[0.08] disabled:opacity-40',
}

export const INPUT =
  'rounded-[7px] border border-white/10 bg-well px-3 py-1.5 text-[13px] text-tx outline-none transition-colors placeholder:text-tx3 focus:border-white/25'

/* Native select with proper chrome: appearance-none + our own chevron with
   breathing room (the UA default sits flush against the right edge). */
export function Select({
  className = '',
  children,
  ...props
}: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <span className={`relative inline-flex ${className}`}>
      <select
        {...props}
        className="w-full appearance-none rounded-[7px] border border-white/10 bg-well py-1.5 pl-3 pr-9 text-[13px] text-[#c6ccd8] outline-none transition-colors focus:border-white/25"
      >
        {children}
      </select>
      <svg
        viewBox="0 0 16 16"
        className="pointer-events-none absolute right-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-tx3"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
      >
        <path d="M4 6l4 4 4-4" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </span>
  )
}

/* Attach a file to any markdown-capable text field: uploads to /ui/attach and hands
   back a snippet to insert — images as ![name](url), everything else as [name](url). */
export function AttachButton({ onInsert }: { onInsert: (snippet: string) => void }) {
  const [busy, setBusy] = useState(false)
  const input = useRef<HTMLInputElement>(null)
  return (
    <>
      <input
        ref={input}
        type="file"
        className="hidden"
        onChange={async (e) => {
          const file = e.target.files?.[0]
          e.target.value = ''
          if (!file) return
          setBusy(true)
          try {
            const { url, name } = await uploadAttachment(file)
            const img = /\.(png|jpe?g|gif|webp|svg)$/i.test(name)
            onInsert(img ? `![${name}](${url})` : `[${name}](${url})`)
          } catch {
            window.alert('Upload failed.')
          } finally {
            setBusy(false)
          }
        }}
      />
      <button
        type="button"
        onClick={() => input.current?.click()}
        disabled={busy}
        className="text-xs text-tx3 underline-offset-2 transition-colors hover:text-tx1 hover:underline disabled:opacity-40"
        title="Attach a file or screenshot — inserted as markdown"
      >
        {busy ? 'Uploading…' : '📎 Attach'}
      </button>
    </>
  )
}
