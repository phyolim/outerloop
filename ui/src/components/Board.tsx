import { useQuery } from '@tanstack/react-query'
import { use$ } from '@legendapp/state/react'
import { fetchBoard, queryKeys } from '../api'
import { ui$ } from '../state'
import Column from './Column'
import { ErrorBanner, PageHeader, Select } from './ui'

export default function Board() {
  const project = use$(ui$.project)
  const { data, isError } = useQuery({
    queryKey: queryKeys.board(project),
    queryFn: () => fetchBoard(project),
    refetchInterval: 3000,
  })

  const projects = data?.projects ?? []
  const cols = data?.columns
  const counts = data?.counts

  return (
    <div>
      <PageHeader
        title="Board"
        subtitle="Everything the loop is working on, live."
        right={
          <Select value={project} onChange={(e) => ui$.project.set(e.target.value)}>
            <option value="">All projects</option>
            {projects.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </Select>
        }
      />

      {isError ? <ErrorBanner /> : null}

      {counts && counts.failed > 0 ? (
        <a
          href="#/decisions"
          className="mb-4 flex items-center gap-2 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-sm font-medium text-amber-800 transition-colors hover:bg-amber-100"
        >
          <i className="h-2 w-2 rounded-full bg-amber-500" />
          {counts.failed} failed item(s) need attention →
        </a>
      ) : null}

      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4">
        <Column title="Backlog" cards={cols?.inbox ?? []} />
        <Column title="In Progress" cards={cols?.active ?? []} />
        <Column title="Blocked" cards={cols?.blocked ?? []} accent />
        <Column
          title="Done"
          cards={cols?.done ?? []}
          footer={
            counts && counts.done_total > counts.done ? (
              <a
                href="#/done"
                className="mono block text-center text-xs text-slate-500 transition-colors hover:text-slate-700"
              >
                all {counts.done_total} done →
              </a>
            ) : null
          }
        />
      </div>
    </div>
  )
}
