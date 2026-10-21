import { describe, expect, it } from 'vitest'
import { isUserFacingRole } from './roleVisibility'

describe('role catalog visibility', () => {
  it('hides internal service roles', () => {
    expect(isUserFacingRole({ key: 'service.aurinara', assignable: false })).toBe(false)
    expect(isUserFacingRole({ key: 'service.aurinara-edc', assignable: false })).toBe(false)
  })

  it('keeps human-facing and non-service built-in roles visible', () => {
    expect(isUserFacingRole({ key: 'aurinara.sdtm_programmer', assignable: true })).toBe(true)
    expect(isUserFacingRole({ key: 'registered_study_creator', assignable: false })).toBe(true)
  })
})
