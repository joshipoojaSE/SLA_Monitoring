import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import type { LogFilters } from '../api/types'

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

/** Upload, then scan for outages, so the dashboard opens on a complete file. */
export const useUpload = () => {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (file: File) => {
      const report = await api.upload(file)
      const outages = await api.scanOutages(report.file_id)
      return { report, outages }
    },
    onSuccess: ({ report }) => {
      queryClient.invalidateQueries({ queryKey: ['files'] })
      queryClient.invalidateQueries({ queryKey: ['stats', report.file_id] })
    },
  })
}
