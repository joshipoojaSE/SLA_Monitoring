import type { ServiceStats } from '../../api/types'
import { formatMinutes, formatMs, formatNumber, formatPct } from '../../lib/format'
import { Badge } from '../ui'

interface Props {
  services: ServiceStats[]
  onSelect: (serviceId: string) => void
}

function SlaBadge({ service }: { service: ServiceStats }) {
  if (service.meets_sla === null) return <Badge tone="neutral">No data</Badge>
  return service.meets_sla ? <Badge tone="good">Met</Badge> : <Badge tone="bad">Breached</Badge>
}

export function ServiceTable({ services, onSelect }: Props) {
  // Worst first: that is the row billing and on-call both look for.
  const sorted = [...services].sort((a, b) => (a.availability_pct ?? 101) - (b.availability_pct ?? 101))

  return (
    <div className="overflow-x-auto">
      <table className="min-w-full text-sm">
        <thead>
          <tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500">
            <th className="py-2 pr-4 font-medium">Service</th>
            <th className="py-2 pr-4 text-right font-medium">Availability</th>
            <th className="py-2 pr-4 font-medium">SLA</th>
            <th className="py-2 pr-4 text-right font-medium" title="Downtime / downtime the SLA allows over the period">
              Downtime / allowed
            </th>
            <th className="py-2 pr-4 text-right font-medium">Incidents</th>
            <th className="py-2 pr-4 text-right font-medium">Longest</th>
            <th className="py-2 pr-4 text-right font-medium">p95 latency</th>
            <th className="py-2 text-right font-medium" title="Checks with an unusable status code / slots with no check">
              Invalid / missing
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {sorted.map((service) => (
            <tr
              key={service.service_id}
              onClick={() => onSelect(service.service_id)}
              className="cursor-pointer hover:bg-slate-50"
              title="Show this service's logs"
            >
              <td className="py-2 pr-4">
                <div className="font-medium">{service.service_name}</div>
                <div className="text-xs text-slate-500">{service.service_id}</div>
              </td>
              <td
                className={`py-2 pr-4 text-right font-semibold tabular-nums ${service.meets_sla === false ? 'text-red-700' : ''}`}
              >
                {formatPct(service.availability_pct)}
              </td>
              <td className="py-2 pr-4">
                <SlaBadge service={service} />
              </td>
              <td className="py-2 pr-4 text-right tabular-nums">
                {formatMinutes(service.downtime_minutes)}
                <span className="text-slate-400"> / {formatMinutes(Math.floor(service.allowed_downtime_minutes))}</span>
              </td>
              <td className="py-2 pr-4 text-right tabular-nums">{formatNumber(service.incidents)}</td>
              <td className="py-2 pr-4 text-right tabular-nums">{formatMinutes(service.longest_incident_minutes)}</td>
              <td className="py-2 pr-4 text-right tabular-nums">{formatMs(service.p95_latency_ms)}</td>
              <td className="py-2 text-right tabular-nums">
                {formatNumber(service.invalid_checks)} / {formatNumber(service.missing_checks)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
