import type { ReactNode } from 'react'

export function Card({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <section className={`rounded-lg border border-slate-200 bg-white shadow-sm ${className}`}>{children}</section>
}

type Tone = 'good' | 'bad' | 'warn' | 'neutral'

const TONES: Record<Tone, string> = {
  good: 'bg-emerald-50 text-emerald-700 ring-emerald-600/20',
  bad: 'bg-red-50 text-red-700 ring-red-600/20',
  warn: 'bg-amber-50 text-amber-800 ring-amber-600/20',
  neutral: 'bg-slate-100 text-slate-700 ring-slate-500/20',
}

export function Badge({ tone, children }: { tone: Tone; children: ReactNode }) {
  return (
    <span className={`inline-flex items-center rounded px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${TONES[tone]}`}>
      {children}
    </span>
  )
}

export function Spinner() {
  return (
    <span
      className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-slate-300 border-t-slate-700"
      aria-hidden
    />
  )
}

/** Loading, error and empty states share one look. */
export function StateMessage({ tone = 'neutral', children }: { tone?: 'neutral' | 'error'; children: ReactNode }) {
  const colour = tone === 'error' ? 'border-red-200 bg-red-50 text-red-700' : 'border-slate-200 bg-slate-50 text-slate-600'
  return (
    <div role={tone === 'error' ? 'alert' : 'status'} className={`flex items-center gap-2 rounded-md border px-4 py-6 text-sm ${colour}`}>
      {children}
    </div>
  )
}

export function Skeleton({ className = '' }: { className?: string }) {
  return <div className={`animate-pulse rounded bg-slate-200 ${className}`} />
}

export type SortDir = 'asc' | 'desc'

export interface SortState<K extends string> {
  key: K
  dir: SortDir
}

/** Clicking the active column reverses it; clicking another starts at that column's own first direction. */
export function nextSort<K extends string>(current: SortState<K>, key: K, firstDir: SortDir): SortState<K> {
  return current.key === key ? { key, dir: current.dir === 'asc' ? 'desc' : 'asc' } : { key, dir: firstDir }
}

/**
 * Horizontal cell padding for every table: even on both sides, so a
 * right-aligned column never sits flush against a left-aligned one, and flush
 * with the card at the table's outer edges.
 */
export const cellX = 'px-3 first:pl-0 last:pr-0'

interface SortHeaderProps<K extends string> {
  label: ReactNode
  sortKey: K
  sort: SortState<K>
  onSort: (key: K, firstDir: SortDir) => void
  /** The direction a first click sorts in: the "worst" or "biggest" end first. */
  firstDir?: SortDir
  align?: 'left' | 'right'
  title?: string
  className?: string
}

export function SortHeader<K extends string>({
  label,
  sortKey,
  sort,
  onSort,
  firstDir = 'asc',
  align = 'left',
  title,
  className = `py-2 ${cellX}`,
}: SortHeaderProps<K>) {
  const active = sort.key === sortKey
  return (
    <th
      className={`${className} font-medium ${align === 'right' ? 'text-right' : ''}`}
      aria-sort={active ? (sort.dir === 'asc' ? 'ascending' : 'descending') : 'none'}
      title={title}
    >
      {/* On a right-aligned column the arrow goes first, so the label's edge lines up with the numbers below. */}
      <button
        type="button"
        onClick={() => onSort(sortKey, firstDir)}
        className={`inline-flex items-center gap-1 uppercase tracking-wide hover:text-slate-800 ${align === 'right' ? 'flex-row-reverse' : ''} ${active ? 'text-slate-800' : ''}`}
      >
        {label}
        <span aria-hidden className={active ? '' : 'text-slate-300'}>
          {active ? (sort.dir === 'asc' ? '▲' : '▼') : '↕'}
        </span>
      </button>
    </th>
  )
}

export const selectClass =
  'rounded-md border border-slate-300 bg-white px-2.5 py-1.5 text-sm shadow-sm focus:border-slate-500 focus:outline-none focus:ring-1 focus:ring-slate-500'
