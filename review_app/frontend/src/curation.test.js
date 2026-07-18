import { describe, it, expect } from 'vitest'
import { ref, nextTick } from 'vue'
import { useLabelFilter, usePagination, useSelection, ytUrl, fileMenuItems } from './curation'

describe('useLabelFilter tri-state cycle', () => {
  it('cycles ignore -> must-have -> exclude -> ignore', () => {
    const { labelFilter, activeFilterCount, cycleFilter } = useLabelFilter()
    cycleFilter('local'); expect(labelFilter.value.local).toBe(1)
    cycleFilter('local'); expect(labelFilter.value.local).toBe(-1)
    expect(activeFilterCount.value).toBe(1)
    cycleFilter('local'); expect('local' in labelFilter.value).toBe(false)
    expect(activeFilterCount.value).toBe(0)
  })
})

describe('usePagination', () => {
  it('slices the source and clamps the page as it shrinks', async () => {
    const source = ref(Array.from({ length: 120 }, (_, i) => i))
    const dep = ref('a')
    const { page, perPage, pageCount, paged } = usePagination(source, [dep])
    expect(pageCount.value).toBe(3)
    expect(paged.value).toHaveLength(50)
    page.value = 3
    source.value = source.value.slice(0, 30)   // now one page
    await nextTick()
    expect(pageCount.value).toBe(1)
    expect(page.value).toBe(1)                  // clamped down
    perPage.value = 10; dep.value = 'b'         // dep change resets to page 1
    await nextTick()
    expect(page.value).toBe(1)
  })
})

describe('useSelection', () => {
  it('toggles keys and preserves off-view picks on select-all', () => {
    const keys = ref(['a', 'b'])            // 'c' is selected but not currently selectable
    const { selected, allSelected, toggle, toggleAll } = useSelection(keys)
    toggle('c'); toggle('a')
    expect(allSelected.value).toBe(false)
    toggleAll()                              // adds 'b'; keeps 'c'
    expect(selected.value.sort()).toEqual(['a', 'b', 'c'])
    expect(allSelected.value).toBe(true)
    toggleAll()                              // removes selectable a,b; keeps 'c'
    expect(selected.value).toEqual(['c'])
  })
  it('works as a plain toggle list with no keys', () => {
    const { selected, allSelected, toggle } = useSelection()
    toggle('x'); toggle('y'); toggle('x')
    expect(selected.value).toEqual(['y'])
    expect(allSelected.value).toBe(false)
  })
})

describe('ytUrl', () => {
  it('prefers explicit url, else builds from youtube_id or yt_id, else null', () => {
    expect(ytUrl({ youtube_url: 'http://x' })).toBe('http://x')
    expect(ytUrl({ youtube_id: 'abc' })).toBe('https://www.youtube.com/watch?v=abc')
    expect(ytUrl({ yt_id: 'def' })).toBe('https://www.youtube.com/watch?v=def')
    expect(ytUrl({})).toBeNull()
  })
})

describe('fileMenuItems', () => {
  it('adds a source-labelled delete row only when deletable', () => {
    expect(fileMenuItems().map((i) => i.action)).toEqual(['play', 'info', 'reveal'])
    const del = fileMenuItems({ deletable: true, source: 'download' }).at(-1)
    expect(del.action).toBe('delete')
    expect(del.title).toBe('Delete downloaded file')
  })
})
