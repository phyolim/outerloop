import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  answerDecision,
  closeTicket,
  commentTicket,
  dismissTicket,
  editDraft,
  fetchTicket,
  queryKeys,
  retryTicket,
  saveFactors,
  startTicket,
} from '../api'
import type { AgentEvent, AgentRun, DecisionContext, Factors, Kind, ThreadComment, TicketThread } from '../types'
import { KINDS } from '../types'
import { ago, fmt } from '../lib'
import KindBadge from './KindBadge'
import { BTN, CARD, EmptyState, ErrorBanner, INPUT, Pill, Select, StatusPill } from './ui'

// Coding kinds get a repo; research/ops don't. Mirrors inbox/taxonomy.type_for.
const CODING = new Set<Kind>(['feature', 'bug', 'chore'])

function ContextBlock({ ctx }: { ctx?: DecisionContext }) {
  if (!ctx || Object.keys(ctx).length === 0) return null
  return (
    <div className="mt-2 space-y-1 border-t border-slate-100 pt-2 text-[13px]">
      {ctx.pr_url ? (
        <p>
          <span className="microlabel mr-1.5">pr</span>
          <a
            href={ctx.pr_url}
            target="_blank"
            rel="noreferrer"
            className="mono text-sky-700 hover:underline"
          >
            {ctx.pr_url}
          </a>
        </p>
      ) : null}
      {ctx.diff_stat ? <p className="mono text-xs text-slate-400">{ctx.diff_stat}</p> : null}
      {'checks_green' in ctx ? (
        <p>
          <span className="microlabel mr-1.5">checks</span>
          <span className={`mono text-xs font-semibold ${ctx.checks_green ? 'text-emerald-600' : 'text-red-600'}`}>
            {ctx.checks ?? '?'}
          </span>
        </p>
      ) : null}
      {ctx.findings && ctx.findings.length ? (
        <ul className="list-disc pl-5 text-slate-600">
          {ctx.findings.map((f, i) => (
            <li key={i}>{f}</li>
          ))}
        </ul>
      ) : null}
    </div>
  )
}

/* Thread avatars: who is speaking, at a glance. */
const AUTHOR = {
  claude: { initial: 'C', avatar: 'bg-sky-600', tag: 'claude' },
  you: { initial: 'Y', avatar: 'bg-slate-700', tag: 'you' },
  system: { initial: '!', avatar: 'bg-red-500', tag: 'system' },
} as const

function Comment({ c, i }: { c: ThreadComment; i: number }) {
  const a = AUTHOR[c.author]
  return (
    <div className="card-enter relative flex gap-3" style={{ animationDelay: `${Math.min(i, 8) * 50}ms` }}>
      <div
        className={`mono z-10 flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-xs font-bold text-white ${a.avatar}`}
      >
        {a.initial}
      </div>
      <div className={`${CARD} mb-3 min-w-0 flex-1 p-3`}>
        <div className="mb-1 flex flex-wrap items-center gap-2 text-xs">
          <span className="font-semibold text-slate-800">{a.tag}</span>
          {c.kind && c.kind !== 'clarification' && c.kind !== 'error' ? (
            <span className="mono rounded bg-slate-100 px-1.5 py-0.5 text-slate-500">{c.kind}</span>
          ) : null}
          {c.verdict ? (
            <Pill tone={c.verdict === 'approved' ? 'green' : c.verdict === 'rework' ? 'amber' : 'red'}>
              {c.verdict}
            </Pill>
          ) : null}
          <span className="mono ml-auto text-slate-400">{fmt(c.at)}</span>
        </div>
        {c.body ? (
          <p className="whitespace-pre-wrap text-sm leading-relaxed text-slate-800">{c.body}</p>
        ) : (
          <p className="text-sm italic text-slate-400">(no note)</p>
        )}
        <ContextBlock ctx={c.context} />
      </div>
    </div>
  )
}

function DraftEditor({
  ticket,
  onDone,
}: {
  ticket: TicketThread['ticket']
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
    mutationFn: () => editDraft({ ticket_id: ticket.id, ...f }),
    onSuccess: onDone,
  })
  return (
    <div className={`card-enter ${CARD} mb-6 p-3.5`}>
      <p className="microlabel mb-2">edit draft</p>
      <input
        value={f.title}
        onChange={(e) => setF((v) => ({ ...v, title: e.target.value }))}
        className={`${INPUT} mb-3 w-full`}
      />
      <div className="mb-3 flex flex-wrap gap-1.5">
        {KINDS.map((k) => {
          const active = f.kind === k.kind
          return (
            <button
              key={k.kind}
              type="button"
              onClick={() => setF((v) => ({ ...v, kind: k.kind }))}
              className="rounded-lg border px-2.5 py-1 text-xs font-medium transition-colors"
              style={
                active
                  ? { backgroundColor: k.color, color: 'white', borderColor: k.color }
                  : { color: k.color, borderColor: '#e2e8f0' }
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
        {save.isError ? <span className="text-xs text-red-600">Failed to save.</span> : null}
      </div>
    </div>
  )
}

const FACTOR_KEYS = ['impact', 'urgency', 'confidence', 'effort'] as const

function PriorityEditor({
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
  const save = useMutation({
    mutationFn: () =>
      saveFactors({
        ticket_id: id,
        impact: vals.impact,
        urgency: vals.urgency,
        confidence: vals.confidence,
        effort: vals.effort,
      }),
    onSuccess: onSaved,
  })
  return (
    <div className={`${CARD} mb-6 p-3.5`}>
      <p className="microlabel mb-2">priority</p>
      <div className="flex flex-wrap items-center gap-3">
        {FACTOR_KEYS.map((k) => (
          <label key={k} className="flex items-center gap-1.5 text-xs text-slate-500">
            {k}
            <Select
              value={vals[k]}
              onChange={(e) => setVals((v) => ({ ...v, [k]: Number(e.target.value) }))}
              className="w-[4.25rem]"
            >
              {[1, 2, 3, 4, 5].map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </Select>
          </label>
        ))}
        <button
          onClick={() => save.mutate()}
          disabled={save.isPending}
          className={`${BTN.subtle} px-3 py-1 text-xs`}
        >
          {save.isPending ? 'Saving…' : 'Save'}
        </button>
        {breakdown ? (
          <span className="mono ml-auto text-[11px] text-slate-400">{breakdown}</span>
        ) : (
          <span className="text-[11px] italic text-slate-300">unscored</span>
        )}
      </div>
    </div>
  )
}

/* What claude is doing right now: the streamed back-and-forth of the agent run.
   flex-col-reverse + newest-first data keeps the scroll pinned to the latest event. */
const EVENT_KIND = {
  text: { tag: 'claude', cls: 'text-sky-700' },
  tool: { tag: 'tool →', cls: 'text-amber-700' },
  tool_result: { tag: '← result', cls: 'text-slate-400' },
} as const

function ActivityFeed({ events, live }: { events: AgentEvent[]; live: boolean }) {
  return (
    <div className="mb-6">
      <p className="microlabel mb-2">
        activity
        {live ? <span className="ml-1.5 animate-pulse font-semibold text-emerald-600">· live</span> : null}
      </p>
      <div className={`${CARD} flex max-h-80 flex-col-reverse overflow-y-auto`}>
        {[...events].reverse().map((e, i) => {
          const k = EVENT_KIND[e.kind as keyof typeof EVENT_KIND] ?? {
            tag: e.kind,
            cls: 'text-slate-500',
          }
          return (
            <div key={events.length - i} className="flex gap-2 border-t border-slate-100 px-3 py-1.5 text-xs last:border-0">
              <span className={`mono w-16 shrink-0 font-semibold ${k.cls}`}>{k.tag}</span>
              <span
                className={`min-w-0 flex-1 whitespace-pre-wrap break-words text-slate-700 ${
                  e.kind === 'text' ? '' : 'mono line-clamp-2 text-slate-500'
                }`}
              >
                {e.body}
              </span>
              <span className="mono w-14 shrink-0 text-right text-slate-400" title={fmt(e.at)}>
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
    <details className="mt-6">
      <summary className="microlabel cursor-pointer select-none">
        agent runs · {runs.length} · {total.toLocaleString()} tokens
      </summary>
      <div className={`${CARD} mt-2 overflow-hidden`}>
        {runs.map((r, i) => (
          <div
            key={i}
            className="mono flex items-center gap-3 border-b border-slate-100 px-3 py-1.5 text-xs last:border-0"
          >
            <span className="w-16 shrink-0 font-semibold text-slate-700">{r.role}</span>
            <span className="min-w-0 flex-1 truncate text-slate-400">{r.model ?? '—'}</span>
            <span
              className={`w-8 shrink-0 text-center ${
                r.exit_code === 0 ? 'text-emerald-600' : r.exit_code == null ? 'text-slate-300' : 'text-red-600'
              }`}
            >
              {r.exit_code ?? '…'}
            </span>
            <span className="w-24 shrink-0 text-right tabular-nums text-slate-500">
              {(r.tokens_in + r.tokens_out).toLocaleString()} tok
            </span>
            <span className="w-14 shrink-0 text-right text-slate-400" title={fmt(r.at)}>
              {ago(r.at)}
            </span>
          </div>
        ))}
      </div>
    </details>
  )
}

export default function TicketPage({ id }: { id: number }) {
  const qc = useQueryClient()
  const [note, setNote] = useState('')
  const { data, isError } = useQuery({
    queryKey: queryKeys.ticket(id),
    queryFn: () => fetchTicket(id),
    // stop polling once we know the item doesn't exist
    refetchInterval: (q) => (q.state.data === null ? false : 4000),
  })

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: queryKeys.ticket(id) })
    qc.invalidateQueries({ queryKey: queryKeys.decisions() })
    qc.invalidateQueries({ queryKey: ['board'] })
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
  if (!data) return <p className="text-sm text-slate-400">Loading…</p>

  const { ticket, comments, pending, failed, steps, factors, breakdown, runs, events } = data
  const decisionId = pending?.decision_id
  const isDraft = ticket.status === 'inbox' && ticket.draft

  return (
    <div className="mx-auto max-w-3xl">
      {/* Breadcrumb + identity */}
      <nav className="mono mb-2 text-xs text-slate-400">
        <a href="#/" className="transition-colors hover:text-slate-600">
          board
        </a>
        <span className="mx-1">/</span>
        <span className="text-slate-600">#{ticket.id}</span>
      </nav>

      <header className="mb-5">
        <div className="flex items-start justify-between gap-3">
          <h1 className="text-xl font-semibold leading-snug tracking-tight">{ticket.title}</h1>
          <KindBadge label={ticket.kind_label} color={ticket.kind_color} />
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          {isDraft ? (
            <Pill tone="slate" dot>
              Draft
            </Pill>
          ) : (
            <StatusPill status={ticket.status} pulse={ticket.status === 'active'} />
          )}
          {ticket.sub_stage ? (
            <span className="mono rounded bg-slate-100 px-1.5 py-0.5 text-[11px] text-slate-500">
              {ticket.sub_stage}
            </span>
          ) : null}
          {ticket.project ? (
            <span className="mono text-[11px] text-violet-600">{ticket.project}</span>
          ) : null}
          {ticket.status !== 'done' ? (
            <button
              onClick={() => {
                if (window.confirm('Close this ticket? Any running work is stopped and it leaves the queue.'))
                  close.mutate()
              }}
              disabled={close.isPending}
              className="ml-auto text-xs text-slate-400 underline-offset-2 transition-colors hover:text-red-600 hover:underline disabled:opacity-40"
              title="No longer relevant — stop any running work and mark it done"
            >
              {close.isPending ? 'Closing…' : 'Close ticket'}
            </button>
          ) : null}
        </div>
      </header>

      {editing && isDraft ? (
        <DraftEditor
          ticket={ticket}
          onDone={() => {
            setEditing(false)
            invalidate()
          }}
        />
      ) : ticket.body ? (
        <div className={`${CARD} mb-6 p-3.5`}>
          <p className="microlabel mb-1.5">description</p>
          <p className="whitespace-pre-wrap text-sm leading-relaxed text-slate-700">
            {ticket.body}
          </p>
        </div>
      ) : null}

      {/* key on breakdown: if the scorer (or another client) rescored while this page
          was open, remount so the selects resync instead of silently overwriting
          fresh factors with stale defaults on Save. */}
      <PriorityEditor
        key={breakdown || 'unscored'}
        id={id}
        factors={factors}
        breakdown={breakdown}
        onSaved={invalidate}
      />

      {events.length ? <ActivityFeed events={events} live={ticket.status === 'active'} /> : null}

      <p className="microlabel mb-3">comments</p>
      {/* Timeline rail connects the conversation. */}
      <div className="relative">
        {comments.length > 1 ? (
          <i className="absolute bottom-3 left-3.5 top-1 w-px bg-slate-200" />
        ) : null}
        {comments.length === 0 ? (
          <p className="mb-4 text-sm text-slate-400">No comments yet.</p>
        ) : (
          comments.map((c, i) => <Comment key={i} c={c} i={i} />)
        )}
      </div>

      {/* Reply / action zone — what the human can do next. */}
      {isDraft ? (
        <div className="card-enter rounded-xl border border-slate-300 bg-slate-50/70 p-3.5">
          <p className="microlabel mb-2">draft</p>
          <div className="flex items-center gap-2">
            <button
              onClick={() => start.mutate()}
              disabled={start.isPending}
              className={BTN.go}
            >
              ▶ Start work
            </button>
            <button onClick={() => setEditing((e) => !e)} className={BTN.subtle}>
              {editing ? 'Close editor' : 'Edit'}
            </button>
            <span className="text-xs text-slate-400">
              Start submits it to the pipeline — triage picks it up on the next tick.
            </span>
            {start.isError ? <span className="text-xs text-red-600">Failed.</span> : null}
          </div>
        </div>
      ) : pending && pending.kind === 'clarification' ? (
        <div className="card-enter rounded-xl border border-sky-200 bg-sky-50/70 p-3.5">
          <p className="microlabel mb-2 !text-sky-600">your reply</p>
          <textarea
            value={note}
            onChange={(e) => setNote(e.target.value)}
            rows={3}
            placeholder="Answer claude's question — sending resumes the worker."
            className={`${INPUT} w-full`}
          />
          <div className="mt-2 flex items-center gap-2">
            <button
              onClick={() => answer.mutate({ decision_id: decisionId!, action: 'approve', note })}
              disabled={answer.isPending || !note.trim()}
              className={BTN.primary}
            >
              {answer.isPending ? 'Sending…' : 'Send reply'}
            </button>
            <span className="text-xs text-slate-400">
              The worker picks this ticket up again on the next tick.
            </span>
            {answer.isError ? <span className="text-xs text-red-600">Failed to send.</span> : null}
          </div>
        </div>
      ) : pending ? (
        <div className="card-enter rounded-xl border border-amber-200 bg-amber-50/70 p-3.5">
          <p className="microlabel mb-2 !text-amber-600">decision · {pending.kind}</p>
          <input
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="comment (required for Request changes)"
            className={`${INPUT} mb-2 w-full`}
          />
          <div className="flex items-center gap-2">
            <button
              onClick={() => answer.mutate({ decision_id: decisionId!, action: 'approve', note })}
              disabled={answer.isPending}
              className={BTN.go}
            >
              Approve
            </button>
            <button
              onClick={() => answer.mutate({ decision_id: decisionId!, action: 'rework', note })}
              disabled={answer.isPending || !note.trim()}
              className={BTN.primary}
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
            {answer.isError ? <span className="text-xs text-red-600">Failed.</span> : null}
          </div>
        </div>
      ) : failed ? (
        <div className="card-enter rounded-xl border border-red-200 bg-red-50/70 p-3.5">
          <p className="microlabel mb-2 !text-red-600">failed</p>
          {steps.length ? (
            <ol className="mono mb-3 space-y-0.5 text-xs text-slate-500">
              {steps.map((s, i) => (
                <li key={i} className="truncate">
                  <span className="font-semibold text-slate-600">{s.action}</span> — {s.reason}
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
            <span className="text-xs text-slate-400">Retry re-runs the stage; Dismiss closes it.</span>
          </div>
        </div>
      ) : (
        <p className="mono text-xs text-slate-400">
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
            {comment.isError ? <span className="text-xs text-red-600">Failed.</span> : null}
          </div>
        </div>
      ) : null}

      {runs.length ? <RunsPanel runs={runs} /> : null}
    </div>
  )
}
