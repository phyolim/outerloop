// ponytail: path routing without a router lib — pushState + popstate, links stay
// plain <a href="/x">. The server serves index.html for every non-/ui/ path
// (history-API fallback), so deep links and reloads hit the same app.
import { useSyncExternalStore } from 'react'

const NAV_EVENT = 'app:navigate'

// Old bookmarks used hash routes (/#/fleet). Migrate them to real paths once, at
// module load, so they land on the same page they always did.
if (window.location.hash.startsWith('#/')) {
  history.replaceState(null, '', window.location.hash.slice(1))
}

// v1 pages that folded into the v2 IA: send old bookmarks to their new homes
// (Approvals → Inbox; On hold / Done → Board filter chips; Insights → Activity tab).
const LEGACY: Record<string, string> = {
  '/decisions': '/',
  '/parked': '/board',
  '/done': '/board',
  '/insights': '/log',
}
if (LEGACY[window.location.pathname]) {
  history.replaceState(null, '', LEGACY[window.location.pathname])
}

export function navigate(path: string): void {
  history.pushState(null, '', path)
  window.dispatchEvent(new Event(NAV_EVENT))
}

export function usePath(): string {
  return useSyncExternalStore(
    (cb) => {
      window.addEventListener('popstate', cb)
      window.addEventListener(NAV_EVENT, cb)
      return () => {
        window.removeEventListener('popstate', cb)
        window.removeEventListener(NAV_EVENT, cb)
      }
    },
    () => window.location.pathname,
  )
}

// Delegated click handler: turns plain same-origin <a> clicks into pushState
// navigations (no full page load) while leaving new-tab/middle/modified clicks
// and target=_blank external links alone.
export function onLinkClick(e: React.MouseEvent): void {
  if (e.defaultPrevented || e.button !== 0) return
  if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return
  const a = (e.target as HTMLElement).closest('a')
  if (!a || a.target === '_blank' || a.hasAttribute('download')) return
  if (a.origin !== window.location.origin) return
  e.preventDefault()
  navigate(a.pathname + a.search)
}
