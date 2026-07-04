import { useSyncExternalStore } from 'react'
import Board from './components/Board'
import DonePage from './components/DonePage'
import DecisionsPage from './components/DecisionsPage'
import TicketPage from './components/TicketPage'
import FleetPage from './components/FleetPage'
import ParkedPage from './components/ParkedPage'
import LogPage from './components/LogPage'
import InsightsPage from './components/InsightsPage'
import Nav from './components/Nav'

// ponytail: hash routing only — a handful of routes, no router lib needed.
function useHash(): string {
  return useSyncExternalStore(
    (cb) => {
      window.addEventListener('hashchange', cb)
      return () => window.removeEventListener('hashchange', cb)
    },
    () => window.location.hash,
  )
}

function route(path: string) {
  if (path.startsWith('/ticket/')) {
    const id = Number(path.split('/')[2])
    return Number.isFinite(id) ? <TicketPage id={id} /> : <Board />
  }
  if (path === '/decisions') return <DecisionsPage />
  if (path === '/done') return <DonePage />
  if (path === '/fleet') return <FleetPage />
  if (path === '/parked') return <ParkedPage />
  if (path === '/log') return <LogPage />
  if (path === '/insights') return <InsightsPage />
  return <Board />
}

export default function App() {
  const path = (useHash().replace(/^#/, '') || '/') as string
  return (
    <div className="workspace-bg min-h-screen text-slate-900">
      <div className="mx-auto max-w-7xl px-4 py-4">
        <Nav path={path} />
        {/* key on path so every route change replays the enter animation */}
        <main key={path} className="page-enter">
          {route(path)}
        </main>
      </div>
    </div>
  )
}
