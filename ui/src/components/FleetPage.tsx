import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { deviceCaps, deviceControl, devicePair, fetchFleet, queryKeys } from '../api'
import type { Device } from '../types'
import { agoSec } from '../lib'
import { BTN, CARD, EmptyState, ErrorBanner, INPUT, PageHeader, Pill, toneForDevice } from './ui'

function SpendMeter({
  spend,
}: {
  spend: { spent: number; cap: number; halted: boolean; window_hours: number }
}) {
  const pct = spend.cap ? Math.min(100, Math.round((spend.spent / spend.cap) * 100)) : 0
  return (
    <div
      className={`card-enter mb-5 flex flex-wrap items-center gap-x-5 gap-y-2 rounded-xl border px-4 py-3 ${
        spend.halted ? 'border-red-200 bg-red-50' : 'border-slate-200 bg-white'
      }`}
    >
      <span className="microlabel">tokens · last {spend.window_hours}h</span>
      <div className="h-1.5 min-w-[10rem] flex-1 overflow-hidden rounded-full bg-slate-200/70">
        <i
          className={`block h-full rounded-full transition-[width] duration-500 ${
            spend.halted ? 'bg-red-500' : pct > 80 ? 'bg-amber-500' : 'bg-emerald-500'
          }`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="mono text-sm tabular-nums text-slate-700">
        <b className="font-semibold">{spend.spent.toLocaleString()}</b>
        <span className="text-slate-400"> / {spend.cap.toLocaleString()}</span>
      </span>
      {spend.halted ? <Pill tone="red">halted — over budget</Pill> : null}
    </div>
  )
}

function DeviceCard({ d, i }: { d: Device; i: number }) {
  const qc = useQueryClient()
  const [caps, setCaps] = useState(d.capabilities.join(', '))
  const invalidate = () => qc.invalidateQueries({ queryKey: queryKeys.fleet() })
  const ctl = useMutation({ mutationFn: deviceControl, onSuccess: invalidate })
  const save = useMutation({ mutationFn: deviceCaps, onSuccess: invalidate })
  const off = d.state === 'offline'

  return (
    <div
      className={`card-enter ${CARD} p-4 ${off ? 'border-dashed bg-slate-50/60' : ''}`}
      style={{ animationDelay: `${Math.min(i, 8) * 45}ms` }}
    >
      <div className="mb-2.5 flex items-center justify-between gap-2">
        <span className="mono text-[15px] font-semibold text-slate-900">{d.name}</span>
        <Pill tone={toneForDevice(d.state)} dot pulse={d.state === 'online'}>
          {d.state}
        </Pill>
      </div>

      <div className="mb-2.5 flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-500">
        <span>
          seen <b className="mono font-semibold text-slate-700">{agoSec(d.seen_sec)}</b>
        </span>
        {d.current_ticket ? (
          <span>
            running{' '}
            <a
              href={`#/ticket/${d.current_ticket}`}
              className="mono font-semibold text-sky-700 hover:underline"
            >
              #{d.current_ticket}
            </a>
          </span>
        ) : null}
        {d.version ? <span className="mono text-slate-400">v{d.version}</span> : null}
      </div>

      <div className="mb-2 flex flex-wrap gap-1">
        {d.capabilities.length ? (
          d.capabilities.map((c) => (
            <span
              key={c}
              className="mono rounded-md bg-slate-100 px-1.5 py-0.5 text-[11px] text-slate-600"
            >
              {c}
            </span>
          ))
        ) : (
          <span className="text-[11px] italic text-slate-400">no capabilities</span>
        )}
      </div>

      <form
        className="mb-3 flex gap-1.5"
        onSubmit={(e) => {
          e.preventDefault()
          save.mutate({ device: d.name, capabilities: caps })
        }}
      >
        <input
          value={caps}
          onChange={(e) => setCaps(e.target.value)}
          placeholder="dev, repos:*, heavy"
          className={`${INPUT} mono min-w-0 flex-1 px-2 py-1 text-xs`}
        />
        <button disabled={save.isPending} className={`${BTN.subtle} px-2.5 py-1 text-xs`}>
          Save
        </button>
      </form>

      <div className="flex gap-1.5">
        {d.state === 'paused' || d.state === 'draining' ? (
          <button
            onClick={() => ctl.mutate({ device: d.name, action: 'resume' })}
            disabled={ctl.isPending}
            className={`${BTN.go} px-3 py-1 text-xs`}
          >
            Resume
          </button>
        ) : (
          <>
            <button
              onClick={() => ctl.mutate({ device: d.name, action: 'pause' })}
              disabled={ctl.isPending}
              className={`${BTN.subtle} px-3 py-1 text-xs`}
            >
              Pause
            </button>
            <button
              onClick={() => ctl.mutate({ device: d.name, action: 'drain' })}
              disabled={ctl.isPending}
              className={`${BTN.subtle} px-3 py-1 text-xs text-amber-700 ring-amber-300 hover:bg-amber-50`}
            >
              Drain
            </button>
          </>
        )}
      </div>
    </div>
  )
}

function PairPanel() {
  const [name, setName] = useState('')
  const qc = useQueryClient()
  const pair = useMutation({
    mutationFn: devicePair,
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.fleet() }),
  })

  return (
    <div className="card-enter mt-6 rounded-xl border border-dashed border-slate-300 bg-white/60 p-4">
      <p className="microlabel mb-2">pair a new device</p>
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
          placeholder="device name (e.g. mbp)"
          className={`${INPUT} mono`}
        />
        <button disabled={pair.isPending || !name.trim()} className={BTN.primary}>
          Generate token
        </button>
      </form>
      {pair.data ? (
        <div className="mt-3 rounded-lg border border-emerald-200 bg-emerald-50 p-3">
          <p className="text-sm text-slate-700">
            On <b className="mono">{pair.data.device}</b>, open the menu-bar <b>Settings…</b>,
            set <b>Device</b> to <code className="mono">{pair.data.device}</code>, and paste
            this token:
          </p>
          <pre className="mono mt-2 overflow-auto rounded-md bg-white p-2 text-xs text-slate-800 ring-1 ring-emerald-200">
            {pair.data.token}
          </pre>
          <p className="mt-1.5 text-xs text-slate-500">
            Shown once — the hub stores only a hash. Re-pair to issue a new token.
          </p>
        </div>
      ) : null}
      <p className="mt-2 text-xs text-slate-400">
        Capabilities gate which tickets a device claims (e.g. <code className="mono">dev</code>,{' '}
        <code className="mono">repos:*</code>). Pause stops claiming; Drain finishes the current
        ticket first.
      </p>
    </div>
  )
}

export default function FleetPage() {
  const { data, isError } = useQuery({
    queryKey: queryKeys.fleet(),
    queryFn: fetchFleet,
    refetchInterval: 3000,
  })

  return (
    <div>
      <PageHeader
        title="Fleet"
        subtitle="Worker machines connected to this hub — capacity, budget, and controls."
      />
      {isError ? <ErrorBanner /> : null}
      {data ? <SpendMeter spend={data.spend} /> : null}
      {data && data.devices.length === 0 ? (
        <EmptyState
          glyph="⧉"
          title="No devices yet"
          hint="Start a worker pointed at this hub, or pair one below."
        />
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {(data?.devices ?? []).map((d, i) => (
            <DeviceCard key={d.name} d={d} i={i} />
          ))}
        </div>
      )}
      <PairPanel />
    </div>
  )
}
