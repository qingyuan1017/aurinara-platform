import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { completeLogin, getToken, sanitizeReturnTo } from './auth'

class MemoryStorage {
  private values = new Map<string, string>()

  getItem(key: string): string | null {
    return this.values.get(key) ?? null
  }

  setItem(key: string, value: string): void {
    this.values.set(key, value)
  }

  removeItem(key: string): void {
    this.values.delete(key)
  }
}

describe('platform OIDC authentication', () => {
  beforeEach(() => {
    vi.stubGlobal('sessionStorage', new MemoryStorage())
    vi.stubEnv('VITE_OIDC_TOKEN_URL', 'https://identity.example/oauth2/token')
    vi.stubEnv('VITE_OIDC_CLIENT_ID', 'platform-spa')
    vi.stubEnv('VITE_OIDC_REDIRECT_URI', 'https://platform.example/auth/callback')
    sessionStorage.setItem('aurinara.platform.oidc_state', 'expected-state')
    sessionStorage.setItem('aurinara.platform.pkce_verifier', 'verifier')
    sessionStorage.setItem('aurinara.platform.return_to', '/#repository')
  })

  afterEach(() => {
    vi.unstubAllEnvs()
    vi.unstubAllGlobals()
  })

  it('stores the ID token and restores the same-origin destination', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      access_token: 'access-token',
      id_token: 'id-token',
    }), { status: 200, headers: { 'Content-Type': 'application/json' } })))

    await expect(completeLogin('?code=authorization-code&state=expected-state'))
      .resolves.toBe('/#repository')
    expect(getToken()).toBe('id-token')
  })

  it('rejects a token response that cannot authenticate a browser user', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      access_token: 'access-token-only',
    }), { status: 200, headers: { 'Content-Type': 'application/json' } })))

    await expect(completeLogin('?code=authorization-code&state=expected-state'))
      .rejects.toThrow('ID token')
    expect(getToken()).toBeNull()
  })

  it('rejects cross-origin and callback return destinations', () => {
    expect(sanitizeReturnTo('//attacker.example/path')).toBe('/')
    expect(sanitizeReturnTo('https://attacker.example/path')).toBe('/')
    expect(sanitizeReturnTo('/auth/callback?code=old')).toBe('/')
    expect(sanitizeReturnTo('/#memberships')).toBe('/#memberships')
  })
})
