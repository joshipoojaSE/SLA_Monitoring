import type { ServiceSort, ServiceStats } from '../../api/types'
import { useServicePage } from '../../hooks/queries'
import { useTableQuery } from '../../hooks/useTableQuery'
import { formatMinutes, formatMs, formatNumber, formatPct } from '../../lib/format'
import { Pagination } from '../Pagination'
import { Badge, cellX, Skeleton, SortHeader, StateMessage } from '../ui'

interface Props {
  fileId: number
  onSelect: (serviceId: string) => void
}

const cell = `py-2 ${cellX}`

function SlaBadge({ service }: { service: ServiceStats }) {
  if (service.meets_sla === null) return <Badge tone="neutral">No data</Badge>
  return service.meets_sla ? <Badge tone="good">Met</Badge> : <Badge tone="bad">Breached</Badge>
}

export function ServiceTable({ fileId, onSelect }: Props) {
  // Worst first by default: that is the row billing and on-call both look for.
  const { query, sort, onSort, onPage, onPageSize } = useTableQuery<ServiceSort>('availability', 'asc')
  const { data, error } = useServicePage(fileId, query)
  const header = { sort, onSort }

  if (error) return <StateMessage tone="error">{error.message}</StateMessage>
  if (!data) return <Skeleton className="h-40" />

  return (
    <div className="space-y-3">
      <div className="overflow-x-auto">
        <table className="min-w-full text-sm">
          <thead>
            <tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500">
              <SortHeader {...header} sortKey="service" label="Service" />
              <SortHeader {...header} sortKey="availability" label="Availability" align="right" />
              <SortHeader {...header} sortKey="sla" label="SLA" />
              <SortHeader
                {...header}
                sortKey="downtime"
                label="Downtime / allowed"
                align="right"
                firstDir="desc"
                title="Downtime / downtime the SLA allows over the period"
              />
              <SortHeader {...header} sortKey="incidents" label="Incidents" align="right" firstDir="desc" />
              <SortHeader {...header} sortKey="longest" label="Longest" align="right" firstDir="desc" />
              <SortHeader {...header} sortKey="p95" label="p95 latency" align="right" firstDir="desc" />
              <SortHeader
                {...header}
                sortKey="gaps"
                label="Invalid / missing"
                align="right"
                firstDir="desc"
                title="Checks with an unusable status code / slots with no check. Sorts by the two added together."
              />
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {data.items.map((service) => (
              <tr
                key={service.service_id}
                onClick={() => onSelect(service.service_id)}
                className="cursor-pointer hover:bg-slate-50"
                title="Show this service's logs"
              >
                <td className={cell}>
                  <div className="font-medium">{service.service_name}</div>
                  <div className="text-xs text-slate-500">{service.service_id}</div>
                </td>
                <td
                  className={`${cell} text-right font-semibold tabular-nums ${service.meets_sla === false ? 'text-red-700' : ''}`}
                >
                  {formatPct(service.availability_pct)}
                </td>
                <td className={cell}>
                  <SlaBadge service={service} />
                </td>
                <td className={`${cell} text-right tabular-nums`}>
                  {formatMinutes(service.downtime_minutes)}
                  <span className="text-slate-400"> / {formatMinutes(Math.floor(service.allowed_downtime_minutes))}</span>
                </td>
                <td className={`${cell} text-right tabular-nums`}>{formatNumber(service.incidents)}</td>
                <td className={`${cell} text-right tabular-nums`}>{formatMinutes(service.longest_incident_minutes)}</td>
                <td className={`${cell} text-right tabular-nums`}>{formatMs(service.p95_latency_ms)}</td>
                <td className={`${cell} text-right tabular-nums`}>
                  {formatNumber(service.invalid_checks)} / {formatNumber(service.missing_checks)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Pagination
        page={data.page}
        pageSize={data.page_size}
        total={data.total}
        noun="services"
        onPage={onPage}
        onPageSize={onPageSize}
      />
    </div>
  )
}
