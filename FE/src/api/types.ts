// Mirrors the response models in BE/app/main.py.

export type Outcome = 'up' | 'down' | 'invalid'

export interface FileSummary {
  file_id: number
  file_name: string
  uploaded_at: string
  rows_received: number
}

export interface ServiceStats {
  service_id: string
  service_name: string
  up_checks: number
  down_checks: number
  invalid_checks: number
  missing_checks: number
  availability_pct: number | null
  meets_sla: boolean | null
  downtime_minutes: number
  allowed_downtime_minutes: number
  incidents: number
  longest_incident_minutes: number
  avg_latency_ms: number | null
  p95_latency_ms: number | null
}

export interface OutageIncident {
  service_id: string
  day_index: number
  checkpoint_start: number
  checkpoint_end: number
  started_at: string
  ended_at: string
  down_checks: number
  downtime_minutes: number
}

export interface DashboardStats {
  file_id: number
  file_name: string
  sla_target_pct: number
  coverage_start: string
  coverage_end: string
  days: number
  availability_pct: number | null
  services_breaching: number
  total_downtime_minutes: number
  incident_count: number
  longest_incident_minutes: number
  services: ServiceStats[]
}

/** One server-side page of a table. */
export interface Page<T> {
  total: number
  page: number
  page_size: number
  items: T[]
}

export type SortOrder = 'asc' | 'desc'

/** Sort and page for the two stats tables, which fetch their rows on their own. */
export interface TableQuery<K extends string> {
  sort: K
  order: SortOrder
  page: number
  pageSize: number
}

export type ServiceSort = 'service' | 'availability' | 'sla' | 'downtime' | 'incidents' | 'longest' | 'p95' | 'gaps'
export type IncidentSort = 'service' | 'started' | 'ended' | 'checks' | 'downtime'

export interface LogRow {
  checked_at: string
  service_id: string
  status_code: number
  outcome: Outcome
  latency_ms: number | null
  agent_id: string
  region_id: string
}

export type LogPage = Page<LogRow>

export type LogSort = 'checked_at' | 'service' | 'status' | 'outcome' | 'latency' | 'agent' | 'region'

export interface LogFilters {
  dateFrom?: string
  dateTo?: string
  serviceId?: string
  outcome?: Outcome
  sort: LogSort
  order: SortOrder
  page: number
  pageSize: number
}

/** POST /uploads: the file is in S3 and the Lambda is working on it. */
export interface UploadAccepted {
  file_id: number
  file_name: string
  status: 'processing'
}

/** GET /uploads/{id}: 'processing' until the Lambda finishes. */
export interface UploadStatus {
  file_id: number
  file_name: string
  uploaded_at: string
  status: 'processing' | 'done' | 'failed'
  error_message: string | null
  rows_received: number
}
