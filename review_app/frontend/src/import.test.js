import { describe, expect, it } from 'vitest'
import { parsePaste } from './import'

describe('import parsing', () => {
  it('classifies valid URLs, duplicate IDs, and invalid input', () => {
    const rows = parsePaste('https://youtu.be/dQw4w9WgXcQ\ndQw4w9WgXcQ\nnope')
    expect(rows.map((row) => row.status)).toEqual(['valid', 'duplicate', 'invalid'])
    expect(rows[0].youtube_id).toBe('dQw4w9WgXcQ')
  })
})
