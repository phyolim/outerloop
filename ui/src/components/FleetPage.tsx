import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  fetchFleet,
  fetchPairRequests,
  pairConfirm,
  pairIgnore,
  queryKeys,
  workerCaps,
  workerControl,
  workerDelete,
  workerPair,
  workerRename,
} from '../api'
import type { PairRequest, Worker } from '../types'
import { agoSec } from '../lib'
import { BTN, EmptyState, ErrorBanner, INPUT, PageHeader, PANEL, STATE_COLOR } from './ui'

/* Guided capability editing: current tags as chips (✕ removes), remaining known
   tags as one-click "+ tag" suggestions, and free typing (Enter / comma) to
   create a brand-new tag. A tag "exists" by being on a worker or a ticket, so
   creating one needs nothing server-side. */
function CapsEditor({
  initial,
  known,
  saving,
  failed,
  onSave,
  onCancel,
}: {
  initial: string[]
  known: string[]
  saving: boolean
  failed: boolean
  onSave: (tags: string[]) => void
  onCancel: () => void
}) {
  const [tags, setTags] = useState<string[]>(initial)
  const [text, setText] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)
  const typed = text.trim()
  const add = (t: string) => {
    const v = t.trim()
    if (v && !tags.includes(v)) setTags((s) => [...s, v])
    setText('')
  }
  const opts = known.filter(
    (k) => !tags.includes(k) && k.toLowerCase().includes(typed.toLowerCase()),
  )
  return (
    <div className="min-w-0 flex-1 py-1">
      <div
        className="flex cursor-text flex-wrap items-center gap-1.5 rounded-[7px] border border-white/10 bg-well px-2 py-1.5"
        onClick={() => inputRef.current?.focus()}
      >
        {tags.map((t) => (
          <span
            key={t}
            className="mono flex items-center gap-1 rounded-[5px] bg-white/[0.06] px-[7px] py-0.5 text-[10px] text-tx2"
          >
            {t}
            <button
              onClick={(e) => {
                e.stopPropagation()
                setTags((s) => s.filter((x) => x !== t))
              }}
              aria-label={`remove ${t}`}
              className="text-tx3 transition-colors hover:text-bad"
            >
              ✕
            </button>
          </span>
        ))}
        <input
          ref={inputRef}
          value={text}
          autoFocus
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ',') {
              e.preventDefault()
              if (typed) add(text)
            } else if (e.key === 'Backspace' && !text) {
              setTags((s) => s.slice(0, -1))
            } else if (e.key === 'Escape') {
              onCancel()
            }
          }}
          placeholder={tags.length ? '' : 'type a tag…'}
          className="mono min-w-[80px] flex-1 bg-transparent text-xs text-tx outline-none placeholder:text-tx3"
          aria-label="capability tags"
        />
      </div>
      <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
        {opts.map((k) => (
          <button
            key={k}
            onClick={() => add(k)}
            className="mono rounded-[5px] border border-dashed border-white/[0.14] px-[7px] py-0.5 text-[10px] text-tx3 transition-colors hover:border-acc/40 hover:text-acc"
          >
            + {k}
          </button>
        ))}
        {typed && !tags.includes(typed) && !known.includes(typed) ? (
          <button
            onClick={() => add(text)}
            className="mono rounded-[5px] border border-dashed border-acc/40 px-[7px] py-0.5 text-[10px] text-acc"
          >
            + create “{typed}”
          </button>
        ) : null}
        <span className="ml-auto flex items-center gap-1.5">
          {failed ? <span className="text-[11px] text-bad">Failed.</span> : null}
          <button
            onClick={() => onSave(tags)}
            disabled={saving}
            className="rounded-md bg-acc px-2.5 py-1 text-[11px] font-semibold text-ink transition-colors hover:bg-[#5ee79a] disabled:opacity-40"
          >
            {saving ? 'Saving…' : 'Save'}
          </button>
          <button
            onClick={onCancel}
            className="rounded-md border border-white/[0.12] px-2.5 py-1 text-[11px] text-tx2 transition-colors hover:text-tx"
          >
            Cancel
          </button>
        </span>
      </div>
    </div>
  )
}

function WorkerRow({ d, known }: { d: Worker; known: string[] }) {
  const qc = useQueryClient()
  const [editing, setEditing] = useState(false)
  const [renaming, setRenaming] = useState(false)
  const [newName, setNewName] = useState(d.name)
  const [confirmDel, setConfirmDel] = useState(false)
  const invalidate = () => qc.invalidateQueries({ queryKey: queryKeys.fleet() })
  const ctl = useMutation({ mutationFn: workerControl, onSuccess: invalidate })
  const save = useMutation({
    mutationFn: workerCaps,
    onSuccess: () => {
      setEditing(false)
      invalidate()
    },
  })
  const rename = useMutation({
    mutationFn: workerRename,
    onSuccess: () => {
      setRenaming(false)
      invalidate()
    },
  })
  const del = useMutation({ mutationFn: workerDelete, onSuccess: invalidate })
  const off = d.state === 'offline'

  if (renaming) {
    // Rename takes over the row: the input needs room, and the caveat matters —
    // a still-running worker heartbeats under its old name and would re-register.
    return (
      <div className="flex flex-wrap items-center gap-2.5 border-t border-hairline2 px-4 py-3 first:border-0">
        <form
          className="flex items-center gap-1.5"
          onSubmit={(e) => {
            e.preventDefault()
            const v = newName.trim()
            if (v && v !== d.name) rename.mutate({ worker: d.name, new_name: v })
            else setRenaming(false)
          }}
        >
          <input
            value={newName}
            autoFocus
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Escape') setRenaming(false)
            }}
            className="mono w-36 rounded-[7px] border border-white/10 bg-well px-2 py-1.5 text-xs text-tx outline-none focus:border-acc/40"
            aria-label="worker name"
          />
          <button
            disabled={rename.isPending || !newName.trim()}
            className="rounded-md bg-acc px-2.5 py-1 text-[11px] font-semibold text-ink transition-colors hover:bg-[#5ee79a] disabled:opacity-40"
          >
            {rename.isPending ? 'Renaming…' : 'Rename'}
          </button>
          <button
            type="button"
            onClick={() => setRenaming(false)}
            className="rounded-md border border-white/[0.12] px-2.5 py-1 text-[11px] text-tx2 transition-colors hover:text-tx"
          >
            Cancel
          </button>
        </form>
        {rename.isError ? (
          <span className="mono text-[11px] text-bad">{(rename.error as Error).message}</span>
        ) : (
          <span className="min-w-0 truncate text-[11px] text-tx3">
            also run <code className="mono">outerloop local worker {newName.trim() || '<name>'}</code> on
            that machine — a worker still running under “{d.name}” re-registers itself
          </span>
        )}
      </div>
    )
  }

  return (
    <div
      className="flex items-center gap-4 border-t border-hairline2 px-4 py-3 first:border-0"
      style={{ opacity: off ? 0.55 : 1 }}
    >
      <button
        onClick={() => {
          setNewName(d.name)
          setRenaming(true)
        }}
        title={`Rename worker${d.version ? ` (v${d.version})` : ''}`}
        className="mono w-[70px] shrink-0 truncate text-left text-[13px] font-semibold text-tx transition-colors hover:text-acc"
      >
        {d.name}
      </button>
      <span
        className="mono w-[82px] shrink-0 text-[11px] font-semibold"
        style={{ color: STATE_COLOR[d.state] ?? '#5d6470' }}
      >
        ● {d.state}
      </span>
      <span className="mono w-[76px] shrink-0 text-[11px] text-tx3">{agoSec(d.seen_sec)}</span>
      <span className="mono w-24 shrink-0 text-[11px]">
        {d.current_ticket ? (
          <a href={`/ticket/${d.current_ticket}`} className="text-info hover:text-[#8ecbfa]">
            ▸ #{d.current_ticket}
          </a>
        ) : null}
      </span>
      {editing ? (
        <CapsEditor
          initial={d.capabilities}
          known={known}
          saving={save.isPending}
          failed={save.isError}
          onSave={(tags) => save.mutate({ worker: d.name, capabilities: tags.join(', ') })}
          onCancel={() => setEditing(false)}
        />
      ) : (
        <button
          onClick={() => setEditing(true)}
          title="Edit capabilities"
          className="flex min-w-0 flex-1 flex-wrap gap-1.5 text-left"
        >
          {d.capabilities.length ? (
            d.capabilities.map((c) => (
              <span key={c} className="mono rounded-[5px] bg-white/[0.06] px-[7px] py-0.5 text-[10px] text-tx2">
                {c}
              </span>
            ))
          ) : (
            <span className="text-[11px] italic text-tx3">no capabilities</span>
          )}
        </button>
      )}
      <span className="flex shrink-0 items-center gap-1.5">
        {confirmDel ? (
          <>
            {del.isError ? (
              <span className="mono text-[11px] text-bad">{(del.error as Error).message}</span>
            ) : (
              <span className="text-[11px] text-tx3">
                Remove <b className="mono text-tx2">{d.name}</b> and revoke its token?
              </span>
            )}
            <button
              onClick={() => del.mutate(d.name)}
              disabled={del.isPending}
              className="rounded-md border border-bad/40 px-2.5 py-1 text-[11px] font-semibold text-bad transition-colors hover:bg-bad/10 disabled:opacity-40"
            >
              {del.isPending ? 'Removing…' : 'Remove'}
            </button>
            <button
              onClick={() => setConfirmDel(false)}
              className="rounded-md border border-white/[0.12] px-2.5 py-1 text-[11px] text-tx2 transition-colors hover:text-tx"
            >
              Cancel
            </button>
          </>
        ) : (
          <>
            {d.state === 'paused' || d.state === 'draining' ? (
              <button
                onClick={() => ctl.mutate({ worker: d.name, action: 'resume' })}
                disabled={ctl.isPending}
                className="rounded-md border border-acc/40 px-2.5 py-1 text-[11px] text-acc transition-colors hover:bg-acc/10 disabled:opacity-40"
              >
                Resume
              </button>
            ) : (
              <>
                <button
                  onClick={() => ctl.mutate({ worker: d.name, action: 'pause' })}
                  disabled={ctl.isPending}
                  className="rounded-md border border-white/[0.12] px-2.5 py-1 text-[11px] text-tx2 transition-colors hover:text-tx disabled:opacity-40"
                >
                  Pause
                </button>
                <button
                  onClick={() => ctl.mutate({ worker: d.name, action: 'drain' })}
                  disabled={ctl.isPending}
                  title="Finish the current ticket, then stop claiming"
                  className="rounded-md border border-warn/30 px-2.5 py-1 text-[11px] text-warn transition-colors hover:bg-warn/[0.08] disabled:opacity-40"
                >
                  Drain
                </button>
              </>
            )}
            <button
              onClick={() => setConfirmDel(true)}
              title="Remove from fleet — revokes its token; stop the worker first or it re-pairs on its next heartbeat"
              aria-label={`remove ${d.name}`}
              className="rounded-md border border-transparent px-1.5 py-1 text-[11px] text-tx3 transition-colors hover:border-bad/30 hover:text-bad"
            >
              ✕
            </button>
          </>
        )}
      </span>
    </div>
  )
}

const CODE_LEN = 6

/* One amber banner per pending LAN pairing request: type the 6-char code the
   worker is displaying, Pair mints the token, Ignore drops the request. The
   code cells are one invisible input rendered as boxes — focus lives in the
   real input so paste/backspace just work. */
function PairingBanner({ r, seed }: { r: PairRequest; seed: string[] }) {
  const qc = useQueryClient()
  const [code, setCode] = useState('')
  const [left, setLeft] = useState(r.expires_in)
  const inputRef = useRef<HTMLInputElement>(null)
  useEffect(() => setLeft(r.expires_in), [r.expires_in])
  useEffect(() => {
    const t = setInterval(() => setLeft((s) => Math.max(0, s - 1)), 1000)
    return () => clearInterval(t)
  }, [])

  const confirm = useMutation({
    mutationFn: () => pairConfirm({ request_id: r.request_id, code }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.pair() })
      qc.invalidateQueries({ queryKey: queryKeys.fleet() })
    },
    onError: () => setCode(''),
  })
  const drop = useMutation({
    mutationFn: () => pairIgnore(r.request_id),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.pair() }),
  })

  const chars = code.padEnd(CODE_LEN).split('')
  return (
    <div className="card-enter mb-3 rounded-[10px] border border-warn/30 bg-warn/5 px-4 py-[15px]">
      <div className="mb-1 flex items-center gap-2">
        <span className="mono text-[11px] font-semibold uppercase tracking-[0.1em] text-warn">
          ◈ pairing request
        </span>
        <span className="mono ml-auto text-[10px] text-tx3">{r.ip}</span>
      </div>
      <p className="mb-3 text-[13px] text-[#c6ccd8]">
        <span className="mono font-semibold text-tx">{r.name}</span>{' '}
        {r.host_info ? <span className="mono text-[11px] text-tx3">· {r.host_info} </span> : null}
        wants to join this fleet. Type the code shown on that machine.
      </p>
      <div className="flex flex-wrap items-center gap-2.5">
        <div
          className="relative flex cursor-text gap-[5px]"
          onClick={() => inputRef.current?.focus()}
        >
          <input
            ref={inputRef}
            value={code}
            autoFocus
            onChange={(e) =>
              setCode(
                e.target.value
                  .toUpperCase()
                  .replace(/[^0-9A-Z]/g, '')
                  .slice(0, CODE_LEN),
              )
            }
            onKeyDown={(e) => {
              if (e.key === 'Enter' && code.length === CODE_LEN) confirm.mutate()
            }}
            className="absolute inset-0 opacity-0"
            aria-label="pairing code"
          />
          {chars.map((ch, i) => (
            <span
              key={i}
              className="mono flex h-9 w-[29px] items-center justify-center rounded-[7px] bg-well text-[17px] font-bold text-tx"
              style={{
                border: `1px solid ${
                  i === Math.min(code.length, CODE_LEN - 1) && code.length < CODE_LEN
                    ? 'rgba(61,220,132,0.5)'
                    : 'rgba(255,255,255,0.14)'
                }`,
              }}
            >
              {ch.trim()}
            </span>
          ))}
        </div>
        <button
          onClick={() => confirm.mutate()}
          disabled={confirm.isPending || code.length < CODE_LEN || left === 0}
          className="rounded-[7px] bg-acc px-[15px] py-[7px] text-xs font-semibold text-ink transition-colors hover:bg-[#5ee79a] disabled:opacity-40"
        >
          Pair {r.name}
        </button>
        <button
          onClick={() => drop.mutate()}
          disabled={drop.isPending}
          className="rounded-[7px] border border-white/[0.12] px-3 py-[7px] text-xs text-tx2 transition-colors hover:text-tx disabled:opacity-40"
        >
          Ignore
        </button>
        <span className="mono ml-auto text-[11px] text-warn">
          {left > 0 ? `expires ${Math.floor(left / 60)}:${String(left % 60).padStart(2, '0')}` : 'expired'}
        </span>
      </div>
      {confirm.isError ? (
        <p className="mono mt-2 text-[11px] text-bad">{(confirm.error as Error).message}</p>
      ) : null}
      <p className="mono mt-[11px] text-[11px] text-tx3">
        seeds caps {seed.join(' · ')} — edit live here after pairing
      </p>
    </div>
  )
}

/* Manual name+token pairing — the footnote flow (remote workers not on this LAN). */
function PairPanel() {
  const [name, setName] = useState('')
  const qc = useQueryClient()
  const pair = useMutation({
    mutationFn: workerPair,
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.fleet() }),
  })

  return (
    <details className="mt-3">
      <summary className="cursor-pointer text-[11px] text-tx3 transition-colors hover:text-tx2">
        Remote worker not on this network? <span className="text-info">Pair manually with a token →</span>
      </summary>
      <div className="card-enter mt-3 rounded-[10px] border border-dashed border-white/10 p-4">
        <p className="microlabel mb-2">pair a new worker</p>
        <form
          className="flex flex-wrap gap-2"
          onSubmit={(e) => {
            e.preventDefault()
            if (name.trim()) pair.mutate(name.trim())
          }}
        >
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="worker name (e.g. mbp)"
            className={`${INPUT} mono`}
          />
          <button disabled={pair.isPending || !name.trim()} className={BTN.primary}>
            Generate token
          </button>
        </form>
        {pair.data ? (
          <div className="mt-3 rounded-lg border border-acc/30 bg-acc/5 p-3">
            <p className="text-[13px] text-[#c6ccd8]">
              On <b className="mono">{pair.data.worker}</b>, run{' '}
              <code className="mono">outerloop local worker {pair.data.worker}</code> and{' '}
              <code className="mono">outerloop local token &lt;token&gt;</code> — or open the
              menu-bar <b>Settings…</b>, set <b>Worker</b> to{' '}
              <code className="mono">{pair.data.worker}</code>, and paste this token:
            </p>
            <pre className="mono mt-2 overflow-auto rounded-md bg-well p-2 text-xs text-tx ring-1 ring-acc/30">
              {pair.data.token}
            </pre>
            <p className="mt-1.5 text-xs text-tx3">
              Shown once — the hub stores only a hash. Re-pair to issue a new token.
            </p>
          </div>
        ) : null}
        <p className="mt-2 text-xs text-tx3">
          Capabilities gate which tickets a worker claims (e.g. <code className="mono">dev</code>,{' '}
          <code className="mono">repos:*</code>). Pause stops claiming; Drain finishes the current
          ticket first.
        </p>
      </div>
    </details>
  )
}

export default function FleetPage() {
  const { data, isError } = useQuery({
    queryKey: queryKeys.fleet(),
    queryFn: fetchFleet,
  })
  // Pairing requests live in hub memory, not the DB, so the SSE stream never
  // announces them — this one query polls while the page is open.
  const { data: pair } = useQuery({
    queryKey: queryKeys.pair(),
    queryFn: fetchPairRequests,
    refetchInterval: 3000,
  })
  const pairRequests = pair?.requests ?? []

  return (
    <div>
      <PageHeader
        title="Fleet"
        subtitle="Worker machines connected to this hub — capacity, budget, and controls."
      />
      {isError ? <ErrorBanner /> : null}
      {data?.spend.halted ? (
        <div className="mb-4 flex items-center gap-2 rounded-[10px] border border-bad/30 bg-bad/5 px-3 py-2 text-[13px] text-bad">
          <i className="h-2 w-2 rounded-full bg-bad" />
          Fleet halted — token budget exhausted for this window.
        </div>
      ) : null}
      {data && data.workers.length === 0 ? (
        <EmptyState
          glyph="⧉"
          title="No workers yet"
          hint="Start a worker pointed at this hub, or pair one below."
        />
      ) : (
        <div className={`card-enter ${PANEL} mb-5`}>
          {(data?.workers ?? []).map((d) => (
            <WorkerRow key={d.name} d={d} known={data?.known_caps ?? []} />
          ))}
        </div>
      )}
      {pairRequests.map((r) => (
        <PairingBanner key={r.request_id} r={r} seed={pair?.seed_caps ?? []} />
      ))}
      <p className="text-[11px] text-tx3">
        New Macs on this LAN appear here automatically when they ask to join.
      </p>
      <PairPanel />
    </div>
  )
}
