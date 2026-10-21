import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, joinApiUrl } from './api'

describe('platform API client', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('does not duplicate a version prefix already present in the API base', () => {
    expect(joinApiUrl('/v1', '/v1/me')).toBe('/v1/me')
    expect(joinApiUrl('https://platform.example/v1/', '/v1/studies')).toBe(
      'https://platform.example/v1/studies',
    )
    expect(joinApiUrl('https://platform.example', '/v1/me')).toBe(
      'https://platform.example/v1/me',
    )
  })

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

  it('removes only the selected product membership scope', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(null, { status: 204 }))
    vi.stubGlobal('fetch', fetch)
    prepareBrowserGlobals()
    await api.removeMembership('study-1', 'user-1', 'aurinara-edc', 'site-1')
    expect(fetch).toHaveBeenCalledWith(
      '/v1/studies/study-1/members/user-1?product=aurinara-edc&site_id=site-1',
      expect.objectContaining({ method: 'DELETE' }),
    )
  })

  it('loads the active Site options for a study', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetch)
    prepareBrowserGlobals()
    await api.sites('study-1')
    expect(fetch).toHaveBeenCalledWith(
      '/v1/studies/study-1/sites',
      expect.objectContaining({ headers: expect.objectContaining({ 'X-Request-ID': 'request-id' }) }),
    )
  })

  it('creates centrally managed roles with an auditable reason', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ id: 'role-1' }), { status: 201, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetch)
    prepareBrowserGlobals()
    await api.createRole('aurinara-edc', {
      key: 'custom.odm_designer',
      name: 'ODM Designer',
      scope_level: 'study',
      permission_codes: ['edc.designer.odm.export'],
      reason: 'Create the reviewed ODM role',
    })
    expect(fetch).toHaveBeenCalledWith(
      '/v1/products/aurinara-edc/roles',
      expect.objectContaining({
        method: 'POST',
        body: expect.stringContaining('Create the reviewed ODM role'),
      }),
    )
  })

  it('filters roles and permissions by the selected product', async () => {
    const fetch = vi.fn().mockImplementation(async () => new Response(JSON.stringify([]), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetch)
    prepareBrowserGlobals()

    await Promise.all([
      api.roles('aurinara-edc'),
      api.permissions('aurinara-edc'),
    ])

    expect(fetch).toHaveBeenNthCalledWith(
      1,
      '/v1/roles?product=aurinara-edc',
      expect.any(Object),
    )
    expect(fetch).toHaveBeenNthCalledWith(
      2,
      '/v1/permissions?product=aurinara-edc',
      expect.any(Object),
    )
  })

  it('protects role edits with the loaded revision', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ id: 'role-1', revision: 3 }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetch)
    prepareBrowserGlobals()
    await api.patchRole('role-1', 2, {
      status: 'inactive',
      reason: 'Retire the role',
    })
    expect(fetch).toHaveBeenCalledWith(
      '/v1/roles/role-1',
      expect.objectContaining({
        method: 'PATCH',
        headers: expect.objectContaining({ 'If-Match': '"2"' }),
      }),
    )
  })
})
