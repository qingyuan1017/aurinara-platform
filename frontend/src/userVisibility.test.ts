import { describe, expect, it } from 'vitest'
import { isUserFacingUser } from './userVisibility'

describe('user directory visibility', () => {
  it('hides internal product service identities', () => {
    expect(isUserFacingUser({ display_name: 'aurinara service', email: null })).toBe(false)
    expect(isUserFacingUser({ display_name: 'aurinara-edc service', email: null })).toBe(false)
  })

  it('keeps human and other registered identities visible', () => {
    expect(isUserFacingUser({ display_name: 'Jason Zhang', email: 'jason@example.com' })).toBe(true)
    expect(isUserFacingUser({ display_name: 'External integration', email: null })).toBe(true)
  })
})
