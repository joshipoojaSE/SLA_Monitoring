// Checks sit on a UTC grid and the API filters by UTC day, so everything is
// shown in UTC. Local time would shift incidents across midnight.

const dateTime = new Intl.DateTimeFormat('en-GB', {
  timeZone: 'UTC',
  year: 'numeric',
  month: 'short',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
})

const dateOnly = new Intl.DateTimeFormat('en-GB', { timeZone: 'UTC', year: 'numeric', month: 'short', day: '2-digit' })

export const formatDateTime = (iso: string) => dateTime.format(new Date(iso))

export const formatDate = (iso: string) => dateOnly.format(new Date(iso))

/** The YYYY-MM-DD UTC day an ISO timestamp falls on. */
export const utcDay = (iso: string) => iso.slice(0, 10)

export const formatPct = (value: number | null, digits = 3) => (value === null ? '—' : `${value.toFixed(digits)}%`)

export const formatNumber = (value: number) => value.toLocaleString('en-US')

export function formatMinutes(minutes: number): string {
  if (minutes < 60) return `${minutes} min`
  const hours = Math.floor(minutes / 60)
  const rest = minutes % 60
  return rest ? `${hours} h ${rest} min` : `${hours} h`
}

export const formatMs = (value: number | null) => (value === null ? '—' : `${formatNumber(Math.round(value))} ms`)
