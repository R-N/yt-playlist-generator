// Tiny shared app-nav store so the Library list can hand a track to the Review
// view and switch tabs, without prop-drilling through App.
import { ref, watch } from 'vue'

export const PRIMARY_NAV = [
  { value: 'import', icon: 'mdi-import', label: 'Import', subtitle: 'paste + Discord' },
  { value: 'workspace', icon: 'mdi-tray-full', label: 'Workspace', subtitle: 'YouTube list + batches' },
  { value: 'library', icon: 'mdi-music-box-multiple', label: 'Library', subtitle: 'tracks + saved links' },
  { value: 'review', icon: 'mdi-check-decagram', label: 'Review', subtitle: 'approve / reject by ear' },
  { value: 'activity', icon: 'mdi-history', label: 'Activity', subtitle: 'tasks + history' },
  { value: 'settings', icon: 'mdi-tune', label: 'Settings', subtitle: 'folders + maintenance', inSettingsSlot: true },
]

export function isPrimaryTab(value) { return PRIMARY_NAV.some((item) => item.value === value) }

// URL hash <-> active tab + optional focus state, so each screen has its own
// shareable path with working back/forward — no vue-router dependency.
//   #/library?ids=1,2,3   isolate those Library track ids
//   #/workspace?ids=1,2,3 isolate those Workspace item ids
//   #/review?track=5      jump straight into reviewing track 5
import { api } from './api'

const hasWindow = typeof window !== 'undefined'
const hashBody = () => (hasWindow ? window.location.hash.replace(/^#\/?/, '') : '')
const tabFromHash = () => hashBody().split('?')[0]
const paramsFromHash = () => new URLSearchParams(hashBody().split('?')[1] || '')
function parseIds(param) {
  if (!param) return null
  const ids = param.split(',').map(Number).filter(Number.isFinite)
  return ids.length ? ids : null
}

export const activeTab = ref(isPrimaryTab(tabFromHash()) ? tabFromHash() : 'workspace')
export const focusTrack = ref(null)   // a full track row to review right now, or null
export const dataRevision = ref(0)

// Isolate specific track ids in the Library list (used by Workspace "Show in library").
export const libraryFocusIds = ref(activeTab.value === 'library' ? parseIds(paramsFromHash().get('ids')) : null)
// Isolate specific Workspace item ids (mirror of libraryFocusIds, for "Show in Workspace").
export const workspaceFocusIds = ref(activeTab.value === 'workspace' ? parseIds(paramsFromHash().get('ids')) : null)

function hashForState() {
  const params = new URLSearchParams()
  if (activeTab.value === 'library' && libraryFocusIds.value?.length) params.set('ids', libraryFocusIds.value.join(','))
  else if (activeTab.value === 'workspace' && workspaceFocusIds.value?.length) params.set('ids', workspaceFocusIds.value.join(','))
  else if (activeTab.value === 'review' && focusTrack.value?.id != null) params.set('track', focusTrack.value.id)
  const qs = params.toString()
  return qs ? `/${activeTab.value}?${qs}` : `/${activeTab.value}`
}

// Re-derive tab + focus state from the hash — used on load and on every
// hashchange (including Back/Forward), so history travel undoes a focus jump.
function applyHash() {
  const tab = tabFromHash()
  if (!isPrimaryTab(tab)) return
  const params = paramsFromHash()
  if (tab !== activeTab.value) activeTab.value = tab
  if (tab === 'library') libraryFocusIds.value = parseIds(params.get('ids'))
  if (tab === 'workspace') workspaceFocusIds.value = parseIds(params.get('ids'))
  if (tab === 'review') {
    const trackId = params.get('track')
    if (!trackId) focusTrack.value = null
    else if (String(focusTrack.value?.id) !== trackId) {
      api.track(Number(trackId)).then((track) => { focusTrack.value = track }).catch(() => { focusTrack.value = null })
    }
  }
}

if (hasWindow) {
  if (window.location.hash.replace(/^#/, '') !== hashForState()) window.location.hash = hashForState()
  watch([activeTab, libraryFocusIds, workspaceFocusIds, focusTrack], () => {
    const next = hashForState()
    if (window.location.hash.replace(/^#/, '') !== next) window.location.hash = next
  })
  window.addEventListener('hashchange', applyHash)
}

export function invalidateData() { dataRevision.value += 1 }

// A screen reloads when the user (re)enters its tab. That alone keeps every
// inactive tab fresh, since only the active tab is interacted with. We deliberately
// do NOT reload the active tab on every invalidateData() bump: an action on the
// active tab already refreshes it (an explicit reload, an in-place patch, or a light
// targeted refetch), so a revision-driven reload would just be a second, full,
// whole-list reload on top — the churn we want to avoid. invalidateData() remains a
// cheap "peers are stale" marker; peers act on it when re-entered.
export function useTabRefresh(tabName, load) {
  watch(activeTab, (tab, previous) => { if (tab === tabName && tab !== previous) load() })
}

export function reviewTrack(track) {
  focusTrack.value = track
  activeTab.value = 'review'
}

export function showInLibrary(ids) {
  libraryFocusIds.value = ids && ids.length ? ids : null
  activeTab.value = 'library'
}

export function showInWorkspace(ids) {
  workspaceFocusIds.value = ids && ids.length ? ids : null
  activeTab.value = 'workspace'
}
