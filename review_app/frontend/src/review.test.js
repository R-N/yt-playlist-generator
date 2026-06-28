import { describe, it, expect } from 'vitest'
import { keyToAction, fmt, advanceIndex, prevIndex, youtubeEmbed, confidenceColor } from './review'

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
    expect(youtubeEmbed('abc123')).toBe('https://www.youtube.com/embed/abc123')
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
