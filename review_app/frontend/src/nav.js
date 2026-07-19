// Tiny shared app-nav store so the Library list can hand a track to the Review
// view and switch tabs, without prop-drilling through App.
import { ref, watch } from 'vue'

export const PRIMARY_NAV = [
  { value: 'import', icon: 'mdi-import', label: 'Import', subtitle: 'paste + Discord' },
  { value: 'workspace', icon: 'mdi-tray-full', label: 'Workspace', subtitle: 'YouTube list + batches' },
  { value: 'library', icon: 'mdi-music-box-multiple', label: 'Library', subtitle: 'tracks + saved links' },
  { value: 'review', icon: 'mdi-check-decagram', label: 'Review', subtitle: 'approve / reject by ear' },
  { value: 'activity', icon: 'mdi-history', label: 'Activity', subtitle: 'tasks + history' },
  { value: 'settings', icon: 'mdi-tune', label: 'Settings', subtitle: 'folders + maintenance' },
]

export function isPrimaryTab(value) { return PRIMARY_NAV.some((item) => item.value === value) }

// URL hash <-> active tab, so each menu has its own path (#/library) with
// working back/forward and shareable links — no vue-router dependency.
const hasWindow = typeof window !== 'undefined'
const tabFromHash = () => (hasWindow ? window.location.hash.replace(/^#\/?/, '') : '')

export const activeTab = ref(isPrimaryTab(tabFromHash()) ? tabFromHash() : 'workspace')
export const focusTrack = ref(null)   // a full track row to review right now, or null
export const dataRevision = ref(0)

if (hasWindow) {
  if (tabFromHash() !== activeTab.value) window.location.hash = '/' + activeTab.value
  watch(activeTab, (value) => { if (tabFromHash() !== value) window.location.hash = '/' + value })
  window.addEventListener('hashchange', () => {
    const value = tabFromHash()
    if (isPrimaryTab(value) && value !== activeTab.value) activeTab.value = value
  })
}

export function invalidateData() { dataRevision.value += 1 }

// Every primary screen reloads on a data-revision bump and when the user
// (re)enters its tab. Wire both watches in one call so the tabs don't drift.
export function useTabRefresh(tabName, load) {
  watch(dataRevision, () => { if (activeTab.value === tabName) load() })
  watch(activeTab, (tab, previous) => { if (tab === tabName && tab !== previous) load() })
}

export function reviewTrack(track) {
  focusTrack.value = track
  activeTab.value = 'review'
}

// Isolate specific track ids in the Library list (used by Workspace "Show in library").
export const libraryFocusIds = ref(null)
export function showInLibrary(ids) {
  libraryFocusIds.value = ids && ids.length ? ids : null
  activeTab.value = 'library'
}

// Isolate specific Workspace item ids (mirror of showInLibrary, for "Show in Workspace").
export const workspaceFocusIds = ref(null)
export function showInWorkspace(ids) {
  workspaceFocusIds.value = ids && ids.length ? ids : null
  activeTab.value = 'workspace'
}
