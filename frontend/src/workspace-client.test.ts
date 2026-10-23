import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { WorkspaceApiError, workspaceApi } from './workspace-client'

describe('workspaceApi', () => {
  beforeEach(() => {
    vi.stubGlobal('sessionStorage', { getItem: () => 'platform-token' })
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({
      workspace_id: null,
      product: 'platform',
      status: 'not_created',
      sync_state: 'pending',
      usage_bytes: 0,
      quota_bytes: 5 * 1024 ** 3,
      session_expires_at: null,
      last_synced_at: null,
      failure_code: null,
      can_open: true,
      can_stop: false,
    }), { status: 200, headers: { 'Content-Type': 'application/json' } })))
  })

  afterEach(() => vi.unstubAllGlobals())

  it('uses the current-workspace API and bearer token', async () => {
    await workspaceApi.start()

    expect(fetch).toHaveBeenCalledWith(
      expect.stringMatching(/\/workspaces\/current\/start$/),
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({ Authorization: 'Bearer platform-token' }),
      }),
    )
  })

  it('preserves standardized problem codes', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(new Response(JSON.stringify({
      error: { code: 'workspace_capacity_reached', message: 'Workspace capacity is full' },
    }), { status: 429, headers: { 'Content-Type': 'application/json' } }))

    await expect(workspaceApi.start()).rejects.toMatchObject<Partial<WorkspaceApiError>>({
      code: 'workspace_capacity_reached',
      status: 429,
    })
  })
})
