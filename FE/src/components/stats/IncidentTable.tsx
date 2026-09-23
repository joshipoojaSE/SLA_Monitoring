import type { IncidentSort, OutageIncident } from '../../api/types'
import { useIncidentPage } from '../../hooks/queries'
import { useTableQuery } from '../../hooks/useTableQuery'
import { formatDateTime, formatMinutes } from '../../lib/format'
import { Pagination } from '../Pagination'
import { cellX, Skeleton, SortHeader, StateMessage } from '../ui'

const cell = `py-2 ${cellX}`

interface Props {
  fileId: number
  /** From the stats summary, so an empty file needs no request. */
  count: number
  onSelect: (incident: OutageIncident) => void
}

export function IncidentTable({ fileId, count, onSelect }: Props) {
  if (count === 0) {
    return (
      <div>
        <h3 className="mb-2 text-sm font-semibold">Incidents (0)</h3>
        <p className="text-sm text-slate-500">No incidents: every service passed every valid check.</p>
      </div>
    )
  }
  return <IncidentPages fileId={fileId} count={count} onSelect={onSelect} />
}

function IncidentPages({ fileId, count, onSelect }: Props) {
  // Longest first by default.
  const { query, sort, onSort, onPage, onPageSize } = useTableQuery<IncidentSort>('downtime', 'desc')
  const { data, error } = useIncidentPage(fileId, query)
  const header = { sort, onSort }

  return (
    <div className="space-y-3">
      <h3 className="text-sm font-semibold">Incidents ({count})</h3>
      {error ? (
        <StateMessage tone="error">{error.message}</StateMessage>
      ) : !data ? (
        <Skeleton className="h-40" />
      ) : (
        <>
          <div className="overflow-x-auto">
            {/* Fixed widths, spare room at the end: see LogsTable. The action sits right after the numbers it acts on. */}
            <table className="w-full min-w-[60rem] table-fixed text-sm">
              <colgroup>
                <col className="w-40" />
                <col className="w-56" />
                <col className="w-56" />
                <col className="w-36" />
                <col className="w-32" />
                <col />
              </colgroup>
              <thead>
                <tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500">
                  <SortHeader {...header} sortKey="service" label="Service" />
                  <SortHeader {...header} sortKey="started" label="First failed check (UTC)" firstDir="desc" />
                  <SortHeader {...header} sortKey="ended" label="Last failed check (UTC)" firstDir="desc" />
                  <SortHeader {...header} sortKey="checks" label="Failed checks" align="right" firstDir="desc" />
                  <SortHeader {...header} sortKey="downtime" label="Downtime" align="right" firstDir="desc" />
                  <th className={`py-2 ${cellX}`}>
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {data.items.map((incident) => (
                  <tr key={`${incident.service_id}-${incident.started_at}`} className="hover:bg-slate-50">
                    <td className={`${cell} truncate font-medium`} title={incident.service_id}>
                      {incident.service_id}
                    </td>
                    <td className={`${cell} whitespace-nowrap tabular-nums`}>{formatDateTime(incident.started_at)}</td>
                    <td className={`${cell} whitespace-nowrap tabular-nums`}>{formatDateTime(incident.ended_at)}</td>
                    <td className={`${cell} text-right tabular-nums`}>{incident.down_checks}</td>
                    <td className={`${cell} text-right tabular-nums`}>{formatMinutes(incident.downtime_minutes)}</td>
                    <td className={cell}>
                      <button
                        type="button"
                        onClick={() => onSelect(incident)}
                        className="text-xs font-medium text-sky-700 hover:underline"
                      >
                        View logs
                      </button>
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
            noun="incidents"
            onPage={onPage}
            onPageSize={onPageSize}
          />
        </>
      )}
    </div>
  )
}
