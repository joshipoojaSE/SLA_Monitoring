import { useCallback, useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'
import type { LogFilters, Outcome } from '../api/types'

export type DateMode = 'day' | 'range'

export interface DashboardParams {
  fileId?: number
  mode: DateMode
  from?: string
  to?: string
  service?: string
  outcome?: Outcome
  page: number
}

const OUTCOMES: Outcome[] = ['up', 'down', 'invalid']
export const PAGE_SIZE = 50

const QUERY_NAMES: Record<keyof DashboardParams, string> = {
  fileId: 'file',
  mode: 'mode',
  from: 'from',
  to: 'to',
  service: 'service',
  outcome: 'outcome',
  page: 'page',
}

/**
 * Dashboard state lives in the query string, so a filtered view survives a
 * reload and can be sent to someone as a link.
 */
export function useDashboardParams() {
  const [search, setSearch] = useSearchParams()

  const params = useMemo<DashboardParams>(() => {
    const file = Number(search.get('file'))
    const outcome = search.get('outcome') as Outcome | null
    const page = Number(search.get('page'))
    return {
      fileId: Number.isInteger(file) && file > 0 ? file : undefined,
      mode: search.get('mode') === 'range' ? 'range' : 'day',
      from: search.get('from') || undefined,
      to: search.get('to') || undefined,
      service: search.get('service') || undefined,
      outcome: outcome && OUTCOMES.includes(outcome) ? outcome : undefined,
      page: Number.isInteger(page) && page > 0 ? page : 1,
    }
  }, [search])

  /** Merge a change in. Any filter change goes back to page 1. */
  const update = useCallback(
    (change: Partial<DashboardParams>) => {
      setSearch(
        (current) => {
          const next = new URLSearchParams(current)
          for (const [key, value] of Object.entries(change)) {
            const name = QUERY_NAMES[key as keyof DashboardParams]
            if (value === undefined || value === '' || (key === 'page' && value === 1)) next.delete(name)
            else next.set(name, String(value))
          }
          if (!('page' in change)) next.delete('page')
          return next
        },
        { replace: true },
      )
    },
    [setSearch],
  )

  const logFilters = useMemo<LogFilters>(
    () => ({
      dateFrom: params.from,
      // A single day is a range of one.
      dateTo: params.mode === 'day' ? params.from : params.to,
      serviceId: params.service,
      outcome: params.outcome,
      page: params.page,
      pageSize: PAGE_SIZE,
    }),
    [params],
  )

  return { params, update, logFilters }
}
