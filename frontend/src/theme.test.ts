import { describe, expect, it } from 'vitest'
import { resolveTheme } from './theme'

describe('theme resolution', () => {
  it('keeps explicit modes and resolves system preference', () => {
    expect(resolveTheme('light', true)).toBe('light')
    expect(resolveTheme('dark', false)).toBe('dark')
    expect(resolveTheme('system', false)).toBe('light')
    expect(resolveTheme('system', true)).toBe('dark')
  })
})
