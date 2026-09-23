import { useId } from 'react'
import { formatNumber } from '../lib/format'
import { selectClass } from './ui'

export const PAGE_SIZES = [10, 25, 50, 100] as const
export const DEFAULT_PAGE_SIZE = 10

interface Props {
  page: number
  pageSize: number
  total: number
  /** What the rows are, plural: "checks", "services". */
  noun: string
  onPage: (page: number) => void
  onPageSize: (pageSize: number) => void
}

const buttonClass =
  'rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40'

export function Pagination({ page, pageSize, total, noun, onPage, onPageSize }: Props) {
  const sizeId = useId()
  const pages = Math.max(1, Math.ceil(total / pageSize))
  const first = total === 0 ? 0 : (page - 1) * pageSize + 1
  const last = Math.min(page * pageSize, total)

  return (
    <nav aria-label={`${noun} pages`} className="flex flex-wrap items-center justify-between gap-2 text-sm text-slate-600">
      <div className="flex items-center gap-3">
        <span className="tabular-nums">
          {formatNumber(first)}–{formatNumber(last)} of {formatNumber(total)} {noun}
        </span>
        <label htmlFor={sizeId} className="flex items-center gap-2 text-xs text-slate-500">
          Rows per page
          <select
            id={sizeId}
            className={selectClass}
            value={pageSize}
            onChange={(event) => onPageSize(Number(event.target.value))}
          >
            {PAGE_SIZES.map((size) => (
              <option key={size} value={size}>
                {size}
              </option>
            ))}
          </select>
        </label>
      </div>
      {/* One page needs no page controls. */}
      {pages > 1 && (
        <div className="flex items-center gap-2">
          <button type="button" className={buttonClass} disabled={page <= 1} onClick={() => onPage(1)}>
            First
          </button>
          <button type="button" className={buttonClass} disabled={page <= 1} onClick={() => onPage(page - 1)}>
            Previous
          </button>
          <span className="px-1 tabular-nums">
            Page {page} of {pages}
          </span>
          <button type="button" className={buttonClass} disabled={page >= pages} onClick={() => onPage(page + 1)}>
            Next
          </button>
          <button type="button" className={buttonClass} disabled={page >= pages} onClick={() => onPage(pages)}>
            Last
          </button>
        </div>
      )}
    </nav>
  )
}
