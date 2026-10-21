import type { User } from './types'

export function isUserFacingUser(user: Pick<User, 'display_name' | 'email'>): boolean {
  const name = user.display_name?.trim().toLowerCase() || ''
  return Boolean(user.email) || !name.endsWith(' service')
}
