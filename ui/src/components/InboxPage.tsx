import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { answerDecision, fetchDecisions, fetchFleet, fetchInbox, queryKeys, retryTicket } from '../api'
import type { DecisionCard, RunningTicket } from '../types'
import { ago } from '../lib'
import { navigate } from '../router'
import { EmptyState, ErrorBanner, PANEL, DEEP } from './ui'
import { LifecycleMeter } from './lifecycle'

/* The Inbox — the operator's home. Three stacked sections read as one vertical
   scan: WAITING ON YOU (costliest first), IN PROGRESS, TODAY. */

// icon block + reason label + CTA style, keyed by the decision's reason class.
// CTA color encodes severity — solid green is reserved for irreversible advancement.
type Cls = { icon: string; iconBg: string; iconColor: string; reason: string; rank: number }
function reasonClass(reason: string): Cls {
  if (reason === 'error')
    return { icon: '!', iconBg: 'rgba(242,109,109,0.14)', iconColor: '#f26d6d', reason: 'FAILED', rank: 0 }
  if (reason === 'question')
    return { icon: '?', iconBg: 'rgba(94,177,247,0.14)', iconColor: '#5eb1f7', reason: 'QUESTION', rank: 2 }
  // merge / merge_gate / deploy / gate — any irreversible advancement
  const label = reason.replace(/_gate$/, '').replace(/_/g, ' ').toUpperCase()
  return { icon: '⇡', iconBg: 'rgba(245,184,67,0.14)', iconColor: '#f5b843', reason: label, rank: 1 }
}

/* PR link is clickable (the click you'd want before an irreversible approve) and
   checks are colored by their ACTUAL state — failing checks must never read green. */
function MetaLine({ c }: { c?: DecisionCard['context'] }) {
  if (!c || (!c.pr_url && !c.diff_stat && !c.checks)) return null
  return (
    <p className="mono mt-1 truncate text-[11px] text-tx3">
      {c.pr_url ? (
        <a
          href={c.pr_url}
          target="_blank"
          rel="noreferrer"
          className="text-info transition-colors hover:text-[#8ecbfa]"
        >
          {c.pr_url.replace(/^https?:\/\//, '')}
        </a>
      ) : null}
      {c.diff_stat ? (
        <span>
          {c.pr_url ? ' · ' : ''}
          {c.diff_stat}
        </span>
      ) : null}
      {c.checks ? (
        <span>
          {c.pr_url || c.diff_stat ? ' · ' : ''}checks{' '}
          <span className={`font-semibold ${c.checks_green ? 'text-acc' : 'text-bad'}`}>
            {c.checks}
          </span>
        </span>
      ) : null}
    </p>
  )
}

function WaitingRow({ t }: { t: DecisionCard }) {
  const qc = useQueryClient()
  const invalidate = () => qc.invalidateQueries()
  const approve = useMutation({
    mutationFn: () => answerDecision({ decision_id: t.decision_id!, action: 'approve', note: '' }),
    onSuccess: invalidate,
  })
  const retry = useMutation({ mutationFn: () => retryTicket(t.id), onSuccess: invalidate })
  const cls = reasonClass(t.reason)
  const open = () => navigate(`/ticket/${t.id}`)

  // CTA: green = irreversible advancement only; amber = retry; neutral = reply-in-thread.
  // The label names what approving DOES — only a merge decision merges.
  const cta =
    t.reason === 'error'
      ? { label: 'Retry', run: () => retry.mutate(), pending: retry.isPending, cls: 'bg-warn/[0.16] text-warn' }
      : t.reason === 'question'
        ? { label: 'Reply', run: open, pending: false, cls: 'bg-white/10 text-tx' }
        : {
            label: t.reason === 'merge' ? 'Approve & merge' : 'Approve',
            run: () => approve.mutate(),
            pending: approve.isPending,
            cls: 'bg-acc text-ink',
          }

  return (
    <div className="flex items-center gap-3.5 border-t border-hairline2 px-4 py-[13px] first:border-0 hover:bg-white/[0.02]">
      <span
        className="mono flex h-[30px] w-[30px] shrink-0 items-center justify-center rounded-lg text-[13px] font-bold"
        style={{ background: cls.iconBg, color: cls.iconColor }}
      >
        {cls.icon}
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2">
          <a
            href={`/ticket/${t.id}`}
            className="min-w-0 truncate text-[13px] font-semibold leading-[1.45] text-tx transition-colors hover:text-white"
          >
            <span className="mono text-tx3">#{t.id}</span> {t.title}
          </a>
          <span
            className="mono shrink-0 text-[10px] font-semibold uppercase tracking-[0.08em]"
            style={{ color: cls.iconColor }}
          >
            {cls.reason}
          </span>
        </div>
        {t.preview ? (
          <p className="mt-1 truncate text-[12.5px] leading-[1.55] text-tx2">{t.preview}</p>
        ) : null}
        <MetaLine c={t.context} />
      </div>
      <div className="flex shrink-0 flex-col items-end justify-between gap-1.5 self-stretch">
        <span className="mono pt-0.5 text-[10px] leading-[1.45] text-tx3">{ago(t.at)}</span>
        <span className="flex gap-1.5">
          <button
            onClick={cta.run}
            disabled={cta.pending}
            className={`whitespace-nowrap rounded-md px-[13px] py-[5px] text-xs font-semibold transition-[filter] hover:brightness-110 disabled:opacity-40 ${cta.cls}`}
          >
            {cta.pending ? '…' : cta.label}
          </button>
          <button
            onClick={open}
            className="whitespace-nowrap rounded-md border border-white/[0.12] px-3 py-[5px] text-xs text-tx2 transition-colors hover:text-tx"
          >
            Open
          </button>
        </span>
      </div>
    </div>
  )
}

function RunningRow({ r }: { r: RunningTicket }) {
  // Not everything 'active' is running: unleased work is queued, and work no online
  // worker can claim (caps mismatch) must say so instead of pretending to progress.
  const st =
    r.state === 'unclaimable'
      ? { icon: '!', cls: 'bg-warn/[0.14] text-warn' }
      : r.state === 'queued'
        ? { icon: '◦', cls: 'bg-white/[0.06] text-tx3' }
        : { icon: '▸', cls: 'bg-acc/[0.12] text-acc' }
  return (
    <a
      href={`/ticket/${r.id}`}
      className="flex items-center gap-3.5 border-t border-hairline2 px-4 py-[13px] first:border-0 transition-colors hover:bg-acc/[0.04]"
    >
      <span
        className={`mono flex h-[30px] w-[30px] shrink-0 items-center justify-center rounded-lg text-xs font-bold ${st.cls}`}
      >
        {st.icon}
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2">
          <span className="min-w-0 truncate text-[13px] font-semibold leading-[1.45] text-tx">
            <span className="mono text-tx3">#{r.id}</span> {r.title}
          </span>
          {r.sub_stage ? (
            <span className="mono shrink-0 rounded-[5px] bg-acc/[0.12] px-[7px] py-0.5 text-[10px] text-acc">
              {r.sub_stage}
            </span>
          ) : null}
          {r.state === 'unclaimable' ? (
            <span
              className="mono shrink-0 rounded-[5px] bg-warn/[0.14] px-[7px] py-0.5 text-[10px] font-semibold text-warn"
              title={`No online worker has the required capabilities: ${r.requires.join(', ') || '(none)'} — check Fleet`}
            >
              no capable worker
            </span>
          ) : r.state === 'queued' ? (
            <span className="mono shrink-0 rounded-[5px] bg-white/[0.06] px-[7px] py-0.5 text-[10px] text-tx3">
              queued
            </span>
          ) : null}
        </div>
        {r.last_line ? (
          <p className="mono mt-1 truncate text-[11px] leading-[1.55] text-tx2">
            <span className="text-info">▸</span> {r.last_line}
          </p>
        ) : null}
      </div>
      <div className="flex shrink-0 flex-col items-end justify-between gap-1.5 self-stretch">
        <span className="mono pt-0.5 text-[10px] leading-[1.45] text-tx3">
          {r.worker ? `on ${r.worker} · ` : ''}
          {ago(r.since)}
        </span>
        <LifecycleMeter t={{ status: 'active', sub_stage: r.sub_stage }} className="w-[62px] pb-[5px]" />
      </div>
    </a>
  )
}

export default function InboxPage() {
  const { data: dec, isError } = useQuery({
    queryKey: queryKeys.decisions(),
    queryFn: fetchDecisions,
  })
  const { data: inbox } = useQuery({ queryKey: queryKeys.inbox(), queryFn: fetchInbox })
  const { data: fleet } = useQuery({ queryKey: queryKeys.fleet(), queryFn: fetchFleet })

  // costliest first: failures → irreversible gates → questions, then recency
  const waiting = [...(dec?.tickets ?? [])].sort(
    (a, b) => reasonClass(a.reason).rank - reasonClass(b.reason).rank,
  )
  const running = inbox?.running ?? []
  const digest = inbox?.digest ?? []
  const drafts = inbox?.drafts ?? 0
  // "The loop is running itself" is a lie while it's stopped — say why instead.
  const stopped = fleet?.kill_switch
    ? 'the kill switch is on'
    : fleet?.spend?.halted
      ? 'the token budget is spent'
      : null

  const today = new Date().toLocaleDateString(undefined, {
    weekday: 'long',
    month: 'long',
    day: 'numeric',
  })
  const DIGEST_DOT = { ok: '✓', bad: '✗', muted: '·' } as const
  const DIGEST_COLOR = { ok: '#3ddc84', bad: '#f26d6d', muted: '#5d6470' } as const

  if (isError) return <ErrorBanner />

  return (
    <div>
      <header className="mb-6">
        <h1 className="text-[19px] font-semibold tracking-[-0.02em] text-tx">{today}</h1>
        <p className="mt-0.5 text-[13px] text-tx2">
          {waiting.length > 0
            ? `${waiting.length} item${waiting.length === 1 ? '' : 's'} ${waiting.length === 1 ? 'is' : 'are'} waiting on your approval or reply. ${stopped ? `The loop is paused — ${stopped}.` : 'The rest of the loop is running itself.'}`
            : stopped
              ? `Nothing is waiting on you, but the loop is paused — ${stopped}.`
              : 'Nothing is waiting on you. The loop is running itself.'}
        </p>
      </header>

      {waiting.length === 0 ? (
        stopped ? (
          <div className="mb-7 rounded-[10px] border border-warn/25 bg-warn/[0.05] p-[22px] text-center">
            <p className="mb-1 text-xl text-warn">⏸</p>
            <p className="mb-[3px] text-[13px] font-semibold text-tx">The loop is paused</p>
            <p className="mono text-[11px] text-tx3">{stopped} · resume from the left rail</p>
          </div>
        ) : (
          <div className="mb-7 rounded-[10px] border border-acc/20 bg-acc/[0.04] p-[22px] text-center">
            <p className="mb-1 text-xl text-acc">✓</p>
            <p className="mb-[3px] text-[13px] font-semibold text-tx">The loop is clear</p>
            <p className="mono text-[11px] text-tx3">
              nothing waiting on you · {running.length} in progress
            </p>
          </div>
        )
      ) : (
        <>
          <p className="mb-2.5 flex flex-wrap items-baseline gap-x-1.5">
            <span className="mono text-[11px] font-semibold uppercase tracking-[0.1em] text-warn">
              waiting on you · {waiting.length}
            </span>
            <span className="text-[11px] text-tx3">— costliest first: failures, merges, then questions</span>
          </p>
          <div className={`mb-7 ${PANEL} overflow-hidden`}>
            {waiting.map((t) => (
              <WaitingRow key={`${t.id}-${t.reason}`} t={t} />
            ))}
          </div>
        </>
      )}

      <p className="mb-2.5 mono text-[11px] font-semibold uppercase tracking-[0.1em] text-acc">
        in progress · {running.length}
      </p>
      {running.length === 0 ? (
        <div className="mb-7">
          <EmptyState glyph="▸" title="Nothing running" hint="Claimed work shows here while a worker is on it." />
        </div>
      ) : (
        <div className="mb-7 overflow-hidden rounded-[10px] border border-acc/[0.18] bg-panel">
          {running.map((r) => (
            <RunningRow key={r.id} r={r} />
          ))}
        </div>
      )}

      {drafts > 0 ? (
        <p className="mono -mt-4 mb-7 text-[11px] text-tx3">
          {drafts} draft{drafts === 1 ? '' : 's'} waiting to be started —{' '}
          <a href="/board" className="text-tx2 transition-colors hover:text-tx">
            start them on the Board →
          </a>
        </p>
      ) : null}

      <div className="mb-2.5 flex items-baseline gap-2.5">
        <p className="mono text-[11px] font-semibold uppercase tracking-[0.1em] text-tx3">today</p>
        <a href="/log" className="mono text-[11px] text-tx3 transition-colors hover:text-tx2">
          full log →
        </a>
      </div>
      {digest.length === 0 ? (
        <p className="mono text-xs text-tx3">— quiet so far today —</p>
      ) : (
        <div className={`${DEEP} overflow-hidden`}>
          {digest.map((e, i) => (
            <div
              key={`${e.id}-${i}`}
              className="flex items-baseline gap-3 border-t border-hairline2 px-3.5 py-2 first:border-0"
            >
              <span className="mono w-3.5 shrink-0 text-xs" style={{ color: DIGEST_COLOR[e.dot] }}>
                {DIGEST_DOT[e.dot]}
              </span>
              <span className="min-w-0 flex-1 text-[12.5px] text-tx2">
                <a
                  href={`/ticket/${e.id}`}
                  className="font-medium text-[#c6ccd8] transition-colors hover:text-white"
                >
                  #{e.id} {e.title}
                </a>{' '}
                — {e.what}
              </span>
              <span className="mono shrink-0 text-[10px] text-tx3">{ago(e.at)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
