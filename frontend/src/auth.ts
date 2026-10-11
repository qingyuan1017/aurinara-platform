const TOKEN_KEY = 'aurinara.platform.access_token'
const STATE_KEY = 'aurinara.platform.oidc_state'
const VERIFIER_KEY = 'aurinara.platform.pkce_verifier'

export interface IdentityClaims {
  sub: string
  email?: string
  name?: string
  preferred_username?: string
  exp?: number
}

function config() {
  return {
    authorizeUrl: import.meta.env.VITE_OIDC_AUTHORIZE_URL?.trim(),
    tokenUrl: import.meta.env.VITE_OIDC_TOKEN_URL?.trim(),
    clientId: import.meta.env.VITE_OIDC_CLIENT_ID?.trim(),
    redirectUri: import.meta.env.VITE_OIDC_REDIRECT_URI?.trim(),
    scopes: import.meta.env.VITE_OIDC_SCOPES?.trim() || 'openid profile email',
  }
}

function encode(bytes: Uint8Array): string {
  let value = ''
  bytes.forEach((byte) => (value += String.fromCharCode(byte)))
  return btoa(value).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

function random(length = 48): string {
  const bytes = new Uint8Array(length)
  crypto.getRandomValues(bytes)
  return encode(bytes)
}

export function getToken(): string | null {
  return sessionStorage.getItem(TOKEN_KEY) || import.meta.env.VITE_DEV_ACCESS_TOKEN?.trim() || null
}

export function clearToken(): void {
  sessionStorage.removeItem(TOKEN_KEY)
}

export function setToken(token: string): void {
  sessionStorage.setItem(TOKEN_KEY, token)
}

export function isOidcConfigured(): boolean {
  const oidc = config()
  return Boolean(oidc.authorizeUrl && oidc.tokenUrl && oidc.clientId && oidc.redirectUri)
}

export function claims(): IdentityClaims | null {
  const token = getToken()
  if (!token) return null
  try {
    const segment = token.split('.')[1]
    if (!segment) return { sub: 'development-token' }
    const normalized = segment.replace(/-/g, '+').replace(/_/g, '/')
    const payload = JSON.parse(atob(normalized.padEnd(Math.ceil(normalized.length / 4) * 4, '='))) as IdentityClaims
    if (!payload.sub || (payload.exp && payload.exp * 1000 <= Date.now())) return null
    return payload
  } catch {
    return null
  }
}

export async function startLogin(): Promise<void> {
  const oidc = config()
  if (!oidc.authorizeUrl || !oidc.clientId || !oidc.redirectUri) {
    throw new Error('OIDC is not configured for this console')
  }
  const state = random(32)
  const verifier = random()
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier))
  sessionStorage.setItem(STATE_KEY, state)
  sessionStorage.setItem(VERIFIER_KEY, verifier)
  const query = new URLSearchParams({
    response_type: 'code', client_id: oidc.clientId, redirect_uri: oidc.redirectUri,
    scope: oidc.scopes, state, code_challenge: encode(new Uint8Array(digest)),
    code_challenge_method: 'S256',
  })
  window.location.assign(`${oidc.authorizeUrl}?${query}`)
}

export async function completeLogin(search: string): Promise<void> {
  const query = new URLSearchParams(search)
  const state = query.get('state')
  const code = query.get('code')
  const verifier = sessionStorage.getItem(VERIFIER_KEY)
  const expected = sessionStorage.getItem(STATE_KEY)
  sessionStorage.removeItem(STATE_KEY)
  sessionStorage.removeItem(VERIFIER_KEY)
  if (!code || !state || state !== expected || !verifier) throw new Error('OIDC callback validation failed')
  const oidc = config()
  if (!oidc.tokenUrl || !oidc.clientId || !oidc.redirectUri) throw new Error('OIDC token exchange is not configured')
  const response = await fetch(oidc.tokenUrl, {
    method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({ grant_type: 'authorization_code', client_id: oidc.clientId,
      redirect_uri: oidc.redirectUri, code, code_verifier: verifier }),
  })
  if (!response.ok) throw new Error('OIDC token exchange failed')
  const payload = (await response.json()) as { access_token?: string }
  if (!payload.access_token) throw new Error('OIDC response did not contain an access token')
  setToken(payload.access_token)
}
