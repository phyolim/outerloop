import { useEffect, useRef } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { observer, use$ } from '@legendapp/state/react'
import { addTicket, fetchTickets, queryKeys } from '../api'
import { ui$, resetForm } from '../state'
import { KINDS } from '../types'
import { BTN, INPUT, kindColor } from './ui'

// Coding kinds get a repo; research/ops don't. Mirrors outerloop/taxonomy.type_for.
const CODING = new Set(['feature', 'bug', 'chore'])

function CreateModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null)
  const qc = useQueryClient()
  const form = use$(ui$.form)

  // Projects for the datalist — piggybacks on the tickets query (already polled).
  const { data } = useQuery({
    queryKey: queryKeys.tickets(''),
    queryFn: () => fetchTickets(''),
    enabled: open,
  })
  const projects = data?.projects ?? []

  useEffect(() => {
    const d = ref.current
    if (!d) return
    if (open && !d.open) d.showModal()
    if (!open && d.open) d.close()
  }, [open])

  const mutation = useMutation({
    mutationFn: addTicket,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['tickets'] })
      qc.invalidateQueries({ queryKey: queryKeys.inbox() })
      resetForm()
      onClose()
    },
  })

  function submit(e: React.FormEvent) {
    e.preventDefault()
    const title = form.title.trim()
    if (!title) return
    mutation.mutate({
      title,
      kind: form.kind,
      body: form.body,
      project: form.project,
      repo_path: CODING.has(form.kind) ? form.repo_path : '',
      draft: !form.start,
    })
  }

  return (
    <dialog
      ref={ref}
      onClose={onClose}
      className="w-full max-w-[500px] rounded-xl border border-white/[0.12] bg-panel p-0 text-tx shadow-[0_25px_60px_rgba(0,0,0,0.5)]"
    >
      <form onSubmit={submit} className="p-5">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-[15px] font-semibold">Create item</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="rounded-md px-2 py-0.5 text-[13px] text-tx3 transition-colors hover:text-tx"
          >
            ✕
          </button>
        </div>

        <label className="microlabel mb-1.5 block">Summary</label>
        <input
          autoFocus
          value={form.title}
          onChange={(e) => ui$.form.title.set(e.target.value)}
          placeholder="What needs doing?"
          className={`${INPUT} mb-3.5 w-full`}
        />

        <label className="microlabel mb-1.5 block">Type</label>
        <div className="mb-3.5 flex flex-wrap gap-1.5">
          {KINDS.map((k) => {
            const active = form.kind === k.kind
            const bright = kindColor(k.kind)
            return (
              <button
                key={k.kind}
                type="button"
                onClick={() => ui$.form.kind.set(k.kind)}
                className="rounded-[7px] border px-[11px] py-1 text-[11px] font-semibold transition-colors"
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

        <label className="microlabel mb-1.5 block">Description</label>
        <textarea
          value={form.body}
          onChange={(e) => ui$.form.body.set(e.target.value)}
          rows={3}
          placeholder="Optional context for the worker."
          className={`${INPUT} mb-3.5 w-full resize-y`}
        />

        <div className="mb-4 grid gap-2.5 sm:grid-cols-2">
          <div>
            <label className="microlabel mb-1.5 block">Project</label>
            <input
              value={form.project}
              onChange={(e) => ui$.form.project.set(e.target.value)}
              placeholder="optional"
              list="project-list"
              className={`${INPUT} w-full`}
            />
            <datalist id="project-list">
              {projects.map((p) => (
                <option key={p} value={p} />
              ))}
            </datalist>
          </div>
          {CODING.has(form.kind) ? (
            <div>
              <label className="microlabel mb-1.5 block">Repository path</label>
              <input
                value={form.repo_path}
                onChange={(e) => ui$.form.repo_path.set(e.target.value)}
                placeholder="~/code/my-app"
                className={`${INPUT} mono w-full text-xs`}
              />
            </div>
          ) : null}
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-hairline pt-3.5">
          {mutation.isError ? (
            <span className="mr-auto text-xs text-bad">Failed to create item.</span>
          ) : (
            <label className="mr-auto flex items-center gap-[7px] text-xs text-tx2">
              <input
                type="checkbox"
                checked={form.start}
                onChange={(e) => ui$.form.start.set(e.target.checked)}
                className="accent-[#3ddc84]"
              />
              start now (skip draft)
            </label>
          )}
          <button type="button" onClick={onClose} className={BTN.subtle}>
            Cancel
          </button>
          <button
            type="submit"
            disabled={mutation.isPending || !form.title.trim()}
            className={BTN.primary}
          >
            {mutation.isPending ? 'Creating…' : form.start ? 'Create & start' : 'Create draft'}
          </button>
        </div>
      </form>
    </dialog>
  )
}

export default observer(CreateModal)
