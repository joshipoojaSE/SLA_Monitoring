import type { DashboardStats } from '../../api/types'
import { formatMinutes, formatNumber, formatPct } from '../../lib/format'

interface Tile {
  label: string
  value: string
  detail: string
  alert?: boolean
}

export function KpiTiles({ stats }: { stats: DashboardStats }) {
  const breaching = stats.services_breaching

  const tiles: Tile[] = [
    {
      label: 'Overall availability',
      value: formatPct(stats.availability_pct),
      detail: `Target ${stats.sla_target_pct}% per service`,
      alert: stats.availability_pct !== null && stats.availability_pct < stats.sla_target_pct,
    },
    {
      label: 'Services below SLA',
      value: `${breaching} of ${stats.services.length}`,
      detail: breaching ? 'Eligible for a billing credit' : 'All within target',
      alert: breaching > 0,
    },
    {
      label: 'Total downtime',
      value: formatMinutes(stats.total_downtime_minutes),
      detail: 'Failed checks × 15 min, all services',
    },
    {
      label: 'Incidents',
      value: formatNumber(stats.incident_count),
      detail: `Longest ${formatMinutes(stats.longest_incident_minutes)}`,
    },
  ]

  return (
    <dl className="grid grid-cols-2 gap-3 xl:grid-cols-4">
      {tiles.map((tile) => (
        <div
          key={tile.label}
          className={`rounded-md border p-3 ${tile.alert ? 'border-red-200 bg-red-50/60' : 'border-slate-200 bg-slate-50'}`}
        >
          <dt className="text-xs font-medium uppercase tracking-wide text-slate-500">{tile.label}</dt>
          <dd className={`mt-1 text-2xl font-semibold tabular-nums ${tile.alert ? 'text-red-700' : 'text-slate-900'}`}>
            {tile.value}
          </dd>
          <dd className="mt-0.5 text-xs text-slate-500">{tile.detail}</dd>
        </div>
      ))}
    </dl>
  )
}
