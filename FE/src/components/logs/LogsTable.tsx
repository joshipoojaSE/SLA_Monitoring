import type { LogRow, Outcome } from '../../api/types'
import { formatDateTime, formatMs } from '../../lib/format'
import { Badge } from '../ui'

const OUTCOME_TONE = { up: 'good', down: 'bad', invalid: 'warn' } as const satisfies Record<Outcome, string>

export function LogsTable({ rows }: { rows: LogRow[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="min-w-full text-sm">
        <thead>
          <tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500">
            <th className="py-2 pr-4 font-medium">Checked at (UTC)</th>
            <th className="py-2 pr-4 font-medium">Service</th>
            <th className="py-2 pr-4 text-right font-medium">Status</th>
            <th className="py-2 pr-4 font-medium">Outcome</th>
            <th className="py-2 pr-4 text-right font-medium">Latency</th>
            <th className="py-2 pr-4 font-medium">Agent</th>
            <th className="py-2 font-medium">Region</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {rows.map((row) => (
            <tr key={`${row.service_id}-${row.checked_at}`} className={row.outcome === 'down' ? 'bg-red-50/40' : ''}>
              <td className="py-1.5 pr-4 whitespace-nowrap tabular-nums">{formatDateTime(row.checked_at)}</td>
              <td className="py-1.5 pr-4">{row.service_id}</td>
              <td className="py-1.5 pr-4 text-right tabular-nums">{row.status_code}</td>
              <td className="py-1.5 pr-4">
                <Badge tone={OUTCOME_TONE[row.outcome]}>{row.outcome}</Badge>
              </td>
              <td className="py-1.5 pr-4 text-right tabular-nums">{formatMs(row.latency_ms)}</td>
              <td className="py-1.5 pr-4">{row.agent_id}</td>
              <td className="py-1.5">{row.region_id}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
