import { useQuery } from '@tanstack/react-query'
import { use$ } from '@legendapp/state/react'
import { fetchDone, queryKeys } from '../api'
import { ui$ } from '../state'
import { fmt } from '../lib'
import KindBadge from './KindBadge'
import { CARD, EmptyState, ErrorBanner, PageHeader } from './ui'

export default function DonePage() {
  const project = use$(ui$.project)
  const { data, isError } = useQuery({
    queryKey: queryKeys.done(project),
    queryFn: () => fetchDone(project),
  })
  const tickets = data?.tickets ?? []

  return (
    <div>
      <PageHeader
        title="Done"
        subtitle={`Completed items${project ? ` in ${project}` : ''}, newest first.`}
      />
      {isError ? <ErrorBanner /> : null}
      {tickets.length === 0 ? (
        <EmptyState glyph="◇" title="No completed items" hint="Finished work lands here." />
      ) : (
        <div className={`card-enter ${CARD} overflow-hidden`}>
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-left">
              <tr>
                <th className="microlabel px-3 py-2">#</th>
                <th className="microlabel px-3 py-2">Title</th>
                <th className="microlabel px-3 py-2">Kind</th>
                <th className="microlabel px-3 py-2">Project</th>
                <th className="microlabel px-3 py-2">Finished</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {tickets.map((t) => (
                <tr key={t.id} className="transition-colors hover:bg-slate-50">
                  <td className="mono px-3 py-2 text-xs text-slate-400">{t.id}</td>
                  <td className="px-3 py-2">
                    <a href={`#/ticket/${t.id}`} className="font-medium hover:underline">
                      {t.title}
                    </a>
                  </td>
                  <td className="px-3 py-2">
                    <KindBadge label={t.kind_label} color={t.kind_color} />
                  </td>
                  <td className="mono px-3 py-2 text-xs text-violet-600">{t.project ?? '—'}</td>
                  <td className="mono px-3 py-2 text-xs text-slate-500">{fmt(t.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
