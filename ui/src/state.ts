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
    // checked (default) = enter the pipeline now — "drop tickets into one inbox" is
    // the promise; uncheck to park it as a draft you'll flesh out later.
    start: true,
  },
})

export function resetForm() {
  // keep `start` — it's a preference, not content; resetting it re-surprises every create
  ui$.form.assign({ title: '', kind: 'feature', body: '', project: '', repo_path: '' })
}
