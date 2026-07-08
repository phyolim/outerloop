import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { fetchAgents, queryKeys, saveAgent } from '../api'
import type { AgentPersona, AgentsResponse } from '../types'
import { navigate } from '../router'
import { BTN, DEEP, EmptyState, ErrorBanner, INPUT, PANEL, PageHeader } from './ui'

/* Agents — the persona roster. Each agent IS a markdown file in the hub's
   agents/ dir (frontmatter: roles / projects glob / model; body = personality,
   prepended to the role prompt at run time). This page lists the roster and
   edits the files in place; edits ship to every worker on the next heartbeat. */

const NEW_TEMPLATE = [
  '---',
  'name: ',
  'description: ',
  'roles: author, reviewer',
  'projects: ',
  'model: ',
  '---',
  'You are …',
  '',
].join('\n')

function ago(at: string | null): string {
  if (!at) return 'never ran'
  const sec = Math.max(0, (Date.now() - new Date(at.replace(' ', 'T') + 'Z').getTime()) / 1000)
  if (sec < 3600) return `${Math.max(1, Math.round(sec / 60))}m ago`
  if (sec < 86400) return `${Math.round(sec / 3600)}h ago`
  return `${Math.round(sec / 86400)}d ago`
}

function TierChip({ model }: { model: string }) {
  return (
    <span className="mono rounded-[5px] bg-white/[0.06] px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-[0.08em] text-tx2">
      {model || 'role default'}
    </span>
  )
}

function AgentCard({ a }: { a: AgentPersona }) {
  const idle = !a.last_at
  return (
    <a
      href={`/agents/${encodeURIComponent(a.name)}`}
      className={`card-enter ${PANEL} flex flex-col gap-2 px-3.5 py-3 transition-colors hover:border-white/[0.16] hover:bg-[#1a1e25]`}
    >
      <div className="flex items-center gap-2">
        <span className="mono text-[13px] font-semibold text-proj">{a.name}</span>
        <TierChip model={a.model} />
        <span className={`mono ml-auto text-[10px] ${idle ? 'text-warn' : 'text-tx3'}`}>
          {idle ? 'idle' : ago(a.last_at)}
        </span>
      </div>
      {a.description ? <p className="text-[12px] leading-snug text-tx2">{a.description}</p> : null}
      <div className="flex flex-wrap items-center gap-1.5">
        {(a.roles.length ? a.roles : ['any role']).map((r) => (
          <span
            key={r}
            className="mono rounded-[5px] bg-info/10 px-1.5 py-0.5 text-[10px] font-semibold text-info"
          >
            {r}
          </span>
        ))}
        <span className="mono ml-auto text-[10px] text-tx3">
          projects: {a.projects.length ? a.projects.join(', ') : '* (generalist)'}
        </span>
      </div>
      <span className="mono text-[10px] text-tx3">{a.file}</span>
    </a>
  )
}

function Editor({ data, name }: { data: AgentsResponse; name: string }) {
  const qc = useQueryClient()
  const isNew = name === 'new'
  const agent = isNew ? null : data.agents.find((a) => a.name === name)
  const [file, setFile] = useState(agent?.file ?? '')
  const [text, setText] = useState(agent?.content ?? NEW_TEMPLATE)
  const [dirty, setDirty] = useState(false)
  // A refetch (SSE) must not clobber in-flight edits; sync only while pristine.
  useEffect(() => {
    if (!dirty && agent) {
      setFile(agent.file)
      setText(agent.content)
    }
  }, [agent, dirty])

  const save = useMutation({
    mutationFn: saveAgent,
    onSuccess: (_res, vars) => {
      setDirty(false)
      qc.invalidateQueries({ queryKey: queryKeys.agents() })
      qc.invalidateQueries({ queryKey: queryKeys.projects() })
      if (isNew) navigate(`/agents/${encodeURIComponent(vars.file.replace(/\.md$/, ''))}`)
    },
  })

  if (!isNew && !agent)
    return <EmptyState glyph="✦" title={`No agent “${name}”`} hint="It may have been renamed." />

  const fileOk = /^[A-Za-z0-9][A-Za-z0-9._-]*\.md$/.test(file) && file !== 'README.md'
  return (
    <>
      <p className="mono mb-1 text-[11px] text-tx3">
        <a href="/agents" className="hover:text-tx2">
          agents
        </a>{' '}
        / {isNew ? 'new' : agent!.name}
      </p>
      <PageHeader
        title={<span className="mono text-proj">{isNew ? 'New agent' : agent!.name}</span>}
        subtitle={
          isNew ? (
            'One markdown file: frontmatter says where it applies; the body is the personality.'
          ) : (
            <>
              <TierChip model={agent!.model} />{' '}
              <span className="mono text-[11px]">{ago(agent!.last_at)}</span>
            </>
          )
        }
      />
      <div className={`${DEEP} overflow-hidden`}>
        <div className="flex flex-wrap items-center gap-3 border-b border-hairline px-3.5 py-2">
          {isNew ? (
            <input
              value={file}
              onChange={(e) => setFile(e.target.value)}
              placeholder="fintech-ux.md"
              className={`${INPUT} mono w-[220px] !py-1 text-[12px]`}
            />
          ) : (
            <span className="mono text-[11px] text-tx3">
              {data.dir}/{agent!.file}
            </span>
          )}
          <span className="mono text-[10px] text-tx3">hub-owned · ships to all workers</span>
          <span className="ml-auto flex items-center gap-2">
            <button
              className={BTN.subtle}
              disabled={!dirty || save.isPending}
              onClick={() => {
                setText(agent?.content ?? NEW_TEMPLATE)
                setDirty(false)
              }}
            >
              Revert
            </button>
            <button
              className={BTN.primary}
              disabled={!dirty || !fileOk || !text.trim() || save.isPending}
              onClick={() => save.mutate({ file, content: text })}
            >
              {save.isPending ? 'Saving…' : 'Save'}
            </button>
          </span>
        </div>
        <textarea
          value={text}
          onChange={(e) => {
            setText(e.target.value)
            setDirty(true)
          }}
          spellCheck={false}
          className="mono block h-[440px] w-full resize-y bg-transparent px-3.5 py-3 text-[12px] leading-[1.8] text-tx outline-none placeholder:text-tx3"
          placeholder={NEW_TEMPLATE}
        />
      </div>
      {save.isError ? (
        <p className="mt-2 text-[12px] text-bad">{(save.error as Error).message}</p>
      ) : null}
      <p className="mt-2.5 text-[11px] leading-relaxed text-tx3">
        At run time the body is prepended to the role prompt for tickets this persona covers
        (frontmatter <span className="mono">roles</span> +{' '}
        <span className="mono">projects</span> globs, or an explicit assignment on the{' '}
        <a href="/projects" className="text-proj hover:underline">
          Projects
        </a>{' '}
        page). <span className="mono">model</span> may be a tier ({data.tiers.join(' / ')}) or a
        full model id.
      </p>
    </>
  )
}

export default function AgentsPage({ name }: { name?: string }) {
  const { data, isError } = useQuery({ queryKey: queryKeys.agents(), queryFn: fetchAgents })
  if (isError) return <ErrorBanner />
  if (!data) return null
  if (name) return <Editor data={data} name={name} />
  return (
    <>
      <PageHeader
        title="Agents"
        subtitle="The persona roster — each agent is a markdown file: expertise, voice, specialties."
      />
      {data.agents.length ? (
        <div className="grid gap-3.5 sm:grid-cols-2">
          {data.agents.map((a) => (
            <AgentCard key={a.file} a={a} />
          ))}
        </div>
      ) : (
        <EmptyState
          glyph="✦"
          title="No personas yet"
          hint="Zero-config default: every role runs its stock prompt until you hire someone."
        />
      )}
      <a
        href="/agents/new"
        className="mt-3.5 block rounded-[10px] border border-dashed border-white/15 px-4 py-3 text-center text-[13px] text-tx3 transition-colors hover:border-white/30 hover:text-tx2"
      >
        + New agent
      </a>
    </>
  )
}
