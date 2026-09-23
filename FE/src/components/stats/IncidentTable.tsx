import { useState } from 'react'
import type { OutageIncident } from '../../api/types'
import { formatDateTime, formatMinutes } from '../../lib/format'
import { selectClass } from '../ui'

type Order = 'longest' | 'latest'

const PREVIEW = 10

interface Props {
  incidents: OutageIncident[]
  onSelect: (incident: OutageIncident) => void
}

export function IncidentTable({ incidents, onSelect }: Props) {
  const [order, setOrder] = useState<Order>('longest')
  const [showAll, setShowAll] = useState(false)

  if (incidents.length === 0) {
    return <p className="text-sm text-slate-500">No incidents: every service passed every valid check.</p>
  }

  const sorted = [...incidents].sort((a, b) =>
    order === 'longest'
      ? b.downtime_minutes - a.downtime_minutes || a.started_at.localeCompare(b.started_at)
      : b.started_at.localeCompare(a.started_at),
  )
  const visible = showAll ? sorted : sorted.slice(0, PREVIEW)

  return (
    <div>
      <div className="mb-2 flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold">Incidents ({incidents.length})</h3>
        <label className="flex items-center gap-2 text-xs text-slate-500">
          Sort
          <select className={selectClass} value={order} onChange={(event) => setOrder(event.target.value as Order)}>
            <option value="longest">Longest first</option>
            <option value="latest">Latest first</option>
          </select>
        </label>
      </div>
      <div className="overflow-x-auto">
        <table className="min-w-full text-sm">
          <thead>
            <tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500">
              <th className="py-2 pr-4 font-medium">Service</th>
              <th className="py-2 pr-4 font-medium">First failed check (UTC)</th>
              <th className="py-2 pr-4 font-medium">Last failed check (UTC)</th>
              <th className="py-2 pr-4 text-right font-medium">Failed checks</th>
              <th className="py-2 pr-4 text-right font-medium">Downtime</th>
              <th className="py-2" />
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {visible.map((incident) => (
              <tr key={`${incident.service_id}-${incident.started_at}`} className="hover:bg-slate-50">
                <td className="py-2 pr-4 font-medium">{incident.service_id}</td>
                <td className="py-2 pr-4 tabular-nums">{formatDateTime(incident.started_at)}</td>
                <td className="py-2 pr-4 tabular-nums">{formatDateTime(incident.ended_at)}</td>
                <td className="py-2 pr-4 text-right tabular-nums">{incident.down_checks}</td>
                <td className="py-2 pr-4 text-right tabular-nums">{formatMinutes(incident.downtime_minutes)}</td>
                <td className="py-2 text-right">
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
      {incidents.length > PREVIEW && (
        <button
          type="button"
          onClick={() => setShowAll((value) => !value)}
          className="mt-2 text-xs font-medium text-sky-700 hover:underline"
        >
          {showAll ? 'Show fewer' : `Show all ${incidents.length}`}
        </button>
      )}
    </div>
  )
}
