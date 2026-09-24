import type {
  DashboardStats,
  FileSummary,
  IncidentSort,
  LogFilters,
  LogPage,
  OutageIncident,
  OutageReport,
  Page,
  ServiceSort,
  ServiceStats,
  TableQuery,
  UploadAccepted,
  UploadStatus,
} from './types'

const API_URL = (import.meta.env.VITE_API_URL ?? 'http://localhost:8001').replace(/\/$/, '')

export class ApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

// FastAPI puts the reason in `detail`: a string, a {message} object from the
// upload checks, or a list of validation errors.
function detailMessage(body: unknown, fallback: string): string {
  const detail = (body as { detail?: unknown } | null)?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) return detail.map((item) => item?.msg ?? String(item)).join('; ')
  if (detail && typeof detail === 'object' && 'message' in detail) return String(detail.message)
  return fallback
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${API_URL}${path}`, init)
  } catch {
    throw new ApiError(0, 'Could not reach the API. Check that it is running and VITE_API_URL is right.')
  }

  const body = await response.json().catch(() => null)
  if (!response.ok) {
    throw new ApiError(response.status, detailMessage(body, `Request failed (${response.status}).`))
  }
  return body as T
}

function tableParams(query: TableQuery<string>): URLSearchParams {
  return new URLSearchParams({
    sort: query.sort,
    order: query.order,
    page: String(query.page),
    page_size: String(query.pageSize),
  })
}

export const api = {
  files: () => request<FileSummary[]>('/files'),

  stats: (fileId: number) => request<DashboardStats>(`/files/${fileId}/stats`),

  services: (fileId: number, query: TableQuery<ServiceSort>) =>
    request<Page<ServiceStats>>(`/files/${fileId}/stats/services?${tableParams(query)}`),

  incidents: (fileId: number, query: TableQuery<IncidentSort>) =>
    request<Page<OutageIncident>>(`/files/${fileId}/stats/incidents?${tableParams(query)}`),

  logs: (fileId: number, filters: LogFilters) => {
    const params = new URLSearchParams({
      page: String(filters.page),
      page_size: String(filters.pageSize),
      sort: filters.sort,
      order: filters.order,
    })
    if (filters.dateFrom) params.set('date_from', filters.dateFrom)
    if (filters.dateTo) params.set('date_to', filters.dateTo)
    if (filters.serviceId) params.set('service_id', filters.serviceId)
    if (filters.outcome) params.set('outcome', filters.outcome)
    return request<LogPage>(`/files/${fileId}/logs?${params}`)
  },

  upload: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<UploadAccepted>('/uploads', { method: 'POST', body: form })
  },

  uploadStatus: (fileId: number) => request<UploadStatus>(`/uploads/${fileId}`),

  scanOutages: (fileId: number) => request<OutageReport>(`/outage?file_id=${fileId}`, { method: 'POST' }),
}
