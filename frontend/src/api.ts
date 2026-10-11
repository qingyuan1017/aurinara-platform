import { getToken } from './auth'
import type { Decision, Membership, ProblemPayload, StoredObject, Study, User } from './types'

const base = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '')

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public requestId?: string) {
    super(message)
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getToken()
  const response = await fetch(`${base}${path}`, {
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
      problem.error?.message || `Request failed (${response.status})`, problem.error?.request_id)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export const api = {
  health: () => request<{ status: string }>('/health/live'),
  me: () => request<User>('/v1/me'),
  studies: () => request<Study[]>('/v1/studies'),
  createStudy: (body: { code: string; display_name: string }) =>
    request<Study>('/v1/studies', { method: 'POST', body: JSON.stringify(body) }),
  patchStudy: (study: Study, body: { display_name?: string; status?: string }) =>
    request<Study>(`/v1/studies/${study.id}`, { method: 'PATCH', headers: { 'If-Match': `"${study.revision}"` }, body: JSON.stringify(body) }),
  memberships: (studyId: string) => request<Membership[]>(`/v1/studies/${studyId}/memberships`),
  replaceMembership: (studyId: string, userId: string, roleIds: string[], siteId?: string) =>
    request<Membership[]>(`/v1/studies/${studyId}/members/${userId}`, { method: 'PUT', body: JSON.stringify({ role_ids: roleIds, site_id: siteId || null }) }),
  removeMembership: (studyId: string, userId: string, siteId?: string | null) =>
    request<void>(`/v1/studies/${studyId}/members/${userId}${siteId ? `?site_id=${siteId}` : ''}`, { method: 'DELETE' }),
  createUser: (body: { issuer: string; subject: string; email: string; display_name: string }) =>
    request<User>('/v1/users', { method: 'POST', body: JSON.stringify(body) }),
  patchUser: (id: string, revision: number, body: { email?: string; display_name?: string; status?: string }) =>
    request<User>(`/v1/users/${id}`, { method: 'PATCH', headers: { 'If-Match': `"${revision}"` }, body: JSON.stringify(body) }),
  decide: (body: object) => request<Decision>('/v1/authorization/decisions', { method: 'POST', body: JSON.stringify(body) }),
  object: (id: string) => request<StoredObject>(`/v1/objects/${id}`),
  download: (id: string) => request<{ url: string; expires_at: string }>(`/v1/objects/${id}/download`, { method: 'POST' }),
}
