import type { Role } from './types'

export function isUserFacingRole(role: Pick<Role, 'assignable' | 'key'>): boolean {
  return role.assignable || !role.key.startsWith('service.')
}
