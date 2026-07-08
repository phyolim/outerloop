import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { fetchProjects, queryKeys, setStaffing } from '../api'
import type { ProjectRow, ProjectsResponse } from '../types'
import { EmptyState, ErrorBanner, PANEL, PageHeader, Select } from './ui'

/* Projects — per-project staffing: which persona plays which pipeline role.
   The list shows each project's current pairings; the detail page is the team
   matrix, where every slot also explains what WOULD run (the server resolves it
   with the same code the agents use, so this page never lies). */

function repoShort(repo: string | null): string | null {
  if (!repo) return null
  return repo.replace(/^https?:\/\/(www\.)?github\.com\//, '').replace(/\.git$/, '')
}

function PairingChip({ role, persona }: { role: string; persona: string }) {
  return (
    <span className="mono rounded-[5px] bg-proj/10 px-1.5 py-0.5 text-[11px] font-semibold text-proj">
      {role} → {persona}
    </span>
  )
}

function ProjectList({ data }: { data: ProjectsResponse }) {
  if (!data.projects.length)
    return (
      <EmptyState
        glyph="▦"
        title="No projects yet"
        hint="Projects appear once tickets carry a project label."
      />
    )
  return (
    <div className={`${PANEL} divide-y divide-white/5`}>
      {data.projects.map((p) => {
        const pairs = Object.entries(p.staffing)
        return (
          <a
            key={p.name}
            href={`/projects/${encodeURIComponent(p.name)}`}
            className="flex flex-wrap items-center gap-x-3 gap-y-1.5 px-3.5 py-3 transition-colors hover:bg-white/[0.02]"
          >
            <span className="mono text-[13px] font-semibold text-proj">{p.name}</span>
            {repoShort(p.repo) ? (
              <span className="mono text-[11px] text-tx3">{repoShort(p.repo)}</span>
            ) : null}
            <span className="flex flex-wrap items-center gap-1.5">
              {pairs.length ? (
                pairs.map(([role, persona]) => (
                  <PairingChip key={role} role={role} persona={persona} />
                ))
              ) : (
                <span className="text-[12px] text-tx3">no staffing — defaults apply</span>
              )}
            </span>
            <span className="mono ml-auto flex items-center gap-3 text-[11px]">
              {!p.coverage ? <span className="text-warn">● no coverage</span> : null}
              <span className="text-tx3">
                {p.open} open ticket{p.open === 1 ? '' : 's'}
              </span>
            </span>
          </a>
        )
      })}
    </div>
  )
}

function RoleSlot({
  project,
  role,
  agents,
}: {
  project: ProjectRow
  role: string
  agents: string[]
}) {
  const qc = useQueryClient()
  const [editing, setEditing] = useState(false)
  const assign = useMutation({
    mutationFn: setStaffing,
    onSuccess: () => {
      setEditing(false)
      qc.invalidateQueries({ queryKey: queryKeys.projects() })
      qc.invalidateQueries({ queryKey: queryKeys.agents() })
    },
  })
  const assigned = project.staffing[role]
  const res = project.resolution[role]
  const explain =
    res?.persona != null
      ? `would use: ${res.persona} — ${res.why}`
      : 'would use: stock role prompt'
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 px-3.5 py-2.5">
      <span className="mono w-[76px] shrink-0 text-[11px] text-tx3">{role}</span>
      {assigned ? (
        <span className="mono flex items-center gap-1.5 rounded-[5px] bg-proj/10 px-2 py-0.5 text-[12px] font-semibold text-proj">
          {assigned}
          {res?.model ? <span className="font-normal text-proj/70">· {res.model}</span> : null}
        </span>
      ) : (
        <span className="mono rounded-[5px] border border-dashed border-white/20 px-2 py-0.5 text-[11px] text-tx3">
          unassigned
        </span>
      )}
      <span className="mono ml-auto text-[11px] text-tx3">{explain}</span>
      {editing ? (
        <Select
          autoFocus
          value={assigned ?? ''}
          disabled={assign.isPending}
          onChange={(e) =>
            assign.mutate({ project: project.name, role, persona: e.target.value })
          }
          className="w-[180px]"
        >
          <option value="">(default — resolve by glob)</option>
          {agents.map((a) => (
            <option key={a} value={a}>
              {a}
            </option>
          ))}
        </Select>
      ) : (
        <button
          onClick={() => setEditing(true)}
          className="rounded-[7px] border border-white/[0.14] px-2.5 py-1 text-[12px] font-medium text-[#c6ccd8] transition-colors hover:bg-white/5"
        >
          {assigned ? 'Change' : 'Assign'}
        </button>
      )}
    </div>
  )
}

function ProjectDetail({ data, name }: { data: ProjectsResponse; name: string }) {
  const project = data.projects.find((p) => p.name.toLowerCase() === name.toLowerCase())
  if (!project)
    return <EmptyState glyph="▦" title={`No project “${name}”`} hint="It may have no tickets yet." />
  return (
    <>
      <p className="mono mb-1 text-[11px] text-tx3">
        <a href="/projects" className="hover:text-tx2">
          projects
        </a>{' '}
        / {project.name}
      </p>
      <PageHeader
        title={<span className="mono text-proj">{project.name}</span>}
        subtitle="The team: which persona plays which role on this project."
      />
      <div className="grid items-start gap-5 lg:grid-cols-[minmax(0,1fr)_264px]">
        <div>
          <div className={`${PANEL} divide-y divide-white/5`}>
            <p className="microlabel px-3.5 pb-1 pt-3">team matrix</p>
            {data.roles.map((role) => (
              <RoleSlot key={role} project={project} role={role} agents={data.agents} />
            ))}
          </div>
          <p className="mt-2.5 text-[11px] leading-relaxed text-tx3">
            Resolution order: <span className="mono">project staffing</span> →{' '}
            <span className="mono">persona project-glob</span> →{' '}
            <span className="mono">generalist</span> →{' '}
            <span className="mono">stock role prompt</span>. Author and reviewer are always
            separate sessions — the same persona in both slots is fine; the same session is
            structurally impossible.
          </p>
        </div>
        <div className="flex flex-col gap-4">
          <div className={`${PANEL} px-3.5 py-3`}>
            <p className="microlabel mb-2.5">config</p>
            <div className="mono flex flex-col gap-1.5 text-[11px]">
              <div className="flex justify-between gap-3">
                <span className="text-tx3">repo</span>
                <span className="truncate text-tx2" title={project.repo ?? undefined}>
                  {repoShort(project.repo) ?? '—'}
                </span>
              </div>
              <div className="flex justify-between gap-3">
                <span className="text-tx3">staffing</span>
                <span className="truncate text-tx2" title={data.staffing_file}>
                  staffing.yml#{project.name.toLowerCase()}
                </span>
              </div>
              <div className="flex justify-between gap-3">
                <span className="text-tx3">open tickets</span>
                <span className="text-tx2">{project.open}</span>
              </div>
            </div>
          </div>
          <p className="text-[11px] leading-relaxed text-tx3">
            Staffing is hub-owned: assignments here ship to every worker on the next
            heartbeat — no restart. Personas are defined on the{' '}
            <a href="/agents" className="text-proj hover:underline">
              Agents
            </a>{' '}
            page.
          </p>
        </div>
      </div>
    </>
  )
}

export default function ProjectsPage({ name }: { name?: string }) {
  const { data, isError } = useQuery({ queryKey: queryKeys.projects(), queryFn: fetchProjects })
  if (isError) return <ErrorBanner />
  if (!data) return null
  if (name) return <ProjectDetail data={data} name={name} />
  return (
    <>
      <PageHeader
        title="Projects"
        subtitle="Per-project staffing — assign personas to roles, like staffing a team."
      />
      <ProjectList data={data} />
    </>
  )
}
