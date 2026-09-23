import type { Outcome, ServiceStats } from '../../api/types'
import type { DashboardParams, DateMode } from '../../hooks/useDashboardParams'
import { selectClass } from '../ui'

interface Props {
  params: DashboardParams
  services: ServiceStats[]
  /** First and last UTC day the file covers, YYYY-MM-DD. */
  minDate?: string
  maxDate?: string
  onChange: (change: Partial<DashboardParams>) => void
}

export function LogFilters({ params, services, minDate, maxDate, onChange }: Props) {
  const hasFilters = params.from || params.to || params.service || params.outcome

  const setMode = (mode: DateMode) => {
    if (mode === params.mode) return
    // Keep the start day when switching; a single day has no end.
    onChange({ mode, to: mode === 'range' ? params.from : undefined })
  }

  return (
    <div className="flex flex-wrap items-end gap-3">
      <div role="group" aria-label="Date filter type" className="inline-flex rounded-md shadow-sm">
        {(['day', 'range'] as DateMode[]).map((mode, index) => (
          <button
            key={mode}
            type="button"
            onClick={() => setMode(mode)}
            aria-pressed={params.mode === mode}
            className={`border border-slate-300 px-3 py-1.5 text-sm ${index === 0 ? 'rounded-l-md' : '-ml-px rounded-r-md'} ${
              params.mode === mode ? 'bg-slate-800 text-white' : 'bg-white text-slate-700 hover:bg-slate-50'
            }`}
          >
            {mode === 'day' ? 'Single date' : 'Date range'}
          </button>
        ))}
      </div>

      <label className="flex flex-col gap-1 text-xs text-slate-500">
        {params.mode === 'day' ? 'Date (UTC)' : 'From (UTC)'}
        <input
          type="date"
          className={selectClass}
          value={params.from ?? ''}
          min={minDate}
          max={params.mode === 'range' && params.to ? params.to : maxDate}
          onChange={(event) => onChange({ from: event.target.value || undefined })}
        />
      </label>

      {params.mode === 'range' && (
        <label className="flex flex-col gap-1 text-xs text-slate-500">
          To (UTC)
          <input
            type="date"
            className={selectClass}
            value={params.to ?? ''}
            min={params.from ?? minDate}
            max={maxDate}
            onChange={(event) => onChange({ to: event.target.value || undefined })}
          />
        </label>
      )}

      <label className="flex flex-col gap-1 text-xs text-slate-500">
        Service
        <select
          className={selectClass}
          value={params.service ?? ''}
          onChange={(event) => onChange({ service: event.target.value || undefined })}
        >
          <option value="">All services</option>
          {services.map((service) => (
            <option key={service.service_id} value={service.service_id}>
              {service.service_name}
            </option>
          ))}
        </select>
      </label>

      <label className="flex flex-col gap-1 text-xs text-slate-500">
        Outcome
        <select
          className={selectClass}
          value={params.outcome ?? ''}
          onChange={(event) => onChange({ outcome: (event.target.value || undefined) as Outcome | undefined })}
        >
          <option value="">All outcomes</option>
          <option value="up">Up</option>
          <option value="down">Down</option>
          <option value="invalid">Invalid</option>
        </select>
      </label>

      {hasFilters && (
        <button
          type="button"
          onClick={() => onChange({ from: undefined, to: undefined, service: undefined, outcome: undefined })}
          className="py-1.5 text-sm font-medium text-sky-700 hover:underline"
        >
          Clear filters
        </button>
      )}
    </div>
  )
}
