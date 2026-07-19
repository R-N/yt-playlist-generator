import { describe, it, expect, vi, beforeEach } from 'vitest'
import { ref, nextTick } from 'vue'

// Mock the network layer so the shared dispatchers can be exercised as pure logic.
vi.mock('./api', () => ({
  api: {
    unreviewTrack: vi.fn().mockResolvedValue({}),
    reveal: vi.fn().mockResolvedValue({}),
    ytAudioUrl: (id) => `yt:${id}`,
    downloadAudioUrl: (id) => `dl:${id}`,
  },
}))
import { api } from './api'
import {
  useLabelFilter, usePagination, useSelection, ytUrl, fileMenuItems,
  usePreview, useRowActions,
  ytMenuItems, libraryLabelMenu, workspaceLabelMenu,
} from './curation'

const actions = (items) => items.map((i) => i.action)

describe('label menu builders', () => {
  it('ytMenuItems adds a Copy ID row carrying the id, only when an id exists', () => {
    expect(ytMenuItems('dQw4w9WgXcQ')).toContainEqual(
      expect.objectContaining({ action: 'copyid', title: 'Copy ID · dQw4w9WgXcQ' }))
    expect(actions(ytMenuItems(null))).not.toContain('copyid')
  })
  it('libraryLabelMenu hides Send-to-workspace when already in workspace and Show-in-library on the library screen', () => {
    expect(actions(libraryLabelMenu({ trackId: 5, onScreen: 'workspace', inWorkspace: false })))
      .toEqual(['info', 'send-workspace', 'review', 'show-library', 'remove-library'])
    expect(actions(libraryLabelMenu({ trackId: 5, onScreen: 'library', inWorkspace: true })))
      .toEqual(['info', 'review', 'remove-library'])   // send-workspace + show-library both dropped
  })
  it('workspaceLabelMenu hides Save-to-library when already in library and Show-in-workspace on the workspace screen', () => {
    expect(actions(workspaceLabelMenu({ onScreen: 'library', inLibrary: false })))
      .toEqual(['info', 'save-library', 'show-workspace', 'remove-workspace'])
    expect(actions(workspaceLabelMenu({ onScreen: 'workspace', inLibrary: true })))
      .toEqual(['info', 'remove-workspace'])
  })
})

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

describe('usePreview', () => {
  it('toggles per (key, mode) and resolves media off the row; key may contain "|"', () => {
    const { toggle, previewFor } = usePreview()
    const row = { key: 'fld|path', ytId: 'v', fileSrc: 'local.mp3', downloadSrc: 'd.mp3' }
    expect(previewFor(row)).toBeNull()
    toggle(row, 'local')                                    // key has a '|' -> split on last one
    expect(previewFor(row)).toEqual({ mode: 'audio', src: 'local.mp3' })
    toggle(row, 'embed')                                    // switch mode
    expect(previewFor(row)).toEqual({ mode: 'embed', id: 'v' })
    toggle(row, 'embed')                                    // same -> close
    expect(previewFor(row)).toBeNull()
    toggle(row, 'ytaudio')
    expect(previewFor(row)).toEqual({ mode: 'audio', src: 'yt:v' })
  })
  it('matches a numeric row.key (Workspace uses item.id) not just strings', () => {
    const { toggle, previewFor } = usePreview()
    const row = { key: 123, ytId: 'v' }               // number, as Workspace feeds it
    toggle(row, 'embed')
    expect(previewFor(row)).toEqual({ mode: 'embed', id: 'v' })
  })
})

describe('useRowActions dispatchers (fixed once, used by every screen)', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('statusAction: unreview clears the mark via setCheck; rereview routes to openReview', async () => {
    const openReview = vi.fn()
    const { statusAction } = useRowActions({ openReview })
    const setCheck = vi.fn()
    await statusAction('unreview', { trackId: 7, setCheck })
    expect(api.unreviewTrack).toHaveBeenCalledWith(7)
    expect(setCheck).toHaveBeenCalledWith(null)
    const row = { trackId: 7 }
    await statusAction('rereview', row)
    expect(openReview).toHaveBeenCalledWith(row)
  })

  it('statusAction: no track id -> no-op', async () => {
    const { statusAction } = useRowActions({})
    await statusAction('unreview', { trackId: null, setCheck: vi.fn() })
    expect(api.unreviewTrack).not.toHaveBeenCalled()
  })

  it('fileAction: reveal uses row.revealArg(source); delete is injected per screen', () => {
    const deleteFile = vi.fn()
    const { fileAction, fileInfo } = useRowActions({ deleteFile })
    const row = { key: 'k', revealArg: (s) => ({ src: s }), infoFor: (s) => ({ title: s, lines: [] }) }
    fileAction('reveal', row, 'download')
    expect(api.reveal).toHaveBeenCalledWith({ src: 'download' })
    fileAction('info', row, 'local')
    expect(fileInfo.value).toEqual({ title: 'local', lines: [] })
    fileAction('delete', row, 'download')
    expect(deleteFile).toHaveBeenCalledWith(row, 'download')
  })
})
