import { describe, it, expect } from 'vitest'
import { chunkItems, selectedItems, formatBytes, activeRun, preferredRun, runDismissed, batchSnapshotIds, folderLines, deletionMessage, duplicateMessage, skippedDuplicateCount,
  itemMeta, isDead, isEnriched, itemTitle, healthLabel, formatViews, formatDuration, formatUploadDate,
  nameMatchScore } from './workspace'

describe('workspace helpers', () => {
  it('splits items into 50-item batches', () => {
    expect(chunkItems(Array.from({ length: 51 }, (_, id) => ({ id })))).toHaveLength(2)
    expect(chunkItems(Array.from({ length: 51 }, (_, id) => ({ id })))[0]).toHaveLength(50)
    expect(chunkItems([])).toEqual([])
  })

  it('scores title/basename token overlap for local-file discovery', () => {
    expect(nameMatchScore('YOASOBI - Idol', 'yoasobi_idol.mp3')).toBe(2)
    expect(nameMatchScore('Idol', 'yoasobi_idol.flac')).toBe(1)
    expect(nameMatchScore('Idol', 'totally_unrelated.mp3')).toBe(0)
    expect(nameMatchScore('', 'x.mp3')).toBe(0)
    expect(nameMatchScore('a b c', 'a-b-c.mp3')).toBe(0) // tokens <=2 chars dropped
  })

  it('keeps selected items in workspace order', () => {
    expect(selectedItems([{ id: 2 }, { id: 1 }, { id: 3 }], [3, 2]).map((x) => x.id)).toEqual([2, 3])
  })

  it('formats local file sizes compactly', () => {
    expect(formatBytes(0)).toBe('0 B')
    expect(formatBytes(1024 ** 2)).toBe('1.0 MB')
    expect(formatBytes(null)).toBe('—')
  })

  it('restores only durable active runs and preserves batch snapshot ids', () => {
    expect(activeRun([{ id: 1, status: 'done' }, { id: 2, status: 'running' }]).id).toBe(2)
    expect(activeRun([{ id: 1, status: 'failed' }])).toBeNull()
    expect(preferredRun([{ id: 1, status: 'done' }]).id).toBe(1)
    expect(batchSnapshotIds([{ item_ids: [4, 5] }, { item_ids: [9] }])).toEqual([4, 5, 9])
  })

  it('hides only the dismissed finished run, never an active one', () => {
    expect(runDismissed({ id: 7, status: 'done' }, '7')).toBe(true)
    expect(runDismissed({ id: 7, status: 'failed' }, 7)).toBe(true)   // id coerced
    expect(runDismissed({ id: 7, status: 'done' }, '9')).toBe(false)  // a newer run
    expect(runDismissed({ id: 7, status: 'running' }, '7')).toBe(false) // active always shows
    expect(runDismissed(null, '7')).toBe(false)
  })

  it('allows explicit empty folder input and reports partial deletion', () => {
    expect(folderLines('  \n')).toEqual([])
    expect(folderLines(' C:/Music \n D:/Audio ')).toEqual(['C:/Music', 'D:/Audio'])
    expect(deletionMessage({ status: 'partial', deleted: 2, rejected: 1 })).toContain('2 deleted')
    expect(duplicateMessage('Download', 2)).toBe('Download omitted 2 duplicate items.')
    expect(skippedDuplicateCount({ skipped_duplicate_item_ids: [3, 8] })).toBe(2)
    expect(skippedDuplicateCount({})).toBe(0)
  })
})

describe('workspace item metadata / health', () => {
  const dead = { youtube_id: 'aaaaaaaaaaa', metadata_json: '{"health":"private"}' }
  const live = { youtube_id: 'bbbbbbbbbbb', channel: 'YOASOBI', title: 'Idol',
    metadata_json: '{"health":"ok","view_count":1500000,"duration":195,"upload_date":"20230412","verified":true}' }
  const raw = { youtube_id: 'ccccccccccc' }   // pasted, not yet enriched

  it('parses metadata_json safely', () => {
    expect(itemMeta(live).view_count).toBe(1500000)
    expect(itemMeta(raw)).toEqual({})
    expect(itemMeta({ metadata_json: 'not json' })).toEqual({})
  })
  it('treats only dead/private as dead, not unknown', () => {
    expect(isDead(dead)).toBe(true)
    expect(isDead(live)).toBe(false)
    expect(isDead({ metadata_json: '{"health":"unknown"}' })).toBe(false)
  })
  it('knows enriched vs pasted-raw', () => {
    expect(isEnriched(live)).toBe(true)
    expect(isEnriched(raw)).toBe(false)
  })
  it('builds a library-style title, falls back for raw/dead', () => {
    expect(itemTitle(live)).toBe('YOASOBI – Idol')
    expect(itemTitle(raw)).toBe('ccccccccccc')
    expect(itemTitle(dead)).toBe('Unavailable video')
    expect(healthLabel(dead)).toBe('Private')
  })
  it('formats views, duration, upload date', () => {
    expect(formatViews(1500000)).toBe('1.5M views')
    expect(formatViews(950)).toBe('950 views')
    expect(formatViews(null)).toBe('')
    expect(formatDuration(195)).toBe('3:15')
    expect(formatDuration(3661)).toBe('1:01:01')
    expect(formatUploadDate('20230412')).toBe('2023-04-12')
  })
})
