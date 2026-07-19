<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { api } from './api'
import {
  selectedItems, fileDownload, preferredRun, runDismissed, batchSnapshotIds, ACTIVE_RUN_STATUSES,
  duplicateMessage, skippedDuplicateCount,
  itemMeta, isDead, isEnriched, healthLabel, itemTitle, itemName, itemAuthor, formatViews, formatDuration, formatUploadDate,
} from './workspace'
import { buildLabels, FILTER_ATTRS, matchesLabelFilter } from './labels'
import { useLabelFilter, usePagination, useSelection, useRowActions, useFilePicker, useMembershipActions, useSearchPicker, useForceSet, ytUrl, ytMenuItems, YT_MENU_ITEMS, STATUS_MENU_ITEMS, workspaceLabelMenu, libraryLabelMenu, fileMenuItems, downloadMenuItems, untrackedMenuItems } from './curation'
import CurationList from './CurationList.vue'
import LabelFilterMenu from './LabelFilterMenu.vue'
import ActionMenu from './ActionMenu.vue'
import InfoDialog from './InfoDialog.vue'
import ConfirmDialog from './ConfirmDialog.vue'
import SearchPickerDialog from './SearchPickerDialog.vue'
import ForceSetDialog from './ForceSetDialog.vue'
import InfoEditDialog from './InfoEditDialog.vue'
import VerifyScopeDialog from './VerifyScopeDialog.vue'

// Workspace items can't be saved-links or untracked files — hide those attrs.
const WS_FILTER_ATTRS = FILTER_ATTRS.filter((attr) => !['saved', 'untracked'].includes(attr.key))
import { activeTab, invalidateData, reviewTrack, showInLibrary, workspaceFocusIds, useTabRefresh } from './nav'

const items = ref([])
const batches = ref([])
const dialog = ref(false)
const loading = ref(false)
const working = ref('')
const verifying = ref(false)
const verifyDialog = ref(false)
const startingVerify = ref(false)
const error = ref('')
const notice = ref('')
const run = ref(null)
const batchIds = ref([])
const batchDuplicates = ref(0)
const exportDuplicates = ref(0)
const runDuplicates = ref(0)
let poll = null

const query = ref('')
const { labelFilter, activeFilterCount, cycleFilter } = useLabelFilter()
const sortBy = ref('added')       // added | title | views
const finding = ref('')
const ytMenu = ref({ open: false, target: [0, 0], row: null, items: YT_MENU_ITEMS })
const fileMenu = ref({ open: false, target: [0, 0], row: null, items: [] })
const downloadMenu = ref({ open: false, target: [0, 0], row: null, items: [] })
const untrackedMenu = ref({ open: false, target: [0, 0], row: null, items: [] })
const statusMenu = ref({ open: false, target: [0, 0], row: null })
const memberMenu = ref({ open: false, target: [0, 0], label: null, items: [] })
const libRemoveConfirm = ref(null)   // { trackId } pending confirm (removing from Library is destructive)
// Shared action logic (curation.js). Rows carry the entity-specific data below.
const { preview, fileInfo, ytAction, fileAction, statusAction } = useRowActions({
  onError: (e) => { error.value = String(e) },
  onNotice: (m) => { notice.value = m },
  openReview: async (row) => { try { reviewTrack(await api.track(row.trackId)) } catch (e) { error.value = String(e) } },
})
// Membership-label actions (In Library / In Workspace). Remove-from-library confirms;
// remove-from-workspace is the item's own removal (non-destructive to files).
const membership = useMembershipActions({
  onError: (e) => { error.value = String(e) },
  reload: load,
  removeLibrary: (trackId) => { libRemoveConfirm.value = { trackId } },
  removeWorkspace: (itemId) => removeItems([itemId]),
})
// Interactive "Find on YouTube" / "Find local file" pickers (see SearchPickerDialog).
const { picker, openYoutube, openLocal, onPick } = useSearchPicker({
  onError: (e) => { error.value = String(e) }, reload: load,
})
// Force-set: "Set YouTube link…" / "Pick local file…" (verify + confirm w/ score).
const { fset, openSetYoutube, pickLocalFile, apply: applyForceSet, setValue: setForceValue } = useForceSet({
  onError: (e) => { error.value = String(e) }, reload: load,
})
const entityFor = (item) => ({ kind: 'workspace', id: item.id, artist: itemAuthor(item), title: itemName(item) })
const info = ref({ open: false, title: '', data: {}, editable: [] })
function openInfo(row) { info.value = { open: true, title: itemTitle(row.raw), data: row.raw, editable: ['title', 'channel'] } }
async function saveInfo(fields) {
  try { await api.workspacePatch(info.value.data.id, fields); info.value.open = false; invalidateData(); await load() }
  catch (e) { error.value = String(e) }
}

const liveItems = computed(() => items.value.filter((item) => !isDead(item)))
const { selected, allSelected, toggle, toggleAll } = useSelection(computed(() => liveItems.value.map((item) => item.id)))
const chosen = computed(() => selectedItems(items.value, selected.value))
const busy = computed(() => !chosen.value.length || !!working.value)
const displayItems = computed(() => {
  if (workspaceFocusIds.value?.length) {          // "Show in Workspace" isolates these ids
    const ids = new Set(workspaceFocusIds.value)
    return items.value.filter((item) => ids.has(item.id))
  }
  const q = query.value.trim().toLowerCase()
  let list = items.value.filter((item) =>
    matchesLabelFilter(new Set(labelsFor(item).map((label) => label.key)), labelFilter.value) &&
    (!q || `${itemTitle(item)} ${item.youtube_id}`.toLowerCase().includes(q)))
  if (sortBy.value === 'title') list = [...list].sort((a, b) => itemTitle(a).localeCompare(itemTitle(b)))
  else if (sortBy.value === 'views') list = [...list].sort((a, b) => (itemMeta(b).view_count || 0) - (itemMeta(a).view_count || 0))
  return list
})
const { page, perPage, pageCount, paged: pagedItems } = usePagination(displayItems, [labelFilter, query, sortBy, workspaceFocusIds])
// Normalize the visible page into the shared CurationList row shape + the
// action contract useRowActions reads (ytUrl/ytId/trackId/setCheck/…).
const listRows = computed(() => pagedItems.value.map((item) => ({
  key: item.id, raw: item,
  selectable: !isDead(item), dead: isDead(item),
  title: itemName(item),
  subtitle: (itemAuthor(item) ? `${itemAuthor(item)} · ` : '') + subtitle(item),
  labels: labelsFor(item),
  ytUrl: ytUrl(item), ytId: item.youtube_id,
  trackId: item.track_id || null,
  setCheck: (v) => { item.track_check = v },
  embed: (source) => api.workspaceEmbed(item.id, source),
  fileSrc: wsLocalSrc(item),
  downloadSrc: item.is_download_file
    ? api.localAudioUrl(item.folder_identity, item.relative_path)
    : (item.youtube_id ? api.downloadAudioUrl(item.youtube_id) : null),
  revealArg: (source) => (source === 'download' && !item.is_download_file)
    ? { download_yt_id: item.youtube_id }
    : (item.relative_path
      ? { folder_identity: item.folder_identity, relative_path: item.relative_path }
      : { track_id: item.track_id }),
  infoFor: () => item.relative_path
    ? { title: 'File info', lines: [['File', item.relative_path.split(/[\\/]/).pop()], ['Path', item.relative_path]] }
    : { title: 'File info', lines: [['File', item.track_filename || '—'], ['Artist', item.track_artist || '—'], ['Title', item.track_title || '—'], ['Local files', String(item.local_count || 0)]] },
})))

// Which finished run the user dismissed, so it doesn't resurface every reload.
// Active runs always show; only a done/failed one can be dismissed.
const DISMISS_KEY = 'ws-dismissed-run'
function dismissRun() {
  if (run.value?.id != null) localStorage.setItem(DISMISS_KEY, String(run.value.id))
  run.value = null
}
async function load() {
  loading.value = true
  try {
    items.value = (await api.workspace()).items
    const runs = (await api.workspaceRuns()).runs
    const durableRun = preferredRun(runs)
    const dismissed = runDismissed(durableRun, localStorage.getItem(DISMISS_KEY))
    run.value = durableRun && !dismissed ? await api.workspaceRun(durableRun.id) : null
    maybeVerify()
  }
  catch (e) { error.value = String(e) }
  finally { loading.value = false }
}

function pruneSelected() {
  const dead = new Set(items.value.filter(isDead).map((item) => item.id))
  if (dead.size) selected.value = selected.value.filter((id) => !dead.has(id))
}
function maybeVerify() {
  if (!verifying.value && items.value.some((item) => !isEnriched(item))) verify(null)
}
// ids=null: enrich only un-checked items, looping until the server reports none
// remaining. ids=array: force a re-check of exactly those, in server-capped chunks.
async function verify(ids) {
  verifying.value = true; error.value = ''
  try {
    if (ids === null) {
      let response
      do {
        response = await api.workspaceEnrich(null, 40)
        items.value = response.items; pruneSelected()
      } while (response.remaining > 0 && activeTab.value === 'workspace')
    } else if (ids.length) {
      for (let i = 0; i < ids.length && activeTab.value === 'workspace'; i += 40) {
        const response = await api.workspaceEnrich(ids.slice(i, i + 40), 40)
        items.value = response.items; pruneSelected()
      }
    }
  } catch (e) { error.value = String(e) }
  finally { verifying.value = false }
}

// Manual verify = paced background task (see Activity). scope: 'all' | 'unverified'.
async function startVerify(scope) {
  startingVerify.value = true; error.value = ''
  try {
    await api.verifyWorkspaceTask(scope)
    verifyDialog.value = false
    activeTab.value = 'activity'
  } catch (e) { error.value = e.message?.includes('409') ? 'A verify task is already running (see Activity).' : String(e) }
  finally { startingVerify.value = false }
}

async function removeItems(ids) {
  if (!ids.length) return
  try { await api.workspaceRemove(ids); selected.value = selected.value.filter((id) => !ids.includes(id)); invalidateData(); await load() }
  catch (e) { error.value = String(e) }
}
// "Save to library" for one row or the whole selection — the server routes each item
// (file -> track, link -> saved link), so per-row and bulk are the exact same call.
async function saveToLibrary(ids = selected.value) {
  if (!ids.length) return
  await act('Saving', async () => { await api.workspaceSaveToLibrary(ids); invalidateData() })
  await load()
}
async function generatePlaylist() {
  await act('Generating playlist', async () => {
    const response = await api.workspacePlaylists(selected.value)
    batches.value = response.batches
    batchIds.value = batchSnapshotIds(response.batches)
    batchDuplicates.value = response.skipped_duplicate_item_ids?.length || 0
    dialog.value = true
  })
}
async function download(format) {
  await act('Preparing download', async () => {
    const response = await api.workspaceDownload(selected.value, format)
    exportDuplicates.value = response.skippedDuplicates
    fileDownload(response.blob, `workspace-${format}.${format === 'csv' ? 'csv' : 'txt'}`)
  })
}
async function downloadBatch(format) {
  await act('Preparing export', async () => {
    const response = await api.workspaceDownload(batchIds.value, format)
    exportDuplicates.value = response.skippedDuplicates
    fileDownload(response.blob, `workspace-${format}.${format === 'csv' ? 'csv' : 'txt'}`)
  })
}
async function startDownload() {
  await act('Starting audio download', async () => {
    run.value = await api.workspaceDownloadRun(selected.value)
    runDuplicates.value = skippedDuplicateCount(run.value)
    invalidateData()
  })
}
function labelsFor(item) {
  // A file ref in the download folder is a *download*, not an mp3-folder local/untracked
  // file (distinct — see CLAUDE.md). A direct file ref is "untracked" only when it has NO
  // Library track (a loose/staged file); once it's linked to a track (track_id) it's that
  // track's local file, never untracked. A catalog-linked file (local_count) is local too.
  const dlFile = !!item.is_download_file
  const directFile = !!item.relative_path && !dlFile
  return buildLabels({
    aliveLink: isEnriched(item) && !isDead(item), deadLink: isDead(item),
    localCount: item.local_count || (directFile ? 1 : 0),
    untracked: directFile && !item.track_id && !item.local_count,
    downloaded: !!item.downloaded, check: item.track_check,
    ytId: item.youtube_id, inLibrary: item.track_id || null, inWorkspace: item.id,
  })
}
function wsLocalSrc(item) {
  if (item.relative_path) return api.localAudioUrl(item.folder_identity, item.relative_path)
  return item.track_id ? api.audioUrl(item.track_id) : null
}
// A label click opens the matching shared menu; the normalized row carries the
// action data. unreview patches track_check locally (labelsFor recomputes),
// rereview routes through openReview above — both fixed once in useRowActions.
function onLabel(row, label, ev) {
  const target = [ev.clientX, ev.clientY]
  const item = row.raw
  if (label.key === 'youtube' || label.key === 'dead') ytMenu.value = { open: true, target, row, items: ytMenuItems(row.ytId, { canFindLocal: !(item.relative_path || item.local_count), canSetLocal: true }) }
  else if (label.key === 'local') fileMenu.value = { open: true, target, row, items: fileMenuItems({ canFindYoutube: !row.ytId, canSetYoutube: true, canEmbed: true }) }
  else if (label.key === 'untracked') untrackedMenu.value = { open: true, target, row, items: untrackedMenuItems({ canSend: false }) }
  else if (label.key === 'downloaded') downloadMenu.value = { open: true, target, row, items: downloadMenuItems() }
  else if (label.key === 'confirmed' || label.key === 'rejected') statusMenu.value = { open: true, target, row }
  else if (label.key === 'inlibrary') memberMenu.value = { open: true, target, label, row, items: libraryLabelMenu({ trackId: label.trackId, onScreen: 'workspace', inWorkspace: true }) }
  else if (label.key === 'inworkspace') memberMenu.value = { open: true, target, label, row, items: workspaceLabelMenu({ onScreen: 'workspace', inLibrary: !!row.trackId }) }
}
function onFileMenu(mode, row) {
  if (mode === 'find-youtube') openYoutube({ title: itemTitle(row.raw), artist: itemAuthor(row.raw), songTitle: itemName(row.raw), target: { kind: 'workspace', id: row.key } })
  else if (mode === 'set-youtube') openSetYoutube(entityFor(row.raw))
  else fileAction(mode, row)
}
const onDownloadMenu = (mode, row) => fileAction(mode, row, 'download')
function onYtMenu(mode, row) {
  if (mode === 'find-local') openLocal({ title: itemTitle(row.raw), query: itemName(row.raw), target: { kind: 'workspace', id: row.key } })
  else if (mode === 'pick-local') pickLocalFile(entityFor(row.raw))
  else ytAction(mode, row)
}
function onMemberMenu(mode) {
  if (mode === 'info') openInfo(memberMenu.value.row)
  else membership.run(mode, memberMenu.value.label)   // save-library routes to the unified endpoint
}
function onUntrackedMenu(mode, row) {
  if (mode === 'add') saveToLibrary([row.key])   // "Send to Workspace" is hidden here (already in Workspace)
}
// Both fire a paced background task over the selection (one active task at a time,
// tracked in Activity), auto-applying the best match — see tasks.py.
async function startFind(kind, fn) {
  finding.value = kind; error.value = ''
  try {
    await fn(selected.value.length ? selected.value : null)
    activeTab.value = 'activity'
  } catch (e) {
    error.value = e.message?.includes('409') ? 'A background task is already running (see Activity).' : String(e)
  } finally { finding.value = '' }
}
const findLocal = () => startFind('local', api.findLocalWorkspaceTask)
const findYoutube = () => startFind('youtube', api.findYoutubeWorkspaceTask)
// Add absolute-path files as Workspace items (tracked or untracked); shared picker.
const { picking, pickFiles } = useFilePicker({
  target: 'workspace',
  onDone: async () => { invalidateData(); await load() },
  onError: (e) => { error.value = String(e) },
})
async function act(label, fn) {
  working.value = label; error.value = ''
  try { await fn() } catch (e) { error.value = String(e) }
  finally { working.value = '' }
}
async function refreshRun() {
  if (!run.value?.id) return
  try {
    const wasActive = ACTIVE_RUN_STATUSES.includes(run.value.status)
    run.value = await api.workspaceRun(run.value.id)
    if (wasActive && !ACTIVE_RUN_STATUSES.includes(run.value.status)) invalidateData()
  }
  catch (e) { error.value = String(e) }
}
function subtitle(item) {
  if (!item.youtube_id) {
    if (item.relative_path) return `Local file · ${item.relative_path}`
    return item.track_id ? `Local track · no YouTube link · Library #${item.track_id}` : 'No YouTube link'
  }
  const parts = []
  if (isDead(item)) parts.push(healthLabel(item))
  else {
    const meta = itemMeta(item)
    const stats = [formatViews(meta.view_count), formatUploadDate(meta.upload_date), formatDuration(meta.duration)].filter(Boolean).join(' · ')
    if (stats) parts.push(stats)
  }
  parts.push('YouTube · ' + item.youtube_id)
  return parts.join(' · ')
}
async function copy(text) {
  try { await navigator.clipboard.writeText(text) }
  catch { error.value = 'Copy failed. Select and copy the text manually.' }
}
useTabRefresh('workspace', load)
onMounted(() => { load(); poll = setInterval(() => { if (run.value && ACTIVE_RUN_STATUSES.includes(run.value.status)) refreshRun() }, 1500) })
onUnmounted(() => clearInterval(poll))
</script>

<template>
  <div class="d-flex align-center mb-4">
    <div class="text-h4">Workspace</div>
    <v-progress-circular v-if="verifying" indeterminate size="18" width="2" class="ml-3" color="primary" />
    <v-spacer />
    <v-chip color="primary" variant="tonal">{{ items.length }} items · {{ selected.length }} selected</v-chip>
  </div>

  <v-alert v-if="run" variant="tonal" :type="run.status === 'done' ? 'success' : run.status === 'failed' ? 'error' : 'info'" class="mb-4" role="status"
    :closable="run.status === 'done' || run.status === 'failed'" @click:close="dismissRun">
    Audio download: <strong>{{ run.status }}</strong>. {{ run.error_text || `${run.items?.length || 0} snapshotted items` }}
  </v-alert>
  <v-alert v-if="batchDuplicates" type="warning" variant="tonal" class="mb-4">{{ duplicateMessage('Playlist', batchDuplicates) }}</v-alert>
  <v-alert v-if="exportDuplicates" type="warning" variant="tonal" class="mb-4">{{ duplicateMessage('Download', exportDuplicates) }}</v-alert>
  <v-alert v-if="runDuplicates" type="warning" variant="tonal" class="mb-4">{{ duplicateMessage('Audio download', runDuplicates) }}</v-alert>
  <v-alert v-if="error" type="error" closable class="mb-4" @click:close="error=''">{{ error }}</v-alert>
  <v-alert v-if="notice" type="success" variant="tonal" closable class="mb-4" @click:close="notice=''">{{ notice }}</v-alert>
  <v-alert v-if="workspaceFocusIds" type="info" variant="tonal" class="mb-3">
    <div class="d-flex align-center">Showing {{ workspaceFocusIds.length }} focused item(s).<v-spacer /><v-btn size="small" variant="text" prepend-icon="mdi-close" @click="workspaceFocusIds = null">Clear</v-btn></div>
  </v-alert>

  <v-toolbar density="comfortable" class="mb-3 px-2 rounded-lg" color="surface" border>
    <v-checkbox-btn :model-value="allSelected" aria-label="Select all live items" @update:model-value="toggleAll" />
    <v-btn icon variant="text" :loading="loading" aria-label="Refresh" @click="load">
      <v-icon>mdi-refresh</v-icon><v-tooltip activator="parent" location="bottom">Refresh list</v-tooltip>
    </v-btn>
    <v-btn icon variant="text" :loading="picking" aria-label="Add files" @click="pickFiles">
      <v-icon>mdi-file-plus-outline</v-icon><v-tooltip activator="parent" location="bottom">Add files from disk (absolute path)</v-tooltip>
    </v-btn>
    <v-btn icon variant="text" :loading="startingVerify" aria-label="Verify links" @click="verifyDialog = true">
      <v-icon>mdi-link-variant</v-icon><v-tooltip activator="parent" location="bottom">Verify links (background task)</v-tooltip>
    </v-btn>
    <v-btn icon variant="text" :loading="finding === 'local'" :disabled="busy" aria-label="Find local file" @click="findLocal">
      <v-icon>mdi-folder-search-outline</v-icon><v-tooltip activator="parent" location="bottom">Find local file — missing/moved, auto-link (background)</v-tooltip>
    </v-btn>
    <v-btn icon variant="text" :loading="finding === 'youtube'" :disabled="busy" aria-label="Find YouTube link" @click="findYoutube">
      <v-icon>mdi-link-variant-plus</v-icon><v-tooltip activator="parent" location="bottom">Find YouTube link — missing/dead, auto-apply best (background)</v-tooltip>
    </v-btn>
    <v-btn icon variant="text" :disabled="busy" aria-label="Generate Playlist" @click="generatePlaylist">
      <v-icon>mdi-playlist-play</v-icon><v-tooltip activator="parent" location="bottom">Generate Playlist</v-tooltip>
    </v-btn>
    <v-btn icon variant="text" :disabled="busy" aria-label="Save to Library" @click="saveToLibrary()">
      <v-icon>mdi-bookmark-plus-outline</v-icon><v-tooltip activator="parent" location="bottom">Save to Library</v-tooltip>
    </v-btn>
    <v-btn icon variant="text" :disabled="!chosen.some((item) => item.track_id)" aria-label="Show in library" @click="showInLibrary(chosen.map((item) => item.track_id).filter(Boolean))">
      <v-icon>mdi-library</v-icon><v-tooltip activator="parent" location="bottom">Show selected in Library</v-tooltip>
    </v-btn>
    <v-menu>
      <template #activator="{ props }">
        <v-btn icon variant="text" :disabled="busy" aria-label="Export" v-bind="props">
          <v-icon>mdi-file-export-outline</v-icon><v-tooltip activator="parent" location="bottom">Export</v-tooltip>
        </v-btn>
      </template>
      <v-list density="compact">
        <v-list-item prepend-icon="mdi-identifier" title="Download IDs" @click="download('ids')" />
        <v-list-item prepend-icon="mdi-link-variant" title="Download URLs" @click="download('urls')" />
        <v-list-item prepend-icon="mdi-file-delimited-outline" title="Download CSV" @click="download('csv')" />
        <v-list-item prepend-icon="mdi-playlist-play" title="Download playlist links" @click="download('playlist-links')" />
      </v-list>
    </v-menu>
    <v-btn icon variant="text" color="primary" :loading="working === 'Starting audio download'" :disabled="busy" aria-label="Download audio" @click="startDownload">
      <v-icon>mdi-download</v-icon><v-tooltip activator="parent" location="bottom">Download audio</v-tooltip>
    </v-btn>
    <v-btn icon variant="text" color="error" :disabled="busy" aria-label="Remove" @click="removeItems(selected)">
      <v-icon>mdi-delete-outline</v-icon><v-tooltip activator="parent" location="bottom">Remove</v-tooltip>
    </v-btn>
    <v-text-field v-model="query" placeholder="Search title, channel, ID…" density="compact" variant="solo-filled" flat hide-details clearable prepend-inner-icon="mdi-magnify" class="mx-2" style="flex:1 1 auto" />
    <LabelFilterMenu :attrs="WS_FILTER_ATTRS" :filter="labelFilter" :count="activeFilterCount" @cycle="cycleFilter" @clear="labelFilter = {}" />
    <v-menu>
      <template #activator="{ props }">
        <v-btn icon variant="text" aria-label="Sort" v-bind="props"><v-icon>mdi-sort</v-icon><v-tooltip activator="parent" location="bottom">Sort</v-tooltip></v-btn>
      </template>
      <v-list density="compact">
        <v-list-item v-for="opt in [{title:'Added',value:'added'},{title:'Title',value:'title'},{title:'Views',value:'views'}]" :key="opt.value" :active="sortBy===opt.value" :title="opt.title" @click="sortBy=opt.value" />
      </v-list>
    </v-menu>
  </v-toolbar>

  <CurationList
    :rows="listRows" :selected="selected" :preview-for="preview.previewFor"
    v-model:page="page" v-model:per-page="perPage" :page-count="pageCount" :has-items="items.length > 0"
    @toggle="(row) => toggle(row.key)" @label="onLabel">
    <template #badge="{ row }">
      <v-icon v-if="itemMeta(row.raw).verified" size="12" class="ml-1 text-medium-emphasis" title="Verified channel">mdi-check-decagram</v-icon>
    </template>
    <template #empty>
      <v-empty-state icon="mdi-youtube-play" title="Workspace is empty" text="Open Import to add YouTube IDs, or send exact tracks from Library." action-text="Open Import" @click:action="activeTab = 'import'" />
    </template>
  </CurationList>

  <ActionMenu v-model="ytMenu.open" :target="ytMenu.target" :items="ytMenu.items" @select="(mode) => { onYtMenu(mode, ytMenu.row); ytMenu.open = false }" />
  <ActionMenu v-model="fileMenu.open" :target="fileMenu.target" :items="fileMenu.items" @select="(mode) => { onFileMenu(mode, fileMenu.row); fileMenu.open = false }" />
  <ActionMenu v-model="downloadMenu.open" :target="downloadMenu.target" :items="downloadMenu.items" @select="(mode) => { onDownloadMenu(mode, downloadMenu.row); downloadMenu.open = false }" />
  <ActionMenu v-model="untrackedMenu.open" :target="untrackedMenu.target" :items="untrackedMenu.items" @select="(mode) => { onUntrackedMenu(mode, untrackedMenu.row); untrackedMenu.open = false }" />
  <ActionMenu v-model="statusMenu.open" :target="statusMenu.target" :items="STATUS_MENU_ITEMS" @select="(mode) => { statusAction(mode, statusMenu.row); statusMenu.open = false }" />
  <ActionMenu v-model="memberMenu.open" :target="memberMenu.target" :items="memberMenu.items" @select="(mode) => { onMemberMenu(mode); memberMenu.open = false }" />
  <SearchPickerDialog v-model="picker.open" :mode="picker.mode" :title="picker.title" :initial-query="picker.query" :artist="picker.artist" :song-title="picker.songTitle" @pick="onPick" />
  <ForceSetDialog :state="fset" @update:open="fset.open = $event" @update:value="setForceValue" @apply="applyForceSet" />
  <InfoEditDialog v-model="info.open" :title="info.title" :data="info.data" :editable="info.editable" @save="saveInfo" />
  <InfoDialog v-model="fileInfo" />
  <VerifyScopeDialog v-model="verifyDialog" :busy="startingVerify" @pick="startVerify" />
  <ConfirmDialog :model-value="!!libRemoveConfirm" title="Remove from Library?" confirm-label="Remove" @update:model-value="(v) => { if (!v) libRemoveConfirm = null }" @confirm="async () => { const id = libRemoveConfirm.trackId; libRemoveConfirm = null; try { await api.libraryRemove([id]); invalidateData(); await load() } catch (e) { error.value = String(e) } }">
    Deletes the Library entry and any downloaded file(s). Your mp3-folder files are not touched. This item stays in the Workspace.
  </ConfirmDialog>

  <v-dialog v-model="dialog" max-width="760" scrollable>
    <v-card>
      <v-card-title class="d-flex align-center">
        YouTube Playlist
        <v-spacer />
        <v-btn icon="mdi-close" variant="text" size="small" aria-label="Close" @click="dialog = false" />
      </v-card-title>
      <v-divider />
      <v-card-text>
        <div class="d-flex align-center flex-wrap ga-1 mb-3">
          <span class="text-body-2 text-medium-emphasis">{{ batches.length }} playlist batch{{ batches.length === 1 ? '' : 'es' }}</span>
          <v-spacer />
          <v-btn icon variant="text" size="small" aria-label="Copy all" @click="copy(batches.map((batch) => batch.playlist_url).join('\n'))">
            <v-icon>mdi-content-copy</v-icon><v-tooltip activator="parent" location="bottom">Copy all</v-tooltip>
          </v-btn>
          <v-btn icon variant="text" size="small" aria-label="Export" @click="downloadBatch('playlist-links')">
            <v-icon>mdi-download</v-icon><v-tooltip activator="parent" location="bottom">Export</v-tooltip>
          </v-btn>
        </div>
        <v-card v-for="batch in batches" :key="batch.number" variant="tonal" class="pa-3 mb-3">
          <div class="d-flex align-center mb-2">
            <strong>Batch {{ batch.number }}</strong>
            <v-spacer />
            <v-chip size="small" variant="tonal">{{ batch.count }} / 50</v-chip>
          </div>
          <v-text-field :model-value="batch.playlist_url" readonly density="compact" variant="outlined" hide-details>
            <template #append-inner>
              <v-btn icon="mdi-content-copy" size="x-small" variant="text" aria-label="Copy" @click="copy(batch.playlist_url)" />
              <v-btn icon="mdi-open-in-new" size="x-small" variant="text" aria-label="Open" :href="batch.playlist_url" target="_blank" rel="noopener noreferrer" />
            </template>
          </v-text-field>
        </v-card>
      </v-card-text>
    </v-card>
  </v-dialog>
</template>

