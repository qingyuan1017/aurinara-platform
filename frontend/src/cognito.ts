import {
  AuthenticationDetails,
  CognitoUser,
  CognitoUserPool,
  type CognitoUserSession,
} from 'amazon-cognito-identity-js'
import { clearToken, setToken } from './auth'

const region = import.meta.env.VITE_COGNITO_REGION?.trim()
const userPoolId = import.meta.env.VITE_COGNITO_USER_POOL_ID?.trim()
const clientId = import.meta.env.VITE_COGNITO_CLIENT_ID?.trim()

export function isCognitoConfigured(): boolean {
  return Boolean(region && userPoolId && clientId)
}

function pool(): CognitoUserPool {
  if (!userPoolId || !clientId) throw new Error('Cognito email sign-in is not configured')
  return new CognitoUserPool({ UserPoolId: userPoolId, ClientId: clientId })
}

function user(username: string): CognitoUser {
  return new CognitoUser({ Username: username, Pool: pool() })
}

function storeSession(session: CognitoUserSession): void {
  // ID tokens carry the app-client audience required by the platform API.
  setToken(session.getIdToken().getJwtToken())
}

export function signInWithPassword(email: string, password: string): Promise<void> {
  const cognitoUser = user(email)
  cognitoUser.setAuthenticationFlowType('USER_PASSWORD_AUTH')
  const details = new AuthenticationDetails({ Username: email, Password: password })
  return new Promise((resolve, reject) => {
    cognitoUser.authenticateUser(details, {
      onSuccess: (session) => { storeSession(session); resolve() },
      onFailure: (error) => reject(normalizeError(error)),
      newPasswordRequired: () => reject(new Error('A new password is required. Complete the password reset before signing in.')),
    })
  })
}

export function restoreCognitoSession(): Promise<boolean> {
  if (!isCognitoConfigured()) return Promise.resolve(false)
  const current = pool().getCurrentUser()
  if (!current) return Promise.resolve(false)
  return new Promise((resolve) => {
    current.getSession((error: Error | null, session: CognitoUserSession | null) => {
      if (error || !session?.isValid()) { clearToken(); resolve(false); return }
      storeSession(session)
      resolve(true)
    })
  })
}

export function signOutCognito(): void {
  if (isCognitoConfigured()) pool().getCurrentUser()?.signOut()
  clearToken()
}

function normalizeError(error: unknown): Error {
  if (error && typeof error === 'object' && 'message' in error && typeof error.message === 'string') return new Error(error.message)
  return new Error('Cognito authentication failed')
}
