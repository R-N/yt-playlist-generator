import { describe, it, expect } from 'vitest'
import { PRIMARY_NAV, isPrimaryTab } from './nav'

describe('primary navigation', () => {
  it('keeps core destinations reachable', () => {
    expect(PRIMARY_NAV.map((item) => item.value)).toEqual([
      'import', 'workspace', 'library', 'review', 'activity', 'settings',
    ])
    expect(isPrimaryTab('library')).toBe(true)
    expect(isPrimaryTab('activity')).toBe(true)
    expect(isPrimaryTab('settings')).toBe(true)
  })
})
