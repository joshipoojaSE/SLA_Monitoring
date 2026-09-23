import { useState } from 'react'
import type { SortOrder, TableQuery } from '../api/types'
import { DEFAULT_PAGE_SIZE } from '../components/Pagination'
import { nextSort, type SortDir } from '../components/ui'

/**
 * Sort and page for a table the server pages. Kept in the component, not the
 * URL: only the log filters are worth sharing as a link. A new sort or page
 * size starts again from page 1.
 */
export function useTableQuery<K extends string>(sort: K, order: SortOrder) {
  const [query, setQuery] = useState<TableQuery<K>>({ sort, order, page: 1, pageSize: DEFAULT_PAGE_SIZE })

  const onSort = (key: K, firstDir: SortDir) =>
    setQuery((current) => {
      const next = nextSort({ key: current.sort, dir: current.order }, key, firstDir)
      return { ...current, sort: next.key, order: next.dir, page: 1 }
    })
  const onPage = (page: number) => setQuery((current) => ({ ...current, page }))
  const onPageSize = (pageSize: number) => setQuery((current) => ({ ...current, pageSize, page: 1 }))

  return { query, sort: { key: query.sort, dir: query.order }, onSort, onPage, onPageSize }
}
