import type {
  Batch,
  BatchDetail,
  CleanupResult,
  GenerationRequest,
  Job,
  TablesResponse,
} from './types'

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!res.ok) {
    let payload: unknown = null
    try {
      payload = await res.json()
    } catch {
      /* non-json error body */
    }
    const detail =
      payload && typeof payload === 'object' && 'detail' in payload
        ? (payload as { detail: unknown }).detail
        : await res.text().catch(() => res.statusText)
    if (detail && typeof detail === 'object' && 'message' in detail) {
      const d = detail as { message: string; details?: Record<string, unknown> }
      const err = new Error(d.message)
      ;(err as Error & { cause?: unknown }).cause = d.details
      throw err
    }
    throw new Error(typeof detail === 'string' ? detail : res.statusText)
  }
  return (await res.json()) as T
}

export const api = {
  tables: () => request<TablesResponse>('/api/tables'),
  generate: (body: GenerationRequest) =>
    request<{ job_id: string }>('/api/generate', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  job: (jobId: string) => request<Job>(`/api/jobs/${jobId}`),
  batches: () => request<Batch[]>('/api/batches'),
  batchDetail: (batchNo: string) =>
    request<BatchDetail>(`/api/batches/${encodeURIComponent(batchNo)}`),
  cleanup: (batchNo: string) =>
    request<CleanupResult>(
      `/api/batches/${encodeURIComponent(batchNo)}/cleanup`,
      { method: 'POST' }
    ),
}
