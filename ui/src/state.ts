import { observable } from '@legendapp/state'
import type { Kind } from './types'

// Small client-only UI state (not server data — that lives in TanStack Query).
export const ui$ = observable({
  project: '', // '' = All projects
  form: {
    title: '',
    kind: 'feature' as Kind,
    body: '',
    project: '',
    repo_path: '',
    start: false, // checked = skip the draft stage and enter the pipeline now
  },
})

export function resetForm() {
  ui$.form.set({ title: '', kind: 'feature', body: '', project: '', repo_path: '', start: false })
}
