import { describe, it, expect } from 'vitest'
import { keyToAction, fmt, advanceIndex, prevIndex, youtubeEmbed, confidenceColor,
  stateMeta, filterLibrary, filterEntries } from './review'

describe('keyToAction', () => {
  it('maps approve keys', () => {
    expect(keyToAction('a')).toBe('approve')
    expect(keyToAction('ArrowRight')).toBe('approve')
  })
  it('maps reject keys', () => {
    expect(keyToAction('r')).toBe('reject')
    expect(keyToAction('ArrowLeft')).toBe('reject')
  })
  it('maps back, ignores others', () => {
    expect(keyToAction('ArrowUp')).toBe('back')
    expect(keyToAction('x')).toBeNull()
    expect(keyToAction('Enter')).toBeNull()
  })
})

describe('fmt', () => {
  it('dashes blanks, formats numbers', () => {
    expect(fmt(null)).toBe('–')
    expect(fmt('')).toBe('–')
    expect(fmt(12.345, 1)).toBe('12.3')
    expect(fmt(7)).toBe('7')
  })
})

describe('advanceIndex', () => {
  it('advances inside the batch', () => {
    expect(advanceIndex(0, 5)).toEqual({ idx: 1, reload: false })
  })
  it('signals reload at the end', () => {
    expect(advanceIndex(4, 5)).toEqual({ idx: 4, reload: true })
  })
})

describe('prevIndex', () => {
  it('clamps at zero', () => {
    expect(prevIndex(3)).toBe(2)
    expect(prevIndex(0)).toBe(0)
  })
})

describe('youtubeEmbed', () => {
  it('builds embed url or null', () => {
    expect(youtubeEmbed('abc123')).toBe('https://www.youtube-nocookie.com/embed/abc123?rel=0')
    expect(youtubeEmbed('')).toBeNull()
    expect(youtubeEmbed(null)).toBeNull()
  })
})

describe('confidenceColor', () => {
  it('maps confidence to a color, defaults to grey', () => {
    expect(confidenceColor('strong')).toBe('green')
    expect(confidenceColor('weak')).toBe('amber')
    expect(confidenceColor('none')).toBe('grey')
    expect(confidenceColor(undefined)).toBe('grey')
  })
})

describe('stateMeta', () => {
  it('maps known states, falls back for unknown', () => {
    expect(stateMeta('confirmed').color).toBe('success')
    expect(stateMeta('link_only').label).toBe('Link only')
    expect(stateMeta('???').color).toBe('grey')
  })
})

describe('filterEntries (merged Library list)', () => {
  const entries = [
    { kind: 'track', filterKey: 'confirmed', search: 'yoasobi idol' },
    { kind: 'track', filterKey: 'unreviewed', search: 'ado show' },
    { kind: 'saved', filterKey: 'saved', search: 'youtu.be/abc song' },
    { kind: 'file', filterKey: 'file:verified', search: 'track.mp3 music/track.mp3' },
    { kind: 'file', filterKey: 'file:unmatched', search: 'orphan.flac music/orphan.flac' },
  ]
  it("'all' shows only tracks", () => {
    expect(filterEntries(entries, 'all', '').every((e) => e.kind === 'track')).toBe(true)
    expect(filterEntries(entries, 'all', '')).toHaveLength(2)
  })
  it('a track state filters tracks by state', () => {
    expect(filterEntries(entries, 'confirmed', '')).toHaveLength(1)
  })
  it("'saved' and 'files' select their kind", () => {
    expect(filterEntries(entries, 'saved', '').map((e) => e.kind)).toEqual(['saved'])
    expect(filterEntries(entries, 'files', '')).toHaveLength(2)
  })
  it('file:<category> narrows to one category, query still applies', () => {
    expect(filterEntries(entries, 'file:verified', '')).toHaveLength(1)
    expect(filterEntries(entries, 'files', 'orphan')).toHaveLength(1)
  })
})

describe('filterLibrary', () => {
  const rows = [
    { state: 'confirmed', artist: 'YOASOBI', title: 'Idol', filename: 'idol.mp3', yt_title: '', yt_channel: '' },
    { state: 'unreviewed', artist: 'Ado', title: 'Usseewa', filename: 'ado.mp3', yt_title: 'Ado - Usseewa', yt_channel: 'Ado' },
    { state: 'file_only', artist: '', title: '', filename: 'mystery.mp3', yt_title: '', yt_channel: '' },
  ]
  it('all + empty query returns everything', () => {
    expect(filterLibrary(rows, 'all', '')).toHaveLength(3)
  })
  it('filters by state', () => {
    expect(filterLibrary(rows, 'confirmed', '').map(r => r.artist)).toEqual(['YOASOBI'])
  })
  it('searches across visible fields, case-insensitive', () => {
    expect(filterLibrary(rows, 'all', 'ado')).toHaveLength(1)
    expect(filterLibrary(rows, 'all', 'MYSTERY')).toHaveLength(1)
  })
  it('state and query combine (AND)', () => {
    expect(filterLibrary(rows, 'confirmed', 'ado')).toHaveLength(0)
  })
})
