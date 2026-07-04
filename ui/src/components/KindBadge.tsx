export default function KindBadge({ label, color }: { label: string; color: string }) {
  return (
    <span
      className="inline-block rounded px-1.5 py-0.5 text-[11px] font-medium text-white"
      style={{ backgroundColor: color }}
    >
      {label}
    </span>
  )
}
