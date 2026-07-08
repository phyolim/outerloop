import { useEffect } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import InboxPage from './components/InboxPage'
import Board from './components/Board'
import TicketPage from './components/TicketPage'
import FleetPage from './components/FleetPage'
import LogPage from './components/LogPage'
import ProjectsPage from './components/ProjectsPage'
import AgentsPage from './components/AgentsPage'
import Shell from './components/Nav'
import { onLinkClick, usePath } from './router'

// Subscribe once to the server's SSE stream; on any DB change, refetch every
// active query. Replaces per-query polling. EventSource auto-reconnects, and we
// invalidate on (re)connect too so a dropped stream can't leave stale data.
function useServerEvents() {
  const qc = useQueryClient()
  useEffect(() => {
    const es = new EventSource('/ui/events')
    const refetch = () => qc.invalidateQueries()
    es.onmessage = refetch
    es.onopen = refetch
    return () => es.close()
  }, [qc])
}

function route(path: string) {
  if (path.startsWith('/ticket/')) {
    const id = Number(path.split('/')[2])
    return Number.isFinite(id) ? <TicketPage id={id} /> : <InboxPage />
  }
  if (path === '/board') return <Board />
  if (path === '/projects' || path.startsWith('/projects/'))
    return <ProjectsPage name={decodeURIComponent(path.split('/')[2] ?? '')} />
  if (path === '/agents' || path.startsWith('/agents/'))
    return <AgentsPage name={decodeURIComponent(path.split('/')[2] ?? '')} />
  if (path === '/fleet') return <FleetPage />
  if (path === '/log') return <LogPage />
  return <InboxPage /> // '/' — the operator's home
}

export default function App() {
  const path = usePath() || '/'
  useServerEvents()
  return (
    // onClickCapture turns every plain same-origin <a> into a pushState navigation —
    // components keep writing ordinary hrefs, no <Link> wrapper needed.
    <div className="min-h-screen bg-ink text-tx" onClickCapture={onLinkClick}>
      <Shell path={path}>{route(path)}</Shell>
    </div>
  )
}
