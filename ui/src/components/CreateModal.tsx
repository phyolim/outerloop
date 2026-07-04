import { useEffect, useRef } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { observer, use$ } from '@legendapp/state/react'
import { addTicket, fetchBoard, queryKeys } from '../api'
import { ui$, resetForm } from '../state'
import { KINDS } from '../types'
import { BTN, INPUT } from './ui'

// Coding kinds get a repo; research/ops don't. Mirrors inbox/taxonomy.type_for.
const CODING = new Set(['feature', 'bug', 'chore'])

function CreateModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null)
  const qc = useQueryClient()
  const form = use$(ui$.form)

  // Projects for the datalist — piggybacks on the board query (already polled).
  const { data } = useQuery({
    queryKey: queryKeys.board(''),
    queryFn: () => fetchBoard(''),
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
      qc.invalidateQueries({ queryKey: ['board'] })
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
      className="w-full max-w-lg rounded-xl border border-slate-200 bg-white p-0 shadow-xl backdrop:bg-slate-900/50"
    >
      <form onSubmit={submit} className="p-5">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-base font-semibold tracking-tight">Create item</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="rounded-md px-2 py-0.5 text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-600"
          >
            ✕
          </button>
        </div>

        <label className="microlabel mb-1 block">Summary</label>
        <input
          autoFocus
          value={form.title}
          onChange={(e) => ui$.form.title.set(e.target.value)}
          placeholder="What needs doing?"
          className={`${INPUT} mb-4 w-full`}
        />

        <label className="microlabel mb-1 block">Type</label>
        <div className="mb-4 flex flex-wrap gap-1.5">
          {KINDS.map((k) => {
            const active = form.kind === k.kind
            return (
              <button
                key={k.kind}
                type="button"
                onClick={() => ui$.form.kind.set(k.kind)}
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

        <label className="microlabel mb-1 block">Description</label>
        <textarea
          value={form.body}
          onChange={(e) => ui$.form.body.set(e.target.value)}
          rows={3}
          placeholder="Optional context for the worker."
          className={`${INPUT} mb-4 w-full`}
        />

        <div className="mb-4 grid gap-3 sm:grid-cols-2">
          <div>
            <label className="microlabel mb-1 block">Project</label>
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
              <label className="microlabel mb-1 block">Repository path</label>
              <input
                value={form.repo_path}
                onChange={(e) => ui$.form.repo_path.set(e.target.value)}
                placeholder="~/code/my-app"
                className={`${INPUT} mono w-full`}
              />
            </div>
          ) : null}
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-slate-100 pt-4">
          {mutation.isError ? (
            <span className="mr-auto text-xs text-red-600">Failed to create item.</span>
          ) : (
            <label className="mr-auto flex items-center gap-1.5 text-xs text-slate-500">
              <input
                type="checkbox"
                checked={form.start}
                onChange={(e) => ui$.form.start.set(e.target.checked)}
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
            {mutation.isPending
              ? 'Creating…'
              : form.start
                ? 'Create & start'
                : 'Create draft'}
          </button>
        </div>
      </form>
    </dialog>
  )
}

export default observer(CreateModal)
