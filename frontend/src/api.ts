import { getToken } from './auth'
import type { Decision, Membership, PdfAnnotation, Permission, ProblemPayload, RepositoryJob, RepositoryNode, RepositoryPage, RepositoryVersion, Role, RoleChange, Site, StoredObject, Study, SystemAssignment, TabularPreview, User } from './types'

const base = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '')

export function joinApiUrl(baseUrl: string, path: string): string {
  const normalizedBase = baseUrl.replace(/\/$/, '')
  if (!normalizedBase) return path
  if (normalizedBase.endsWith('/v1') && path.startsWith('/v1/')) {
    return `${normalizedBase}${path.slice('/v1'.length)}`
  }
  return `${normalizedBase}${path}`
}

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public requestId?: string, public details?: unknown) {
    super(message)
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getToken()
  const response = await fetch(joinApiUrl(base, path), {
    ...init,
    headers: {
      ...(init.body ? { 'Content-Type': 'application/json' } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      'X-Request-ID': crypto.randomUUID(),
      ...init.headers,
    },
  })
  if (!response.ok) {
    let problem: ProblemPayload = {}
    try { problem = await response.json() as ProblemPayload } catch { /* sanitized fallback */ }
    throw new ApiError(response.status, problem.error?.code || 'request_failed',
      problem.error?.message || `Request failed (${response.status})`, problem.error?.request_id, problem.error?.details)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export const api = {
  health: () => request<{ status: string }>('/health/live'),
  me: () => request<User>('/v1/me'),
  users: () => request<User[]>('/v1/users'),
  roles: (product?: string) => request<Role[]>(`/v1/roles${product ? `?product=${encodeURIComponent(product)}` : ''}`),
  permissions: (product: string) => request<Permission[]>(`/v1/permissions?product=${encodeURIComponent(product)}`),
  createRole: (product: string, body: { key: string; name: string; description?: string; scope_level: string; permission_codes: string[]; reason: string }) =>
    request<Role>(`/v1/products/${encodeURIComponent(product)}/roles`, { method: 'POST', body: JSON.stringify(body) }),
  patchRole: (roleId: string, revision: number, body: { name?: string; description?: string | null; scope_level?: string; permission_codes?: string[]; status?: string; reason: string }) =>
    request<Role>(`/v1/roles/${roleId}`, { method: 'PATCH', headers: { 'If-Match': `"${revision}"` }, body: JSON.stringify(body) }),
  roleChanges: (roleId: string) => request<RoleChange[]>(`/v1/roles/${roleId}/changes`),
  systemAssignments: (userId: string) => request<SystemAssignment[]>(`/v1/users/${userId}/system-assignments`),
  replaceSystemAssignments: (userId: string, product: string, roleIds: string[]) =>
    request<SystemAssignment[]>(`/v1/users/${userId}/system-assignments`, { method: 'PUT', body: JSON.stringify({ product, role_ids: roleIds }) }),
  studies: () => request<Study[]>('/v1/studies'),
  createStudy: (body: { code: string; display_name: string; sponsor: string }) =>
    request<Study>('/v1/studies', { method: 'POST', body: JSON.stringify(body) }),
  patchStudy: (study: Study, body: { display_name?: string; sponsor?: string; status?: string }) =>
    request<Study>(`/v1/studies/${study.id}`, { method: 'PATCH', headers: { 'If-Match': `"${study.revision}"` }, body: JSON.stringify(body) }),
  memberships: (studyId: string) => request<Membership[]>(`/v1/studies/${studyId}/memberships`),
  sites: (studyId: string) => request<Site[]>(`/v1/studies/${studyId}/sites`),
  replaceMembership: (studyId: string, userId: string, product: string, roleIds: string[], siteId?: string) =>
    request<Membership[]>(`/v1/studies/${studyId}/members/${userId}`, { method: 'PUT', body: JSON.stringify({ product, role_ids: roleIds, site_id: siteId || null }) }),
  removeMembership: (studyId: string, userId: string, product: string, siteId?: string | null) => {
    const query = new URLSearchParams({ product })
    if (siteId) query.set('site_id', siteId)
    return request<void>(`/v1/studies/${studyId}/members/${userId}?${query}`, { method: 'DELETE' })
  },
  createUser: (body: { issuer: string; subject: string; email: string; display_name: string }) =>
    request<User>('/v1/users', { method: 'POST', body: JSON.stringify(body) }),
  patchUser: (id: string, revision: number, body: { email?: string; display_name?: string; status?: string }) =>
    request<User>(`/v1/users/${id}`, { method: 'PATCH', headers: { 'If-Match': `"${revision}"` }, body: JSON.stringify(body) }),
  decide: (body: object) => request<Decision>('/v1/authorization/decisions', { method: 'POST', body: JSON.stringify(body) }),
  object: (id: string) => request<StoredObject>(`/v1/objects/${id}`),
  download: (id: string) => request<{ url: string; expires_at: string }>(`/v1/objects/${id}/download`, { method: 'POST' }),
  repository: (studyId: string, parameters: URLSearchParams) =>
    request<RepositoryPage>(`/v1/studies/${studyId}/repository?${parameters}`),
  createRepositoryFolder: (studyId: string, body: { name: string; parent_id: string | null }) =>
    request<RepositoryNode>(`/v1/studies/${studyId}/repository/folders`, { method: 'POST', body: JSON.stringify(body) }),
  patchRepositoryNode: (node: RepositoryNode, body: { name?: string; parent_id?: string | null; source_status?: string }) =>
    request<RepositoryNode>(`/v1/repository/nodes/${node.id}`, { method: 'PATCH', headers: { 'If-Match': `"${node.revision}"` }, body: JSON.stringify(body) }),
  archiveRepositoryNode: (node: RepositoryNode) =>
    request<RepositoryNode>(`/v1/repository/nodes/${node.id}`, { method: 'DELETE', headers: { 'If-Match': `"${node.revision}"` } }),
  restoreRepositoryNode: (node: RepositoryNode) =>
    request<RepositoryNode>(`/v1/repository/nodes/${node.id}/restore`, { method: 'POST', headers: { 'If-Match': `"${node.revision}"` } }),
  repositoryVersions: (nodeId: string) => request<RepositoryVersion[]>(`/v1/repository/nodes/${nodeId}/versions`),
  repositoryVersion: (versionId: string) => request<RepositoryVersion & { study_id: string; filename: string }>(`/v1/repository/versions/${versionId}`),
  repositoryNode: (nodeId: string) => request<RepositoryNode>(`/v1/repository/nodes/${nodeId}`),
  downloadRepositoryVersion: (versionId: string) => request<{ url: string; expires_at: string }>(`/v1/repository/versions/${versionId}/download`, { method: 'POST' }),
  addRepositoryVersion: (nodeId: string, body: object) => request<RepositoryVersion>(`/v1/repository/nodes/${nodeId}/versions`, { method: 'POST', body: JSON.stringify(body) }),
  tabularPreview: (versionId: string, body: object) => request<TabularPreview>(`/v1/repository/versions/${versionId}/tabular-preview`, { method: 'POST', body: JSON.stringify(body) }),
  annotations: (versionId: string) => request<PdfAnnotation[]>(`/v1/repository/versions/${versionId}/annotations`),
  createAnnotation: (versionId: string, body: object) => request<PdfAnnotation>(`/v1/repository/versions/${versionId}/annotations`, { method: 'POST', body: JSON.stringify(body) }),
  patchAnnotation: (annotation: PdfAnnotation, body: object) => request<PdfAnnotation>(`/v1/repository/annotations/${annotation.id}`, { method: 'PATCH', headers: { 'If-Match': `"${annotation.revision}"` }, body: JSON.stringify(body) }),
  deleteAnnotation: (annotation: PdfAnnotation) => request<void>(`/v1/repository/annotations/${annotation.id}`, { method: 'DELETE', headers: { 'If-Match': `"${annotation.revision}"` } }),
  convertRepositoryVersion: (versionId: string, target: 'csv' | 'parquet', filename: string) => request<RepositoryJob>(`/v1/repository/versions/${versionId}/conversions`, { method: 'POST', body: JSON.stringify({ target, filename, idempotency_key: crypto.randomUUID() }) }),
  annotatedExport: (versionId: string, filename: string) => request<RepositoryJob>(`/v1/repository/versions/${versionId}/annotated-exports`, { method: 'POST', body: JSON.stringify({ filename, idempotency_key: crypto.randomUUID() }) }),
  repositoryJob: (jobId: string) => request<RepositoryJob>(`/v1/repository/jobs/${jobId}`),
  officeSession: (versionId: string, mode: 'view' | 'edit') => request<{ session_id: string; document_server_url: string; config: Record<string, unknown> }>(`/v1/repository/versions/${versionId}/office-sessions`, { method: 'POST', body: JSON.stringify({ mode }) }),
}

export type UploadProgress = { phase: 'hashing' | 'uploading' | 'completing'; loaded: number; total: number }

async function sha256(file: File, onProgress?: (progress: UploadProgress) => void, signal?: AbortSignal): Promise<string> {
  return new Promise((resolve, reject) => {
    const worker = new Worker(new URL('./repositoryHash.worker.ts', import.meta.url), { type: 'module' })
    const abort = () => { worker.terminate(); reject(new DOMException('Upload cancelled', 'AbortError')) }
    signal?.addEventListener('abort', abort, { once: true })
    worker.onmessage = (event: MessageEvent<{ type: string; loaded?: number; total?: number; sha256?: string }>) => {
      if (event.data.type === 'progress') onProgress?.({ phase: 'hashing', loaded: event.data.loaded || 0, total: event.data.total || file.size })
      if (event.data.type === 'complete' && event.data.sha256) { signal?.removeEventListener('abort', abort); worker.terminate(); resolve(event.data.sha256) }
    }
    worker.onerror = (error) => { worker.terminate(); reject(error) }
    worker.postMessage(file)
  })
}

async function uploadPart(url: string, body: Blob, signal?: AbortSignal): Promise<string> {
  let lastError: unknown
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      const response = await fetch(url, { method: 'PUT', body, signal })
      if (!response.ok) throw new Error(`Object upload failed (${response.status})`)
      return response.headers.get('ETag') || 'uploaded'
    } catch (error) { lastError = error; if (signal?.aborted) throw error }
  }
  throw lastError
}

export async function uploadNeutralFile(studyId: string, file: File, parentId: string | null, replace?: RepositoryNode, onProgress?: (progress: UploadProgress) => void, signal?: AbortSignal): Promise<RepositoryNode> {
  const checksum = await sha256(file, onProgress, signal)
  const partSize = 16 * 1024 * 1024
  const partCount = Math.max(1, Math.ceil(file.size / partSize))
  const upload = await request<{ object_id: string; parts: Array<{ part_number: number; url: string }>; already_available: boolean }> (`/v1/studies/${studyId}/repository/uploads`, {
    method: 'POST',
    body: JSON.stringify({ filename: file.name, size_bytes: file.size, sha256: checksum, media_type: file.type || 'application/octet-stream', part_count: partCount, parent_id: replace ? null : parentId, replacement_node_id: replace?.id || null, classification: replace?.classification || 'internal', idempotency_key: crypto.randomUUID() }),
  })
  try {
    let loaded = 0
    const etags = new Map<number, string>()
    const queue = [...upload.parts]
    const runners = Array.from({ length: Math.min(3, queue.length) }, async () => {
      while (queue.length) {
        const part = queue.shift()
        if (!part) return
        const start = (part.part_number - 1) * partSize
        const blob = file.slice(start, Math.min(start + partSize, file.size))
        etags.set(part.part_number, await uploadPart(part.url, blob, signal))
        loaded += blob.size
        onProgress?.({ phase: 'uploading', loaded, total: file.size })
      }
    })
    await Promise.all(runners)
    onProgress?.({ phase: 'completing', loaded: file.size, total: file.size })
    return await request<RepositoryNode>(`/v1/repository/uploads/${upload.object_id}/complete`, { method: 'POST', body: JSON.stringify({ parts: [...etags].sort(([a], [b]) => a - b).map(([part_number, etag]) => ({ part_number, etag })) }) })
  } catch (error) {
    await request<void>(`/v1/repository/uploads/${upload.object_id}`, { method: 'DELETE' }).catch(() => undefined)
    throw error
  }
}

export async function waitForRepositoryJob(
  initial: RepositoryJob,
  timeoutMs = 120_000,
): Promise<RepositoryJob> {
  const deadline = Date.now() + timeoutMs
  let job = initial
  while (job.state === 'queued' || job.state === 'running') {
    if (Date.now() >= deadline) throw new Error('Repository job timed out')
    await new Promise((resolve) => window.setTimeout(resolve, 1000))
    job = await api.repositoryJob(job.id)
  }
  if (job.state === 'failed') {
    throw new Error(`Repository job failed (${job.failure_code || 'unknown'})`)
  }
  return job
}
