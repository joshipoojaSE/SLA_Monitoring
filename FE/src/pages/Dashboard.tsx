import { useRef } from 'react'
import { Link } from 'react-router-dom'
import type { OutageIncident } from '../api/types'
import { LogsPanel } from '../components/logs/LogsPanel'
import { StatsPanel } from '../components/stats/StatsPanel'
import { Skeleton, StateMessage, selectClass } from '../components/ui'
import { useFiles, useLogs, useStats } from '../hooks/queries'
import { useDashboardParams } from '../hooks/useDashboardParams'
import { formatDate, formatDateTime, utcDay } from '../lib/format'

export function Dashboard() {
  const { params, update, logFilters } = useDashboardParams()
  const files = useFiles()
  const logsRef = useRef<HTMLDivElement>(null)

  // No file in the URL means the newest upload.
  const fileId = params.fileId ?? files.data?.[0]?.file_id
  const stats = useStats(fileId)
  const logs = useLogs(fileId, logFilters)

  const showLogs = (change: Parameters<typeof update>[0]) => {
    update(change)
    logsRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  const selectIncident = (incident: OutageIncident) =>
    showLogs({
      mode: 'day',
      from: utcDay(incident.started_at),
      to: undefined,
      service: incident.service_id,
      outcome: undefined,
    })

  if (files.isLoading) return <Skeleton className="h-64" />
  if (files.error) return <StateMessage tone="error">{files.error.message}</StateMessage>
  if (!files.data?.length) {
    return (
      <StateMessage>
        No data yet.{' '}
        <Link to="/upload" className="font-medium text-sky-700 hover:underline">
          Upload a CSV
        </Link>{' '}
        to get started.
      </StateMessage>
    )
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <label className="flex flex-col gap-1 text-xs text-slate-500">
          Dataset
          <select
            className={`${selectClass} min-w-72`}
            value={fileId}
            // A different file has different dates and services, so its filters start fresh.
            onChange={(event) =>
              update({
                fileId: Number(event.target.value),
                mode: undefined,
                from: undefined,
                to: undefined,
                service: undefined,
                outcome: undefined,
              })
            }
          >
            {files.data.map((file) => (
              <option key={file.file_id} value={file.file_id}>
                {file.file_name} · uploaded {formatDateTime(file.uploaded_at)}
              </option>
            ))}
          </select>
        </label>
        {stats.data && (
          <p className="text-sm text-slate-600">
            Covers <strong>{formatDate(stats.data.coverage_start)}</strong> –{' '}
            <strong>{formatDate(stats.data.coverage_end)}</strong> ({stats.data.days} days, UTC)
          </p>
        )}
      </div>

      <StatsPanel
        stats={stats.data}
        isLoading={stats.isLoading}
        error={stats.error}
        onSelectService={(service) => showLogs({ service })}
        onSelectIncident={selectIncident}
      />

      <div ref={logsRef} className="scroll-mt-4">
        <LogsPanel
          params={params}
          stats={stats.data}
          logs={logs.data}
          isLoading={logs.isLoading}
          isFetching={logs.isFetching}
          error={logs.error}
          onChange={update}
        />
      </div>
    </div>
  )
}
