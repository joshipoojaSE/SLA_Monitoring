import { useState, type DragEvent, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import type { OutageReport, UploadReport } from '../api/types'
import { Badge, Card, Spinner, StateMessage } from '../components/ui'
import { useUpload } from '../hooks/queries'
import { formatDateTime, formatMinutes, formatNumber } from '../lib/format'

const ISSUE_LABELS: Record<string, string> = {
  epoch_timestamps: 'Epoch timestamps converted',
  offset_timestamps: 'Timestamps with an offset, moved to UTC',
  naive_timestamps: 'Timestamps without a zone, read as UTC',
  latency_converted_from_seconds: 'Latencies converted from seconds',
  blank_latency: 'Blank latencies',
  negative_latency: 'Negative latencies dropped',
  invalid_status_codes: 'Invalid status codes',
}

function Report({ report, outages }: { report: UploadReport; outages: OutageReport }) {
  const issues = Object.entries(report.issues).filter(([, count]) => count > 0)
  const facts: [string, string][] = [
    ['Rows received', formatNumber(report.rows_received)],
    ['Clean checks saved', formatNumber(report.clean_checks)],
    ['Rows removed', formatNumber(report.rows_removed)],
    ['Duplicates', formatNumber(report.duplicates.total)],
    ['Covers', `${formatDateTime(report.coverage.start)} – ${formatDateTime(report.coverage.end)} (${report.coverage.days} days)`],
    ['Missing checks', `${formatNumber(report.coverage.missing_checks)} of ${formatNumber(report.coverage.expected_checks)}`],
    ['Incidents found', `${outages.incidents_found} (${formatMinutes(outages.total_downtime_minutes)} downtime)`],
  ]

  return (
    <Card className="space-y-4 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-semibold">{report.file_name}</h2>
        {report.status === 'clean' ? <Badge tone="good">Clean</Badge> : <Badge tone="warn">Accepted with warnings</Badge>}
      </div>

      <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
        {facts.map(([label, value]) => (
          <div key={label} className="flex justify-between gap-4 border-b border-slate-100 py-1">
            <dt className="text-slate-500">{label}</dt>
            <dd className="text-right font-medium tabular-nums">{value}</dd>
          </div>
        ))}
      </dl>

      {issues.length > 0 && (
        <div>
          <h3 className="mb-1 text-sm font-semibold">Cleaned</h3>
          <ul className="list-inside list-disc text-sm text-slate-700">
            {issues.map(([key, count]) => (
              <li key={key}>
                {ISSUE_LABELS[key] ?? key}: {formatNumber(count)}
              </li>
            ))}
          </ul>
        </div>
      )}

      {report.warnings.length > 0 && (
        <div>
          <h3 className="mb-1 text-sm font-semibold">Warnings</h3>
          <ul className="list-inside list-disc text-sm text-amber-800">
            {report.warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </div>
      )}

      <Link
        to={`/?file=${report.file_id}`}
        className="inline-block rounded-md bg-slate-800 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700"
      >
        Open in dashboard
      </Link>
    </Card>
  )
}

export function UploadPage() {
  const [file, setFile] = useState<File | null>(null)
  const [dragging, setDragging] = useState(false)
  const upload = useUpload()

  const onDrop = (event: DragEvent) => {
    event.preventDefault()
    setDragging(false)
    const dropped = event.dataTransfer.files[0]
    if (dropped) setFile(dropped)
  }

  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    if (file) upload.mutate(file)
  }

  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <form onSubmit={onSubmit}>
        <Card className="space-y-4 p-4">
          <div>
            <h1 className="font-semibold">Upload health checks</h1>
            <p className="text-sm text-slate-500">
              A CSV of monitoring checks. It is validated and cleaned, saved, then scanned for outages.
            </p>
          </div>

          <label
            onDragOver={(event) => {
              event.preventDefault()
              setDragging(true)
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={onDrop}
            className={`flex cursor-pointer flex-col items-center justify-center gap-1 rounded-md border-2 border-dashed px-4 py-10 text-sm ${
              dragging ? 'border-sky-500 bg-sky-50' : 'border-slate-300 hover:bg-slate-50'
            }`}
          >
            <input
              type="file"
              accept=".csv,text/csv"
              className="sr-only"
              onChange={(event) => setFile(event.target.files?.[0] ?? null)}
            />
            <span className="font-medium">{file ? file.name : 'Drop a CSV here or click to choose'}</span>
            {file && <span className="text-slate-500">{formatNumber(Math.ceil(file.size / 1024))} KB</span>}
          </label>

          <button
            type="submit"
            disabled={!file || upload.isPending}
            className="inline-flex items-center gap-2 rounded-md bg-slate-800 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {upload.isPending && <Spinner />}
            {upload.isPending ? 'Processing…' : 'Upload'}
          </button>
        </Card>
      </form>

      {upload.error && <StateMessage tone="error">{upload.error.message}</StateMessage>}
      {upload.data && <Report report={upload.data.report} outages={upload.data.outages} />}
    </div>
  )
}
