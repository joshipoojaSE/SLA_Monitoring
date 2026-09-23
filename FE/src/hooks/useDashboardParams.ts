import { useCallback, useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'
import type { LogFilters, LogSort, Outcome, SortOrder } from '../api/types'
import { DEFAULT_PAGE_SIZE, PAGE_SIZES } from '../components/Pagination'

export type DateMode = 'day' | 'range'

export interface DashboardParams {
  fileId?: number
  mode: DateMode
  from?: string
  to?: string
  service?: string
  outcome?: Outcome
  sort: LogSort
  order: SortOrder
  page: number
  pageSize: number
}

const OUTCOMES: Outcome[] = ['up', 'down', 'invalid']
const LOG_SORTS: LogSort[] = ['checked_at', 'service', 'status', 'outcome', 'latency', 'agent', 'region']

const QUERY_NAMES: Record<keyof DashboardParams, string> = {
  fileId: 'file',
  mode: 'mode',
  from: 'from',
  to: 'to',
  service: 'service',
  outcome: 'outcome',
  sort: 'sort',
  order: 'order',
  page: 'page',
  pageSize: 'size',
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
    const pageSize = Number(search.get('size'))
    const sort = search.get('sort') as LogSort | null
    return {
      fileId: Number.isInteger(file) && file > 0 ? file : undefined,
      mode: search.get('mode') === 'range' ? 'range' : 'day',
      from: search.get('from') || undefined,
      to: search.get('to') || undefined,
      service: search.get('service') || undefined,
      outcome: outcome && OUTCOMES.includes(outcome) ? outcome : undefined,
      // Oldest first unless the viewer picked a column.
      sort: sort && LOG_SORTS.includes(sort) ? sort : 'checked_at',
      order: search.get('order') === 'desc' ? 'desc' : 'asc',
      page: Number.isInteger(page) && page > 0 ? page : 1,
      pageSize: (PAGE_SIZES as readonly number[]).includes(pageSize) ? pageSize : DEFAULT_PAGE_SIZE,
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
            // Defaults stay out of the URL, so a plain link stays short.
            const isDefault = (key === 'page' && value === 1) || (key === 'pageSize' && value === DEFAULT_PAGE_SIZE)
            if (value === undefined || value === '' || isDefault) next.delete(name)
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
      sort: params.sort,
      order: params.order,
      page: params.page,
      pageSize: params.pageSize,
    }),
    [params],
  )

  return { params, update, logFilters }
}
