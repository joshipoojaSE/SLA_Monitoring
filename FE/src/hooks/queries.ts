import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import type { IncidentSort, LogFilters, ServiceSort, TableQuery, UploadStatus } from '../api/types'

export const useFiles = () => useQuery({ queryKey: ['files'], queryFn: api.files })

export const useStats = (fileId: number | undefined) =>
  useQuery({
    queryKey: ['stats', fileId],
    queryFn: () => api.stats(fileId!),
    enabled: fileId !== undefined,
  })

export const useLogs = (fileId: number | undefined, filters: LogFilters) =>
  useQuery({
    queryKey: ['logs', fileId, filters],
    queryFn: () => api.logs(fileId!, filters),
    enabled: fileId !== undefined,
    // Keep the current page on screen while the next one loads.
    placeholderData: keepPreviousData,
  })

// Both sit under ['stats', fileId], so anything that refreshes a file's stats refreshes the tables too.
export const useServicePage = (fileId: number, query: TableQuery<ServiceSort>) =>
  useQuery({
    queryKey: ['stats', fileId, 'services', query],
    queryFn: () => api.services(fileId, query),
    placeholderData: keepPreviousData,
  })

export const useIncidentPage = (fileId: number, query: TableQuery<IncidentSort>) =>
  useQuery({
    queryKey: ['stats', fileId, 'incidents', query],
    queryFn: () => api.incidents(fileId, query),
    placeholderData: keepPreviousData,
  })

const POLL_INTERVAL_MS = 2000
// The Lambda's own timeout is 300 s; past that it will not finish.
const POLL_TIMEOUT_MS = 330_000

/** Wait for the Lambda to finish with an upload, which it does after the API returns. */
async function waitForUpload(fileId: number): Promise<UploadStatus> {
  const deadline = Date.now() + POLL_TIMEOUT_MS
  for (;;) {
    const status = await api.uploadStatus(fileId)
    if (status.status === 'done') return status
    if (status.status === 'failed') throw new Error(status.error_message ?? `File ${fileId} could not be processed.`)
    if (Date.now() > deadline) throw new Error(`File ${fileId} is still processing. Check the Lambda's logs.`)
    await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS))
  }
}

/** Upload, wait for the Lambda to save it, then read back its outage scan. */
export const useUpload = () => {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (file: File) => {
      const accepted = await api.upload(file)
      const upload = await waitForUpload(accepted.file_id)
      // The Lambda has already scanned the file, so this only reads the result.
      const outages = await api.scanOutages(upload.file_id)
      return { upload, outages }
    },
    onSuccess: ({ upload }) => {
      queryClient.invalidateQueries({ queryKey: ['files'] })
      queryClient.invalidateQueries({ queryKey: ['stats', upload.file_id] })
    },
  })
}
