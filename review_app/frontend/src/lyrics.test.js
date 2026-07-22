import { describe, it, expect } from 'vitest'
import { parseLyricLines, isSynced, activeLineIndex } from './lyrics'

describe('LRC parsing + active line', () => {
  const lrc = '[00:01.00]first\n[00:03.50]second\nplain\n[00:10.00]third'

  it('parses timed and untimed lines', () => {
    const lines = parseLyricLines(lrc)
    expect(lines.map((l) => l.timed)).toEqual([true, true, false, true])
    expect(lines[1].time).toBeCloseTo(3.5)
    expect(lines[2].text).toBe('plain')
  })

  it('isSynced reflects presence of stamps', () => {
    expect(isSynced(parseLyricLines(lrc))).toBe(true)
    expect(isSynced(parseLyricLines('just\nplain\ntext'))).toBe(false)
    expect(parseLyricLines('')).toEqual([])
  })

  it('activeLineIndex returns the last stamp <= t, skipping untimed lines', () => {
    const lines = parseLyricLines(lrc)
    expect(activeLineIndex(lines, 0)).toBe(-1)     // before first stamp
    expect(activeLineIndex(lines, 2)).toBe(0)
    expect(activeLineIndex(lines, 4)).toBe(1)      // untimed 'plain' never becomes active
    expect(activeLineIndex(lines, 99)).toBe(3)
  })
})
