import { useEffect, useId, useState } from 'react'
import type { DashboardStats, OutageIncident } from '../../api/types'
import { formatPct } from '../../lib/format'
import { Card, Skeleton, StateMessage } from '../ui'
import { IncidentTable } from './IncidentTable'
import { KpiTiles } from './KpiTiles'
import { ServiceTable } from './ServiceTable'

const STORAGE_KEY = 'stats-expanded'

// A viewer's own preference only, so browser storage is enough; it may be
// unavailable (private mode), in which case the panel just starts open.
function readExpanded(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) !== 'false'
  } catch {
    return true
  }
}

interface Props {
  stats?: DashboardStats
  isLoading: boolean
  error: Error | null
  onSelectService: (serviceId: string) => void
  onSelectIncident: (incident: OutageIncident) => void
}

export function StatsPanel({ stats, isLoading, error, onSelectService, onSelectIncident }: Props) {
  const [expanded, setExpanded] = useState(readExpanded)
  const bodyId = useId()

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, String(expanded))
    } catch {
      // Not remembered; nothing else depends on it.
    }
  }, [expanded])

  return (
    <Card>
      <button
        type="button"
        onClick={() => setExpanded((value) => !value)}
        aria-expanded={expanded}
        aria-controls={bodyId}
        className="flex w-full items-center justify-between gap-4 px-4 py-3 text-left"
      >
        <span className="flex items-center gap-2">
          <svg
            viewBox="0 0 20 20"
            fill="currentColor"
            aria-hidden
            className={`h-4 w-4 text-slate-500 transition-transform ${expanded ? 'rotate-90' : ''}`}
          >
            <path d="M7.2 4.2a1 1 0 0 1 1.4 0l5.1 5.1a1 1 0 0 1 0 1.4l-5.1 5.1a1 1 0 1 1-1.4-1.4L11.6 10 7.2 5.6a1 1 0 0 1 0-1.4Z" />
          </svg>
          <span className="font-semibold">SLA stats</span>
        </span>
        {/* When collapsed, the headline numbers stay visible. */}
        {!expanded && stats && (
          <span className="truncate text-sm text-slate-600">
            {formatPct(stats.availability_pct)} overall · {stats.services_breaching} of {stats.services.length} below{' '}
            {stats.sla_target_pct}% · {stats.incident_count} incidents
          </span>
        )}
      </button>

      {expanded && (
        <div id={bodyId} className="space-y-6 border-t border-slate-100 px-4 py-4">
          {error ? (
            <StateMessage tone="error">{error.message}</StateMessage>
          ) : isLoading || !stats ? (
            <div className="space-y-3">
              <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
                {Array.from({ length: 4 }, (_, index) => (
                  <Skeleton key={index} className="h-20" />
                ))}
              </div>
              <Skeleton className="h-40" />
            </div>
          ) : (
            <>
              <KpiTiles stats={stats} />
              {!stats.scanned && (
                <p className="text-xs text-amber-700">
                  This file has not been scanned for outages yet; incidents below are detected on the fly and not
                  stored.
                </p>
              )}
              <div>
                <h3 className="mb-2 text-sm font-semibold">By service</h3>
                {/* Keyed by file, so switching files starts each table again from page 1. */}
                <ServiceTable key={stats.file_id} fileId={stats.file_id} onSelect={onSelectService} />
              </div>
              <IncidentTable
                key={stats.file_id}
                fileId={stats.file_id}
                count={stats.incident_count}
                onSelect={onSelectIncident}
              />
            </>
          )}
        </div>
      )}
    </Card>
  )
}
