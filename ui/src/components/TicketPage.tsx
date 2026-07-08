import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  answerDecision,
  closeTicket,
  commentTicket,
  dismissTicket,
  editTicket,
  fetchTicket,
  queryKeys,
  retryTicket,
  reviveTicket,
  saveFactors,
  startTicket,
} from '../api'
import type { AgentEvent, AgentRun, DecisionContext, Factors, Kind, ThreadComment, TicketThread } from '../types'
import { KINDS } from '../types'
import { ago, fmt } from '../lib'
import { stageDone } from './lifecycle'
import { BTN, EmptyState, ErrorBanner, INPUT, PANEL, DEEP, STATE_COLOR, STATUS_LABEL, kindColor } from './ui'

// Coding kinds get a repo; research/ops don't. Mirrors outerloop/taxonomy.type_for.
const CODING = new Set<Kind>(['feature', 'bug', 'chore'])

function fmtTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`
  return String(n)
}

function ContextBlock({ ctx }: { ctx?: DecisionContext }) {
  if (!ctx || Object.keys(ctx).length === 0) return null
  return (
    <div className="mono mt-2.5 flex flex-col gap-1 border-t border-hairline2 pt-2.5 text-[11px]">
      {ctx.pr_url ? (
        <span>
          <span className="mr-3 text-tx3">pr</span>
          <a href={ctx.pr_url} target="_blank" rel="noreferrer" className="text-info hover:text-[#8ecbfa]">
            {ctx.pr_url.replace(/^https?:\/\//, '')}
          </a>
        </span>
      ) : null}
      {ctx.diff_stat ? (
        <span className="text-tx3">
          <span className="mr-1.5">diff</span>
          <span className="text-tx2">{ctx.diff_stat}</span>
        </span>
      ) : null}
      {'checks_green' in ctx ? (
        <span className="text-tx3">
          <span className="mr-1.5">checks</span>
          <span className={`font-semibold ${ctx.checks_green ? 'text-acc' : 'text-bad'}`}>
            {ctx.checks ?? '?'}
          </span>
        </span>
      ) : null}
      {ctx.findings && ctx.findings.length ? (
        <ul className="list-disc pl-5 font-sans text-[12px] text-tx2">
          {ctx.findings.map((f, i) => (
            <li key={i}>{f}</li>
          ))}
        </ul>
      ) : null}
    </div>
  )
}

/* Thread voices: who is speaking, at a glance. */
const AUTHOR = {
  claude: { tag: 'claude', color: '#5eb1f7' },
  you: { tag: 'you', color: '#a78bfa' },
  system: { tag: 'system', color: '#f26d6d' },
} as const

function Comment({ c, i }: { c: ThreadComment; i: number }) {
  const a = AUTHOR[c.author]
  const verdictStyle =
    c.verdict === 'approved'
      ? { background: 'rgba(61,220,132,0.12)', color: '#3ddc84' }
      : c.verdict === 'rework'
        ? { background: 'rgba(245,184,67,0.14)', color: '#f5b843' }
        : { background: 'rgba(242,109,109,0.12)', color: '#f26d6d' }
  return (
    <div
      className={`card-enter ${PANEL} px-3.5 py-3`}
      style={{ animationDelay: `${Math.min(i, 8) * 50}ms` }}
    >
      <div className="mb-1.5 flex flex-wrap items-center gap-2 text-[11px]">
        <span className="mono font-semibold" style={{ color: a.color }}>
          {a.tag}
        </span>
        {c.kind && c.kind !== 'clarification' && c.kind !== 'error' ? (
          <span className="mono rounded-[5px] bg-white/[0.06] px-1.5 py-px text-[10px] text-tx2">
            {c.kind}
          </span>
        ) : null}
        {c.verdict ? (
          <span className="mono rounded-[5px] px-1.5 py-px text-[10px] font-semibold" style={verdictStyle}>
            {c.verdict}
          </span>
        ) : null}
        <span className="mono ml-auto text-[10px] text-tx3">{fmt(c.at)}</span>
      </div>
      {c.body ? (
        <p className="whitespace-pre-wrap text-[13px] leading-[1.6] text-[#c6ccd8]">{c.body}</p>
      ) : (
        <p className="text-[13px] italic text-tx3">(no note)</p>
      )}
      <ContextBlock ctx={c.context} />
    </div>
  )
}

function TicketEditor({
  ticket,
  isDraft,
  onDone,
}: {
  ticket: TicketThread['ticket']
  isDraft: boolean
  onDone: () => void
}) {
  const [f, setF] = useState({
    title: ticket.title,
    kind: ticket.kind,
    body: ticket.body,
    project: ticket.project ?? '',
    repo_path: ticket.repo_path ?? '',
  })
  const save = useMutation({
    mutationFn: () => editTicket({ ticket_id: ticket.id, ...f }),
    onSuccess: onDone,
  })
  return (
    <div className={`card-enter ${PANEL} mb-4 p-3.5`}>
      <p className="microlabel mb-2">{isDraft ? 'edit draft' : 'edit ticket'}</p>
      <input
        value={f.title}
        onChange={(e) => setF((v) => ({ ...v, title: e.target.value }))}
        className={`${INPUT} mb-3 w-full`}
      />
      {/* kind/type are structural once the ticket is in a handler's lifecycle —
          the server rejects changing them post-draft, so don't offer it. */}
      <div className={isDraft ? 'mb-3 flex flex-wrap gap-1.5' : 'hidden'}>
        {KINDS.map((k) => {
          const active = f.kind === k.kind
          const bright = kindColor(k.kind)
          return (
            <button
              key={k.kind}
              type="button"
              onClick={() => setF((v) => ({ ...v, kind: k.kind }))}
              className="rounded-[7px] border px-2.5 py-1 text-[11px] font-semibold transition-colors"
              style={
                active
                  ? { background: `${bright}1f`, color: bright, borderColor: bright }
                  : { color: bright, borderColor: 'rgba(255,255,255,0.12)' }
              }
            >
              {k.label}
            </button>
          )
        })}
      </div>
      <textarea
        value={f.body}
        onChange={(e) => setF((v) => ({ ...v, body: e.target.value }))}
        rows={3}
        placeholder="Description"
        className={`${INPUT} mb-3 w-full`}
      />
      <div className="mb-3 grid gap-3 sm:grid-cols-2">
        <input
          value={f.project}
          onChange={(e) => setF((v) => ({ ...v, project: e.target.value }))}
          placeholder="project (optional)"
          className={`${INPUT} w-full`}
        />
        {CODING.has(f.kind) ? (
          <input
            value={f.repo_path}
            onChange={(e) => setF((v) => ({ ...v, repo_path: e.target.value }))}
            placeholder="repository path"
            className={`${INPUT} mono w-full`}
          />
        ) : null}
      </div>
      <div className="flex items-center gap-2">
        <button
          onClick={() => save.mutate()}
          disabled={save.isPending || !f.title.trim()}
          className={BTN.primary}
        >
          {save.isPending ? 'Saving…' : 'Save'}
        </button>
        <button onClick={onDone} className={BTN.subtle}>
          Cancel
        </button>
        {save.isError ? (
          <span className="text-xs text-bad">
            {save.error instanceof Error ? save.error.message : 'Failed to save.'}
          </span>
        ) : null}
      </div>
    </div>
  )
}

const FACTOR_KEYS = ['impact', 'urgency', 'confidence', 'effort'] as const

/* Priority as 5-pip bars — click a pip to set the value, Save persists. */
function PriorityPanel({
  id,
  factors,
  breakdown,
  onSaved,
}: {
  id: number
  factors: Factors
  breakdown: string
  onSaved: () => void
}) {
  const [vals, setVals] = useState<Record<string, number>>({
    impact: factors.impact ?? 3,
    urgency: factors.urgency ?? 3,
    confidence: factors.confidence ?? 3,
    effort: factors.effort ?? 3,
  })
  const [dirty, setDirty] = useState(false)
  const save = useMutation({
    mutationFn: () =>
      saveFactors({
        ticket_id: id,
        impact: vals.impact,
        urgency: vals.urgency,
        confidence: vals.confidence,
        effort: vals.effort,
      }),
    onSuccess: () => {
      setDirty(false)
      onSaved()
    },
  })
  return (
    <div className={`${PANEL} px-3.5 py-3`}>
      <p className="microlabel mb-2.5">priority</p>
      <div className="mono flex flex-col gap-[7px] text-[11px]">
        {FACTOR_KEYS.map((k) => (
          <div key={k} className="flex items-center gap-2">
            <span className="w-[76px] text-tx3">{k}</span>
            <div className="flex flex-1 gap-[3px]">
              {[1, 2, 3, 4, 5].map((n) => (
                <button
                  key={n}
                  title={`${k} = ${n}`}
                  onClick={() => {
                    setVals((v) => ({ ...v, [k]: n }))
                    setDirty(true)
                  }}
                  className="h-[5px] flex-1 rounded-[2px] transition-colors"
                  style={{
                    background: n <= vals[k] ? '#5eb1f7' : 'rgba(255,255,255,0.08)',
                  }}
                />
              ))}
            </div>
            <span className="w-3 text-right text-[#c6ccd8]">{vals[k]}</span>
          </div>
        ))}
        <div className="mt-1 flex items-center justify-between">
          {breakdown ? (
            <span className="text-tx3">{breakdown}</span>
          ) : (
            <span className="italic text-tx3">unscored</span>
          )}
          {dirty ? (
            <button
              onClick={() => save.mutate()}
              disabled={save.isPending}
              className="rounded-[5px] border border-white/[0.14] px-2 py-0.5 font-sans text-[11px] text-[#c6ccd8] transition-colors hover:bg-white/5 disabled:opacity-40"
            >
              {save.isPending ? 'Saving…' : 'Save'}
            </button>
          ) : null}
        </div>
      </div>
    </div>
  )
}

/* Vertical lifecycle checklist: ● done, ◔ current, ○ future. Coding only. */
const LIFECYCLE = ['seed', 'groomed', 'implemented', 'reviewed', 'pr open', 'merge gate', 'merged']

function LifecyclePanel({ ticket }: { ticket: TicketThread['ticket'] }) {
  if (!CODING.has(ticket.kind)) return null
  const done = stageDone(ticket)
  const current = ticket.status === 'active' || ticket.status === 'blocked' ? done : -1
  return (
    <div className={`${PANEL} px-3.5 py-3`}>
      <p className="microlabel mb-2.5">lifecycle</p>
      <div className="flex flex-col gap-1.5">
        {LIFECYCLE.map((name, i) => {
          const state = i < done ? 'done' : i === current ? 'current' : 'future'
          const dot = state === 'current' ? '◔' : state === 'done' ? '●' : '○'
          const dotColor = state === 'current' ? '#f5b843' : state === 'done' ? '#3ddc84' : '#3a3f4a'
          const color = state === 'current' ? '#f5b843' : state === 'done' ? '#9aa2b1' : '#5d6470'
          return (
            <div key={name} className="mono flex items-center gap-2 text-[11px]">
              <span className="w-2.5 text-center" style={{ color: dotColor }}>
                {dot}
              </span>
              <span style={{ color }}>{name}</span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

/* What claude is doing right now: the streamed back-and-forth of the agent run.
   flex-col-reverse + newest-first data keeps the scroll pinned to the latest event. */
const EVENT_KIND = {
  text: { tag: 'claude', color: '#5eb1f7' },
  tool: { tag: 'tool →', color: '#f5b843' },
  tool_result: { tag: '← result', color: '#5d6470' },
} as const

function ActivityFeed({ events, live }: { events: AgentEvent[]; live: boolean }) {
  return (
    <div className="mb-4">
      <p className="microlabel mb-2">
        agent activity
        {live ? <span className="ml-1.5 animate-pulse font-semibold normal-case tracking-normal text-acc">· live</span> : null}
      </p>
      <div className={`${DEEP} flex max-h-80 flex-col-reverse overflow-y-auto`}>
        {[...events].reverse().map((e, i) => {
          const k = EVENT_KIND[e.kind as keyof typeof EVENT_KIND] ?? { tag: e.kind, color: '#9aa2b1' }
          return (
            <div
              key={events.length - i}
              className="flex gap-2.5 border-t border-hairline2 px-3 py-[7px] text-xs first:border-0"
            >
              <span className="mono w-16 shrink-0 text-[11px] font-semibold" style={{ color: k.color }}>
                {k.tag}
              </span>
              <span
                className={`min-w-0 flex-1 whitespace-pre-wrap break-words text-xs ${
                  e.kind === 'text' ? 'text-[#c6ccd8]' : 'mono line-clamp-2 text-tx2'
                }`}
              >
                {e.body}
              </span>
              <span className="mono w-[52px] shrink-0 text-right text-[10px] text-tx3" title={fmt(e.at)}>
                {ago(e.at)}
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function RunsPanel({ runs }: { runs: AgentRun[] }) {
  const total = runs.reduce((s, r) => s + r.tokens_in + r.tokens_out, 0)
  return (
    <div className={`${PANEL} px-3.5 py-3`}>
      <p className="microlabel mb-2.5">agent runs · {fmtTokens(total)} tok</p>
      <div className="mono flex flex-col gap-1.5 text-[11px]">
        {runs.map((r, i) => (
          <div
            key={i}
            className="flex justify-between"
            title={`${r.model ?? '—'} · exit ${r.exit_code ?? '…'} · ${fmt(r.at)}`}
          >
            <span className={r.exit_code != null && r.exit_code !== 0 ? 'text-bad' : 'text-tx2'}>
              {r.role}
            </span>
            <span className="text-tx3">{fmtTokens(r.tokens_in + r.tokens_out)}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

export default function TicketPage({ id }: { id: number }) {
  const qc = useQueryClient()
  const [note, setNote] = useState('')
  const { data, isError } = useQuery({
    queryKey: queryKeys.ticket(id),
    queryFn: () => fetchTicket(id),
  })

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: queryKeys.ticket(id) })
    qc.invalidateQueries({ queryKey: queryKeys.decisions() })
    qc.invalidateQueries({ queryKey: ['tickets'] })
    qc.invalidateQueries({ queryKey: queryKeys.inbox() })
  }

  const answer = useMutation({
    mutationFn: answerDecision,
    onSuccess: () => {
      setNote('')
      invalidate()
    },
  })
  const retry = useMutation({ mutationFn: () => retryTicket(id), onSuccess: invalidate })
  const dismiss = useMutation({ mutationFn: () => dismissTicket(id), onSuccess: invalidate })
  const close = useMutation({ mutationFn: () => closeTicket(id), onSuccess: invalidate })
  const start = useMutation({ mutationFn: () => startTicket(id), onSuccess: invalidate })
  const revive = useMutation({ mutationFn: () => reviveTicket(id), onSuccess: invalidate })
  const [editing, setEditing] = useState(false)
  const [opNote, setOpNote] = useState('')
  const comment = useMutation({
    mutationFn: () => commentTicket({ ticket_id: id, note: opNote }),
    onSuccess: () => {
      setOpNote('')
      invalidate()
    },
  })

  if (isError) return <ErrorBanner />
  if (data === null)
    return (
      <EmptyState glyph="?" title={`Item #${id} not found`} hint="It may have been deleted." />
    )
  if (!data) return <p className="text-[13px] text-tx3">Loading…</p>

  const { ticket, comments, pending, failed, steps, factors, breakdown, runs, events } = data
  const decisionId = pending?.decision_id
  const isDraft = ticket.status === 'inbox' && ticket.draft
  const state = isDraft ? 'draft' : ticket.status
  const kc = kindColor(ticket.kind, ticket.kind_color)

  return (
    <div>
      {/* Breadcrumb */}
      <nav className="mono mb-3 text-[11px] text-tx3">
        <a href="/" className="text-tx3 transition-colors hover:text-tx2">
          board
        </a>
        <span className="mx-1.5">/</span>
        <span className="text-tx2">#{ticket.id}</span>
      </nav>

      <div className="grid items-start gap-5 lg:grid-cols-[minmax(0,1fr)_264px]">
        {/* ---- main column ---- */}
        <div className="min-w-0">
          <header className="mb-4 flex items-start justify-between gap-3">
            <h1 className="text-[19px] font-semibold leading-[1.35] tracking-[-0.02em]">
              {ticket.title}
            </h1>
            {ticket.status !== 'done' ? (
              <div className="flex shrink-0 items-center gap-3">
                {!isDraft ? (
                  <button
                    onClick={() => setEditing((e) => !e)}
                    className="text-xs text-tx3 underline-offset-2 transition-colors hover:text-tx1 hover:underline"
                    title="Edit title, description, project, or repo"
                  >
                    {editing ? 'Close editor' : 'Edit'}
                  </button>
                ) : null}
                <button
                  onClick={() => {
                    if (window.confirm('Close this ticket? Any running work is stopped and it leaves the queue.'))
                      close.mutate()
                  }}
                  disabled={close.isPending}
                  className="text-xs text-tx3 underline-offset-2 transition-colors hover:text-bad hover:underline disabled:opacity-40"
                  title="No longer relevant — stop any running work and mark it done"
                >
                  {close.isPending ? 'Closing…' : 'Close ticket'}
                </button>
              </div>
            ) : null}
          </header>

          {editing ? (
            <TicketEditor
              ticket={ticket}
              isDraft={isDraft}
              onDone={() => {
                setEditing(false)
                invalidate()
              }}
            />
          ) : ticket.body ? (
            <div className={`${PANEL} mb-4 px-3.5 py-3`}>
              <p className="microlabel mb-1.5">description</p>
              <p className="whitespace-pre-wrap text-[13px] leading-[1.6] text-[#c6ccd8]">
                {ticket.body}
              </p>
            </div>
          ) : null}

          {events.length ? <ActivityFeed events={events} live={ticket.status === 'active'} /> : null}

          <p className="microlabel mb-2">thread</p>
          <div className="mb-4 flex flex-col gap-2.5">
            {comments.length === 0 ? (
              <p className="text-[13px] text-tx3">No comments yet.</p>
            ) : (
              comments.map((c, i) => <Comment key={i} c={c} i={i} />)
            )}
          </div>

          {/* Reply / action zone — what the human can do next. */}
          {isDraft ? (
            <div className="card-enter rounded-[10px] border border-white/[0.14] bg-white/[0.02] p-3.5">
              <p className="mono mb-2.5 text-[11px] font-semibold uppercase tracking-[0.1em] text-tx2">
                draft
              </p>
              <div className="flex flex-wrap items-center gap-2">
                <button onClick={() => start.mutate()} disabled={start.isPending} className={BTN.go}>
                  ▶ Start work
                </button>
                <button onClick={() => setEditing((e) => !e)} className={BTN.subtle}>
                  {editing ? 'Close editor' : 'Edit'}
                </button>
                <span className="text-xs text-tx3">
                  Start submits it to the pipeline — triage picks it up on the next tick.
                </span>
                {start.isError ? <span className="text-xs text-bad">Failed.</span> : null}
              </div>
            </div>
          ) : pending && pending.kind === 'clarification' ? (
            <div className="card-enter rounded-[10px] border border-info/30 bg-info/5 p-3.5">
              <p className="mono mb-2.5 text-[11px] font-semibold uppercase tracking-[0.1em] text-info">
                ? your reply
              </p>
              <textarea
                value={note}
                onChange={(e) => setNote(e.target.value)}
                rows={3}
                placeholder="Answer claude's question — sending resumes the worker."
                className={`${INPUT} w-full`}
              />
              <div className="mt-2.5 flex items-center gap-2">
                <button
                  onClick={() => answer.mutate({ decision_id: decisionId!, action: 'approve', note })}
                  disabled={answer.isPending || !note.trim()}
                  className={BTN.primary}
                >
                  {answer.isPending ? 'Sending…' : 'Send reply'}
                </button>
                <button
                  onClick={() => {
                    if (window.confirm('Decline to answer? The ticket will be marked failed — you can retry it later.'))
                      answer.mutate({ decision_id: decisionId!, action: 'reject', note: '' })
                  }}
                  disabled={answer.isPending}
                  className={BTN.subtle}
                  title="Not worth answering — the ticket fails instead of waiting forever"
                >
                  Decline
                </button>
                <span className="text-xs text-tx3">
                  The worker picks this ticket up again on the next tick.
                </span>
                {answer.isError ? <span className="text-xs text-bad">Failed to send.</span> : null}
              </div>
            </div>
          ) : pending ? (
            <div className="card-enter rounded-[10px] border border-warn/30 bg-warn/5 p-3.5">
              <p className="mono mb-2.5 text-[11px] font-semibold uppercase tracking-[0.1em] text-warn">
                ⏸ decision · {pending.kind}
              </p>
              <input
                value={note}
                onChange={(e) => setNote(e.target.value)}
                placeholder="comment (required for Request changes)"
                className={`${INPUT} mb-2.5 w-full`}
              />
              <div className="flex flex-wrap items-center gap-2">
                <button
                  onClick={() => answer.mutate({ decision_id: decisionId!, action: 'approve', note })}
                  disabled={answer.isPending}
                  className={BTN.go}
                >
                  {pending.kind === 'merge_gate' || pending.kind === 'merge' ? 'Approve & merge' : 'Approve'}
                </button>
                <button
                  onClick={() => answer.mutate({ decision_id: decisionId!, action: 'rework', note })}
                  disabled={answer.isPending || !note.trim()}
                  className={BTN.subtle}
                  title="Send your comment back to the worker for another pass — neither approves nor stops the work"
                >
                  Request changes
                </button>
                <button
                  onClick={() => answer.mutate({ decision_id: decisionId!, action: 'reject', note })}
                  disabled={answer.isPending}
                  className={BTN.danger}
                  title="Stop: the ticket is closed or failed depending on the gate"
                >
                  Reject
                </button>
                {answer.isError ? <span className="text-xs text-bad">Failed.</span> : null}
              </div>
            </div>
          ) : failed ? (
            <div className="card-enter rounded-[10px] border border-bad/30 bg-bad/5 p-3.5">
              <p className="mono mb-2.5 text-[11px] font-semibold uppercase tracking-[0.1em] text-bad">
                ! failed
              </p>
              {steps.length ? (
                <ol className="mono mb-3 space-y-0.5 text-xs text-tx2">
                  {steps.map((s, i) => (
                    <li key={i} className="truncate">
                      <span className="font-semibold text-[#c6ccd8]">{s.action}</span> — {s.reason}
                    </li>
                  ))}
                </ol>
              ) : null}
              <div className="flex items-center gap-2">
                <button onClick={() => retry.mutate()} disabled={retry.isPending} className={BTN.primary}>
                  Retry
                </button>
                <button onClick={() => dismiss.mutate()} disabled={dismiss.isPending} className={BTN.subtle}>
                  Dismiss
                </button>
                <span className="text-xs text-tx3">Retry re-runs the stage; Dismiss closes it.</span>
              </div>
            </div>
          ) : ticket.status === 'parked' ? (
            <div className="card-enter rounded-[10px] border border-white/[0.14] bg-white/[0.02] p-3.5">
              <p className="mono mb-2.5 text-[11px] font-semibold uppercase tracking-[0.1em] text-tx2">
                ⏸ on hold
              </p>
              <div className="flex items-center gap-2">
                <button onClick={() => revive.mutate()} disabled={revive.isPending} className={BTN.primary}>
                  Restore to backlog
                </button>
                <span className="text-xs text-tx3">
                  Triage set this aside — restoring sends it back to the backlog for scoring.
                </span>
                {revive.isError ? <span className="text-xs text-bad">Failed.</span> : null}
              </div>
            </div>
          ) : (
            <p className="mono text-xs text-tx3">
              — nothing to answer right now; the worker has it —
            </p>
          )}

          {/* Operator note: steer the item without waiting to be asked. Hidden while a
              clarification is pending — the reply box IS the note channel then. */}
          {!pending ? (
            <div className="mt-4">
              <textarea
                value={opNote}
                onChange={(e) => setOpNote(e.target.value)}
                rows={2}
                placeholder="Add a note — shown here and passed to the worker on its next run."
                className={`${INPUT} w-full`}
              />
              <div className="mt-1.5 flex items-center gap-2">
                <button
                  onClick={() => comment.mutate()}
                  disabled={comment.isPending || !opNote.trim()}
                  className={`${BTN.subtle} px-3 py-1 text-xs`}
                >
                  {comment.isPending ? 'Adding…' : 'Add note'}
                </button>
                {comment.isError ? <span className="text-xs text-bad">Failed.</span> : null}
              </div>
            </div>
          ) : null}
        </div>

        {/* ---- meta rail ---- */}
        <aside className="flex flex-col gap-3">
          <div className={`${PANEL} px-3.5 py-3`}>
            <p className="microlabel mb-2.5">status</p>
            <div className="mono flex flex-col gap-2 text-[11px]">
              <span className="flex justify-between">
                <span className="text-tx3">state</span>
                <span className="font-semibold" style={{ color: STATE_COLOR[state] ?? '#c6ccd8' }}>
                  ● {STATUS_LABEL[state] ?? state}
                </span>
              </span>
              {ticket.sub_stage ? (
                <span className="flex justify-between">
                  <span className="text-tx3">stage</span>
                  <span className="text-[#c6ccd8]">{ticket.sub_stage}</span>
                </span>
              ) : null}
              <span className="flex justify-between">
                <span className="text-tx3">kind</span>
                <span style={{ color: kc }}>{ticket.kind}</span>
              </span>
              {ticket.project ? (
                <span className="flex justify-between">
                  <span className="text-tx3">project</span>
                  <span className="text-proj">{ticket.project}</span>
                </span>
              ) : null}
              {ticket.repo_path ? (
                <span className="flex justify-between gap-2">
                  <span className="text-tx3">repo</span>
                  <span className="truncate text-[#c6ccd8]" title={ticket.repo_path}>
                    {ticket.repo_path}
                  </span>
                </span>
              ) : null}
            </div>
          </div>

          <LifecyclePanel ticket={ticket} />

          {/* key on breakdown: if the scorer (or another client) rescored while this page
              was open, remount so the pips resync instead of silently overwriting
              fresh factors with stale defaults on Save. */}
          <PriorityPanel
            key={breakdown || 'unscored'}
            id={id}
            factors={factors}
            breakdown={breakdown}
            onSaved={invalidate}
          />

          {runs.length ? <RunsPanel runs={runs} /> : null}
        </aside>
      </div>
    </div>
  )
}
