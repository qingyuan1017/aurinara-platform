import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from './api'

describe('platform API client', () => {
  afterEach(() => vi.unstubAllGlobals())

  const prepareBrowserGlobals = () => {
    vi.stubGlobal('sessionStorage', { getItem: () => null })
    vi.stubGlobal('crypto', { randomUUID: () => 'request-id' })
  }

  it('sends optimistic concurrency for study changes', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ id: 's1' }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetch)
    prepareBrowserGlobals()
    await api.patchStudy({ id: 's1', revision: 7 } as never, { status: 'archived' })
    expect(fetch).toHaveBeenCalledWith('/v1/studies/s1', expect.objectContaining({ method: 'PATCH', headers: expect.objectContaining({ 'If-Match': '"7"' }) }))
  })

  it('preserves sanitized problem details', async () => {
    prepareBrowserGlobals()
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: { code: 'authorization_denied', message: 'The requested action is not allowed', request_id: 'req-1' } }), { status: 403, headers: { 'Content-Type': 'application/json' } })))
    await expect(api.me()).rejects.toMatchObject({ status: 403, code: 'authorization_denied', requestId: 'req-1' })
  })
})
