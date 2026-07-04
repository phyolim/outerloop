// Shared formatting. SQLite timestamps are "YYYY-MM-DD HH:MM:SS" in UTC.
export function parseTs(ts: string): Date {
  return new Date(ts.replace(' ', 'T') + 'Z')
}

export function fmt(ts: string): string {
  const d = parseTs(ts)
  return isNaN(d.getTime()) ? ts : d.toLocaleString()
}

export function ago(ts: string | null | undefined): string {
  if (!ts) return 'never'
  const d = parseTs(ts)
  if (isNaN(d.getTime())) return ts
  const s = Math.max(0, Math.round((Date.now() - d.getTime()) / 1000))
  if (s < 60) return `${s}s ago`
  if (s < 3600) return `${Math.round(s / 60)}m ago`
  if (s < 86400) return `${Math.round(s / 3600)}h ago`
  return `${Math.round(s / 86400)}d ago`
}

export function agoSec(sec: number | null | undefined): string {
  if (sec == null) return 'never'
  if (sec < 60) return `${sec}s ago`
  if (sec < 3600) return `${Math.round(sec / 60)}m ago`
  return `${Math.round(sec / 3600)}h ago`
}
