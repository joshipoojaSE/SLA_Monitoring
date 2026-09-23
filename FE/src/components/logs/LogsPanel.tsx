import type { DashboardStats, LogPage } from '../../api/types'
import type { DashboardParams } from '../../hooks/useDashboardParams'
import { utcDay } from '../../lib/format'
import { Card, Skeleton, Spinner, StateMessage } from '../ui'
import { LogFilters } from './LogFilters'
import { LogsTable } from './LogsTable'
import { Pagination } from './Pagination'

interface Props {
  params: DashboardParams
  stats?: DashboardStats
  logs?: LogPage
  isLoading: boolean
  isFetching: boolean
  error: Error | null
  onChange: (change: Partial<DashboardParams>) => void
}

export function LogsPanel({ params, stats, logs, isLoading, isFetching, error, onChange }: Props) {
  return (
    <Card>
      <div className="flex items-center justify-between gap-2 border-b border-slate-100 px-4 py-3">
        <h2 className="font-semibold">Check logs</h2>
        {isFetching && !isLoading && <Spinner />}
      </div>
      <div className="space-y-4 px-4 py-4">
        <LogFilters
          params={params}
          services={stats?.services ?? []}
          minDate={stats && utcDay(stats.coverage_start)}
          maxDate={stats && utcDay(stats.coverage_end)}
          onChange={onChange}
        />

        {error ? (
          <StateMessage tone="error">{error.message}</StateMessage>
        ) : isLoading || !logs ? (
          <div className="space-y-2">
            {Array.from({ length: 8 }, (_, index) => (
              <Skeleton key={index} className="h-7" />
            ))}
          </div>
        ) : logs.total === 0 ? (
          <StateMessage>No checks match these filters.</StateMessage>
        ) : (
          <>
            <LogsTable rows={logs.items} />
            <Pagination
              page={logs.page}
              pageSize={logs.page_size}
              total={logs.total}
              onPage={(page) => onChange({ page })}
            />
          </>
        )}
      </div>
    </Card>
  )
}
