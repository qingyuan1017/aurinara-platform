import { getToken } from './auth'

export type WorkspaceStatus = 'not_created' | 'pending' | 'running' | 'stopping' | 'stopped' | 'failed' | 'expired'
export type WorkspaceSyncState = 'pending' | 'syncing' | 'synced' | 'quota_exceeded' | 'failed'

export interface WorkspaceState {
  workspace_id: string | null
  product: string
  status: WorkspaceStatus
  sync_state: WorkspaceSyncState
  usage_bytes: number
  quota_bytes: number
  session_expires_at: string | null
  last_synced_at: string | null
  failure_code: string | null
  can_open: boolean
  can_stop: boolean
}

export interface WorkspaceLaunch {
  url: string
  expires_at: string
}

export class WorkspaceApiError extends Error {
  constructor(message: string, readonly code: string, readonly status: number) {
    super(message)
  }
}

const base = (import.meta.env.VITE_API_BASE_URL || '/v1').replace(/\/$/, '')

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getToken()
  const response = await fetch(`${base}${path}`, {
    ...init,
    headers: { Authorization: `Bearer ${token}`, ...init?.headers },
  })
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    throw new WorkspaceApiError(
      body?.error?.message || `Workspace request failed (${response.status})`,
      body?.error?.code || 'workspace_request_failed',
      response.status,
    )
  }
  return response.json() as Promise<T>
}

export const workspaceApi = {
  current: () => request<WorkspaceState>('/workspaces/current'),
  start: () => request<WorkspaceState>('/workspaces/current/start', { method: 'POST' }),
  stop: () => request<WorkspaceState>('/workspaces/current/stop', { method: 'POST' }),
  launch: () => request<WorkspaceLaunch>('/workspaces/current/launch', { method: 'POST' }),
}
