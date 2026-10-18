import { getToken } from './auth'
import type { Decision, Membership, Permission, ProblemPayload, RepositoryNode, RepositoryPage, RepositoryVersion, Role, RoleChange, Site, StoredObject, Study, SystemAssignment, User } from './types'

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
  createRole: (product: string, body: { name: string; description?: string; scope_level: string; permission_codes: string[]; reason: string }) =>
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
  downloadRepositoryVersion: (versionId: string) => request<{ url: string; expires_at: string }>(`/v1/repository/versions/${versionId}/download`, { method: 'POST' }),
  addRepositoryVersion: (nodeId: string, body: object) => request<RepositoryVersion>(`/v1/repository/nodes/${nodeId}/versions`, { method: 'POST', body: JSON.stringify(body) }),
}

async function sha256(file: File): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', await file.arrayBuffer())
  return [...new Uint8Array(digest)].map((value) => value.toString(16).padStart(2, '0')).join('')
}

export async function uploadNeutralFile(studyId: string, file: File, parentId: string | null, replace?: RepositoryNode): Promise<void> {
  const checksum = await sha256(file)
  let objectId: string
  try {
    const upload = await request<{ object_id: string; upload_id: string; parts: Array<{ part_number: number; url: string }> }>('/v1/objects/upload-sessions', {
      method: 'POST',
      body: JSON.stringify({ study_id: studyId, owner_product: 'platform', classification: replace?.classification || 'internal', media_type: file.type || 'application/octet-stream', expected_sha256: checksum, expected_size_bytes: file.size, part_count: 1 }),
    })
    objectId = upload.object_id
    const part = upload.parts[0]
    const put = await fetch(part.url, { method: 'PUT', body: file })
    if (!put.ok) throw new Error(`Object upload failed (${put.status})`)
    await request(`/v1/objects/${objectId}/finalize`, { method: 'POST', body: JSON.stringify({ parts: [{ part_number: 1, etag: put.headers.get('ETag') || 'uploaded' }] }) })
  } catch (error) {
    if (!(error instanceof ApiError) || error.code !== 'object_already_exists') throw error
    const details = error.details as { object_id?: string } | undefined
    if (!details?.object_id) throw error
    objectId = details.object_id
  }
  const key = crypto.randomUUID()
  const reference = await request<{ id: string }>(`/v1/objects/${objectId}/references`, {
    method: 'POST',
    body: JSON.stringify({
      product: 'platform', external_reference: `neutral:${key}`, idempotency_key: key,
      ...(replace ? {} : { repository: { filename: file.name, parent_id: parentId, source_type: 'neutral_upload', source_id: key, source_status: 'available', module: 'repository', solution: 'file-management', purpose: 'user-files', classification: 'internal', access: { read_action: 'platform.repository.read', write_action: 'platform.repository.write' }, provenance: { original_filename: file.name } } }),
    }),
  })
  if (replace) {
    await api.addRepositoryVersion(replace.id, { object_id: objectId, object_reference_id: reference.id, product: 'platform', external_reference: `neutral:${key}`, source_type: 'neutral_upload', source_id: key, source_status: 'available', provenance: { original_filename: file.name } })
  }
}
