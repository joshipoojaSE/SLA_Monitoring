import type { LogRow, LogSort, Outcome } from '../../api/types'
import { formatDateTime, formatMs } from '../../lib/format'
import { Badge, cellX, SortHeader, type SortDir, type SortState } from '../ui'

const OUTCOME_TONE = { up: 'good', down: 'bad', invalid: 'warn' } as const satisfies Record<Outcome, string>

interface Props {
  rows: LogRow[]
  // The server sorts across every page, so the order lives with the other filters.
  sort: SortState<LogSort>
  onSort: (key: LogSort, firstDir: SortDir) => void
}

const cell = `py-1.5 ${cellX}`

export function LogsTable({ rows, sort, onSort }: Props) {
  const header = { sort, onSort }

  return (
    <div className="overflow-x-auto">
      {/* Fixed widths sized to each column's content keep the spacing even from page to page;
          the last column takes whatever width is left, so spare room sits at the end, not between columns. */}
      <table className="w-full min-w-[56rem] table-fixed text-sm">
        <colgroup>
          <col className="w-44" />
          <col className="w-36" />
          <col className="w-24" />
          <col className="w-28" />
          <col className="w-28" />
          <col className="w-32" />
          <col />
        </colgroup>
        <thead>
          <tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500">
            <SortHeader {...header} sortKey="checked_at" label="Checked at (UTC)" />
            <SortHeader {...header} sortKey="service" label="Service" />
            {/* A status code is a label, not a quantity: it reads with the outcome beside it. */}
            <SortHeader {...header} sortKey="status" label="Status" firstDir="desc" />
            <SortHeader {...header} sortKey="outcome" label="Outcome" />
            <SortHeader {...header} sortKey="latency" label="Latency" align="right" firstDir="desc" />
            <SortHeader {...header} sortKey="agent" label="Agent" />
            <SortHeader {...header} sortKey="region" label="Region" />
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {rows.map((row) => (
            <tr key={`${row.service_id}-${row.checked_at}`} className={row.outcome === 'down' ? 'bg-red-50/40' : ''}>
              <td className={`${cell} whitespace-nowrap tabular-nums`}>{formatDateTime(row.checked_at)}</td>
              <td className={`${cell} truncate`} title={row.service_id}>
                {row.service_id}
              </td>
              <td className={`${cell} tabular-nums`}>{row.status_code}</td>
              <td className={cell}>
                <Badge tone={OUTCOME_TONE[row.outcome]}>{row.outcome}</Badge>
              </td>
              <td className={`${cell} text-right tabular-nums`}>{formatMs(row.latency_ms)}</td>
              <td className={`${cell} truncate`} title={row.agent_id}>
                {row.agent_id}
              </td>
              <td className={`${cell} truncate`} title={row.region_id}>
                {row.region_id}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
