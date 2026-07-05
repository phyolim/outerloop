/* The coding delivery lifecycle as a 7-segment meter, shared by the board cards,
   the board list rows, and the Inbox in-progress rows. sub_stage names the state
   reached; the value here is how many segments that completes — the next segment
   is the one in progress (amber) only while the loop is actually on it. */
const STAGE_DONE: Record<string, number> = {
  seed: 1,
  creating_repo: 1,
  groomed: 2,
  implemented: 3,
  reviewing: 3,
  fixing: 3,
  opening_pr: 4,
  merge_gate: 5,
  merging: 6,
  merged: 7,
}
const TITLE = 'seed → groomed → implemented → reviewed → PR → merge gate → merged'

type StageLike = { status: string; sub_stage: string | null; draft?: boolean }

export function stageDone(t: StageLike): number {
  if (t.status === 'done') return 7
  if (t.status === 'inbox' && t.draft) return 0
  return STAGE_DONE[t.sub_stage ?? 'seed'] ?? 1
}

export function LifecycleMeter({ t, className = '' }: { t: StageLike; className?: string }) {
  const done = stageDone(t)
  const current = t.status === 'active' || t.status === 'blocked' ? done : -1
  return (
    <div className={`flex items-center gap-[3px] ${className}`} title={TITLE}>
      {Array.from({ length: 7 }, (_, i) => (
        <i
          key={i}
          className="h-[3px] flex-1 rounded-full"
          style={{
            background:
              i < done
                ? 'rgba(61,220,132,0.75)'
                : i === current
                  ? '#f5b843'
                  : 'rgba(255,255,255,0.10)',
          }}
        />
      ))}
    </div>
  )
}
