import { formatNumber } from '../../lib/format'

interface Props {
  page: number
  pageSize: number
  total: number
  onPage: (page: number) => void
}

const buttonClass =
  'rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40'

export function Pagination({ page, pageSize, total, onPage }: Props) {
  const pages = Math.max(1, Math.ceil(total / pageSize))
  const first = total === 0 ? 0 : (page - 1) * pageSize + 1
  const last = Math.min(page * pageSize, total)

  return (
    <nav aria-label="Log pages" className="flex flex-wrap items-center justify-between gap-2 text-sm text-slate-600">
      <span className="tabular-nums">
        {formatNumber(first)}–{formatNumber(last)} of {formatNumber(total)} checks
      </span>
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
    </nav>
  )
}
