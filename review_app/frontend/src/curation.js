// Shared list-view logic for the Workspace and Library screens, which are the
// same shape: a searchable, tri-state-label-filtered, paginated list of rows
// with the same context menus. Composables keep the reactive plumbing in one
// place; the MENU_ITEMS constants keep the menu definitions from drifting apart.
import { ref, computed, watch } from 'vue'
import { api } from './api'

// Tri-state label filter (see labels.js): tap cycles ignore -> must-have -> exclude.
export function useLabelFilter() {
  const labelFilter = ref({})
  const activeFilterCount = computed(() => Object.keys(labelFilter.value).length)
  function cycleFilter(key) {
    const state = labelFilter.value[key] || 0
    const next = { ...labelFilter.value }
    if (state === 0) next[key] = 1
    else if (state === 1) next[key] = -1
    else delete next[key]
    labelFilter.value = next
  }
  return { labelFilter, activeFilterCount, cycleFilter }
}

// Keyed multi-select shared by the list screens. `keys` (a ref/computed of the
// currently selectable ids/keys, optional) drives select-all: allSelected is true
// when every selectable key is picked; toggleAll unions or subtracts them,
// preserving any off-view selections. Omit keys for a plain toggle list (Import).
export function useSelection(keys = null) {
  const selected = ref([])
  const allKeys = () => (keys ? keys.value : [])
  const allSelected = computed(() => allKeys().length > 0 && allKeys().every((k) => selected.value.includes(k)))
  function toggle(k) {
    selected.value = selected.value.includes(k) ? selected.value.filter((x) => x !== k) : [...selected.value, k]
  }
  function toggleAll() {
    selected.value = allSelected.value
      ? selected.value.filter((k) => !allKeys().includes(k))
      : [...new Set([...selected.value, ...allKeys()])]
  }
  return { selected, allSelected, toggle, toggleAll }
}

// Pagination over a reactive source list. resetDeps snap back to page 1 (filter,
// query, sort changes); the clamp keeps `page` in range as the list shrinks.
export function usePagination(source, resetDeps = []) {
  const page = ref(1)
  const perPage = ref(50)
  const pageCount = computed(() => Math.max(1, Math.ceil(source.value.length / perPage.value)))
  const paged = computed(() => source.value.slice((page.value - 1) * perPage.value, page.value * perPage.value))
  if (resetDeps.length) watch(resetDeps, () => { page.value = 1 })
  watch(pageCount, (count) => { if (page.value > count) page.value = count })
  return { page, perPage, pageCount, paged }
}

// A watchable YouTube link for any row shape (workspace item or library entry).
export function ytUrl(row) {
  const id = row?.youtube_id || row?.yt_id
  return row?.youtube_url || (id ? `https://www.youtube.com/watch?v=${id}` : null)
}

// Context-menu item definitions, shared so Workspace and Library stay identical.
// Each tab renders these through ActionMenu and handles @select with its own
// dispatcher. FILE_MENU_ITEMS is a function because the delete row is optional
// (Library manages deletion; Workspace does not) and its label depends on source.
export const YT_MENU_ITEMS = [
  { action: 'open', icon: 'mdi-open-in-new', title: 'Open in new tab' },
  { action: 'copy', icon: 'mdi-content-copy', title: 'Copy link' },
  { action: 'embed', icon: 'mdi-youtube', title: 'Play embed' },
  { action: 'ytaudio', icon: 'mdi-music', title: 'Play audio' },
]

export const STATUS_MENU_ITEMS = [
  { action: 'unreview', icon: 'mdi-restore', title: 'Set unreviewed' },
  { action: 'rereview', icon: 'mdi-eye-refresh-outline', title: 'Re-review' },
]

// Untracked-file label menu, shared by Library and Import (untracked tab).
export const UNTRACKED_MENU_ITEMS = [
  { action: 'add', icon: 'mdi-plus-box', title: 'Add to Library' },
  { action: 'send', icon: 'mdi-send', title: 'Send to Workspace' },
]

export function fileMenuItems({ deletable = false, source = 'local' } = {}) {
  const items = [
    { action: 'play', icon: 'mdi-play', title: 'Play audio' },
    { action: 'info', icon: 'mdi-information-outline', title: 'File info' },
    { action: 'reveal', icon: 'mdi-folder-open-outline', title: 'Show in folder' },
  ]
  if (deletable) items.push({
    action: 'delete', icon: 'mdi-delete-outline', color: 'error',
    title: source === 'download' ? 'Delete downloaded file' : 'Delete local file',
  })
  return items
}

// Inline media-preview state shared by every list screen. Rows expose a stable
// `key`; the preview key is `${row.key}|${mode}`. Split on the LAST '|' so keys
// that themselves contain '|' (Import's folder|path fkey) still parse. Media
// sources are read off the row so previewFor never needs per-screen branches:
//   ytId -> embed / yt-audio,  fileSrc -> local audio,  downloadSrc -> download.
export function usePreview() {
  const previewKey = ref(null)
  function toggle(row, mode) {
    const k = `${row.key}|${mode}`
    previewKey.value = previewKey.value === k ? null : k
  }
  function previewFor(row) {
    if (!previewKey.value) return null
    const cut = previewKey.value.lastIndexOf('|')
    // Coerce: toggle() builds the key via a template literal (stringifies), but
    // row.key may be a number (Workspace uses item.id) — compare like-for-like.
    if (previewKey.value.slice(0, cut) !== String(row.key)) return null
    const mode = previewKey.value.slice(cut + 1)
    if (mode === 'embed') return { mode: 'embed', id: row.ytId }
    if (mode === 'ytaudio') return { mode: 'audio', src: row.ytId ? api.ytAudioUrl(row.ytId) : null }
    if (mode === 'local') return { mode: 'audio', src: row.fileSrc }
    if (mode === 'download') return { mode: 'audio', src: row.downloadSrc }
    return null
  }
  return { previewKey, toggle, previewFor }
}

// The one place each list action's LOGIC lives, so a fix lands everywhere at
// once. The three dispatchers switch on the menu action and read everything
// entity-specific off the row (ytUrl, trackId, setCheck, revealArg, infoFor,
// media srcs) — so Workspace / Library / Import differ only in how their
// row-normalizer fills those, never in what an action does. `deleteFile` is
// injected because delete is genuinely per-screen (Library's approved/download
// flows; Workspace/Import have none -> default no-op).
export function useRowActions({ onError = () => {}, openReview = () => {}, deleteFile = () => {} } = {}) {
  const preview = usePreview()
  const fileInfo = ref(null)   // bind to <InfoDialog v-model>

  function ytAction(mode, row) {
    if (!row) return
    if (mode === 'open') { if (row.ytUrl) window.open(row.ytUrl, '_blank', 'noopener') }
    else if (mode === 'copy') { if (row.ytUrl) navigator.clipboard?.writeText(row.ytUrl) }
    else if (mode === 'embed') preview.toggle(row, 'embed')
    else if (mode === 'ytaudio') preview.toggle(row, 'ytaudio')
  }
  function fileAction(mode, row, source = 'local') {
    if (!row) return
    if (mode === 'play') preview.toggle(row, source === 'download' ? 'download' : 'local')
    else if (mode === 'info') fileInfo.value = row.infoFor(source)
    else if (mode === 'reveal') api.reveal(row.revealArg(source)).catch(onError)
    else if (mode === 'delete') deleteFile(row, source)
  }
  async function statusAction(mode, row) {
    if (!row?.trackId) return
    if (mode === 'unreview') { try { await api.unreviewTrack(row.trackId); row.setCheck(null) } catch (e) { onError(e) } }
    else if (mode === 'rereview') openReview(row)
  }
  return { preview, fileInfo, ytAction, fileAction, statusAction }
}
