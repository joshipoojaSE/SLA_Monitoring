// Mirrors the response models in BE/app/main.py.

export type Outcome = 'up' | 'down' | 'invalid'

export interface FileSummary {
  file_id: number
  file_name: string
  uploaded_at: string
  rows_received: number
  stored_checks: number
  scanned: boolean
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
  scanned: boolean
  sla_target_pct: number
  coverage_start: string
  coverage_end: string
  days: number
  expected_checks: number
  stored_checks: number
  availability_pct: number | null
  services_breaching: number
  total_downtime_minutes: number
  services: ServiceStats[]
  incidents: OutageIncident[]
}

export interface LogRow {
  checked_at: string
  service_id: string
  status_code: number
  outcome: Outcome
  latency_ms: number | null
  agent_id: string
  region_id: string
}

export interface LogPage {
  total: number
  page: number
  page_size: number
  items: LogRow[]
}

export interface LogFilters {
  dateFrom?: string
  dateTo?: string
  serviceId?: string
  outcome?: Outcome
  page: number
  pageSize: number
}

export interface UploadReport {
  file_id: number
  file_name: string
  status: 'clean' | 'accepted_with_warnings'
  rows_received: number
  clean_checks: number
  rows_removed: number
  coverage: {
    start: string
    end: string
    days: number
    services: string[]
    expected_checks: number
    missing_checks: number
  }
  duplicates: {
    total: number
    exact_rows: number
    same_slot_rows: number
    conflicting_slots: number
  }
  issues: Record<string, number>
  ragged_rows: number
  rejected_rows: { line: number; reason: string }[]
  warnings: string[]
}

export interface OutageReport {
  file_id: number
  file_name: string
  already_processed: boolean
  incidents_found: number
  services_affected: string[]
  total_downtime_minutes: number
}
