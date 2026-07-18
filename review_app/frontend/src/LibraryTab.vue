<script setup>
import { ref, computed, onMounted } from 'vue'
import { api } from './api'
import { reviewTrack, activeTab, invalidateData, libraryFocusIds, useTabRefresh } from './nav'
import { deletionMessage, formatBytes } from './workspace'
import { buildLabels, FILTER_ATTRS, matchesLabelFilter } from './labels'
import { useLabelFilter, usePagination, useSelection, ytUrl, YT_MENU_ITEMS, STATUS_MENU_ITEMS, fileMenuItems } from './curation'
import LabelRow from './LabelRow.vue'
import LabelFilterMenu from './LabelFilterMenu.vue'
import ActionMenu from './ActionMenu.vue'
import MediaPreview from './MediaPreview.vue'
import InfoDialog from './InfoDialog.vue'
import TypedConfirmDialog from './TypedConfirmDialog.vue'
import ConfirmDialog from './ConfirmDialog.vue'

const UNTRACKED_MENU_ITEMS = [
  { action: 'add', icon: 'mdi-plus-box', title: 'Add to Library' },
  { action: 'send', icon: 'mdi-send', title: 'Send to Workspace' },
]

const rows = ref([])
const savedLinks = ref([])
const localFiles = ref([])
const { labelFilter, activeFilterCount, cycleFilter } = useLabelFilter()
const query = ref('')
const sortBy = ref(null)   // { key, order }
const loading = ref(false)
const opening = ref(null)
const matching = ref(null)
const addingFiles = ref(false)
const error = ref('')
const notice = ref('')
const deletePreview = ref(null)
const matchingLink = ref(null)
const matchFile = ref(null)
const audit = ref([])
const deleteBusy = ref(false)
const deleteOutcome = ref('')
const verifying = ref(false)
const removeConfirm = ref(null)   // { ids, count } pending confirmation
const removing = ref(false)
const previewKey = ref(null)      // `${item.key}|${mode}` currently previewing
const ytMenu = ref({ open: false, target: [0, 0], item: null })
const fileMenu = ref({ open: false, target: [0, 0], item: null, source: 'local' })
const fileInfo = ref(null)        // { title, lines }
const downloadDeleteConfirm = ref(null)
const statusMenu = ref({ open: false, target: [0, 0], item: null })
const untrackedMenu = ref({ open: false, target: [0, 0], item: null })

// Title falls back file name -> fetched YouTube title -> id; never blank.
function trackTitle(row) { return row.title || row.yt_title || row.filename || row.yt_id || '(untitled)' }
function trackAuthor(row) { return row.artist || row.yt_channel || '' }
function fileLabel(file) { return `${file.basename} · ${file.relative_path}` }

function attrsOf(labels, ...extra) { return new Set([...labels.map((label) => label.key), ...extra]) }

// One list, three row kinds. Matched saved links are hidden (their track already shows).
const entries = computed(() => {
  const tracks = rows.value.map((row) => {
    const labels = buildLabels({
      hasLink: !!row.yt_id, aliveLink: row.yt_health === 'ok',
      deadLink: row.yt_health === 'dead' || row.yt_health === 'private',
      localCount: row.has_local ? 1 : 0, downloaded: !!row.downloaded, check: row.check,
    })
    return {
      kind: 'track', key: `t${row.id}`, id: row.id, raw: row,
      title: trackTitle(row), author: trackAuthor(row),
      detail: row.has_local ? row.filename : '', labels, attrs: attrsOf(labels),
      search: `${trackTitle(row)} ${trackAuthor(row)} ${row.filename || ''} ${row.yt_id || ''} #${row.id} ${row.id}`.toLowerCase(),
    }
  })
  const saved = savedLinks.value.filter((link) => link.track_id == null).map((link) => {
    const labels = buildLabels({ hasLink: true })
    return {
      kind: 'saved', key: `s${link.id}`, raw: link,
      title: link.youtube_id || 'Saved YouTube link', author: '',
      detail: link.youtube_url, labels, attrs: attrsOf(labels, 'saved'),
      search: `${link.youtube_id || ''} ${link.youtube_url}`.toLowerCase(),
    }
  })
  const files = localFiles.value.filter((file) => !file.tracks?.length).map((file) => {
    const labels = buildLabels({ localCount: 1, untracked: true })
    return {
      kind: 'file', key: `f${file.folder_identity}-${file.relative_path}`, raw: file,
      title: file.basename, author: '',
      detail: `${file.relative_path} · ${formatBytes(file.file_size)}`,
      labels, attrs: attrsOf(labels, 'untracked'),
      search: `${file.basename} ${file.relative_path} ${file.category}`.toLowerCase(),
    }
  })
  return [...tracks, ...saved, ...files]
})
const filtered = computed(() => {
  if (libraryFocusIds.value?.length) {
    const ids = new Set(libraryFocusIds.value)
    return entries.value.filter((entry) => entry.kind === 'track' && ids.has(entry.id))
  }
  const q = query.value.trim().toLowerCase()
  const list = entries.value.filter((entry) => matchesLabelFilter(entry.attrs, labelFilter.value) && (!q || entry.search.includes(q)))
  if (!sortBy.value) return list
  const { key, order } = sortBy.value
  const dir = order === 'desc' ? -1 : 1
  return [...list].sort((a, b) => String(a[key] || '').localeCompare(String(b[key] || '')) * dir)
})
// Selection is keyed by entry.key so tracks and untracked files can both be picked.
const fileCount = computed(() => filtered.value.filter((entry) => entry.kind === 'file').length)
const { page, perPage, pageCount, paged } = usePagination(filtered, [labelFilter, query, sortBy, libraryFocusIds])
const selectableKeys = computed(() => filtered.value.filter((entry) => entry.kind !== 'saved').map((entry) => entry.key))
const { selected, allSelected: allVisible, toggle, toggleAll } = useSelection(selectableKeys)
const selectedEntries = computed(() => { const set = new Set(selected.value); return entries.value.filter((entry) => set.has(entry.key)) })
const selectedTrackRows = computed(() => selectedEntries.value.filter((entry) => entry.kind === 'track').map((entry) => entry.raw))
const selectedTrackIds = computed(() => selectedTrackRows.value.map((row) => row.id))
const selectedFileRefs = computed(() => selectedEntries.value.filter((entry) => entry.kind === 'file').map((entry) => ({ folder_identity: entry.raw.folder_identity, relative_path: entry.raw.relative_path })))
const deletable = computed(() => selectedTrackRows.value.filter((row) => row.has_local && row.check === 1))

async function load() {
  loading.value = true; error.value = ''
  try {
    const [library, links, files] = await Promise.all([api.library(), api.savedLinks(), api.localFiles()])
    rows.value = library.rows; savedLinks.value = links.links; localFiles.value = files.files
  } catch (e) { error.value = String(e) }
  finally { loading.value = false }
}
// ids=null: verify all not-yet-checked; ids=array: force re-check those. Loops on remaining.
async function verify(ids = null) {
  verifying.value = true; error.value = ''
  try {
    let res
    do {
      res = await api.libraryVerify(ids, 30)
      rows.value = res.rows
    } while (res.remaining > 0 && activeTab.value === 'library')
  } catch (e) { error.value = String(e) }
  finally { verifying.value = false }
}
function askRemove(ids) { if (ids.length) removeConfirm.value = { ids, count: ids.length } }
async function confirmRemove() {
  const ids = removeConfirm.value.ids
  removing.value = true; error.value = ''; notice.value = ''
  try {
    const r = await api.libraryRemove(ids)
    notice.value = `Removed ${r.removed} entr${r.removed === 1 ? 'y' : 'ies'} from Library.`
    const removedKeys = new Set(ids.map((i) => `t${i}`))
    selected.value = selected.value.filter((key) => !removedKeys.has(key))
    removeConfirm.value = null; invalidateData(); await load()
  } catch (e) { error.value = String(e); removeConfirm.value = null }
  finally { removing.value = false }
}
function entryYtId(e) { return e.kind === 'saved' ? e.raw.youtube_id : (e.raw?.yt_id || null) }
function localSrc(e) {
  if (e.kind === 'track') return api.audioUrl(e.id)
  if (e.kind === 'file') return api.localAudioUrl(e.raw.folder_identity, e.raw.relative_path)
  return null
}
function onLabel(item, label, ev) {
  const target = [ev.clientX, ev.clientY]
  if (label.key === 'youtube' || label.key === 'dead') ytMenu.value = { open: true, target, item }
  else if (label.key === 'local') fileMenu.value = { open: true, target, item, source: 'local' }
  else if (label.key === 'downloaded') fileMenu.value = { open: true, target, item, source: 'download' }
  else if (label.key === 'confirmed' || label.key === 'rejected') statusMenu.value = { open: true, target, item }
  else if (label.key === 'untracked') untrackedMenu.value = { open: true, target, item }
}
async function untrackedAction(mode) {
  const item = untrackedMenu.value.item; untrackedMenu.value.open = false
  if (!item) return
  if (mode === 'add') await addFileToLibrary(item.raw)
  else if (mode === 'send') await sendFileToWorkspace(item.raw)
}
async function sendFileToWorkspace(file) {
  matching.value = `f${file.folder_identity}-${file.relative_path}`; error.value = ''; notice.value = ''
  try {
    const r = await api.workspaceAddFiles([{ folder_identity: file.folder_identity, relative_path: file.relative_path }])
    const res = r.results[0]
    notice.value = res.added ? `Sent ${file.basename} to Workspace.` : `Not sent: ${res.reason}.`
    invalidateData(); await load()
  } catch (e) { error.value = String(e) }
  finally { matching.value = null }
}
async function statusAction(mode) {
  const item = statusMenu.value.item; statusMenu.value.open = false
  if (!item) return
  if (mode === 'unreview') { try { await api.unreviewTrack(item.id); invalidateData(); await load() } catch (e) { error.value = String(e) } }
  else if (mode === 'rereview') openReview(item.raw)
}
function setPreview(item, mode) { const key = `${item.key}|${mode}`; previewKey.value = previewKey.value === key ? null : key }
function previewFor(item) {
  if (!previewKey.value) return null
  const [key, mode] = previewKey.value.split('|')
  if (key !== item.key) return null
  if (mode === 'embed') return { mode: 'embed', id: entryYtId(item) }
  if (mode === 'ytaudio') { const id = entryYtId(item); return { mode: 'audio', src: id ? api.ytAudioUrl(id) : null } }
  if (mode === 'local') return { mode: 'audio', src: localSrc(item) }
  if (mode === 'download') { const id = entryYtId(item); return { mode: 'audio', src: id ? api.downloadAudioUrl(id) : null } }
  return null
}
function ytAction(mode) {
  const e = ytMenu.value.item; ytMenu.value.open = false
  if (!e) return
  if (mode === 'open') { const u = ytUrl(e.raw); if (u) window.open(u, '_blank', 'noopener') }
  else if (mode === 'copy') { const u = ytUrl(e.raw); if (u) navigator.clipboard?.writeText(u) }
  else if (mode === 'embed') setPreview(e, 'embed')
  else if (mode === 'ytaudio') setPreview(e, 'ytaudio')
}
const fileMenuList = computed(() => fileMenuItems({ deletable: true, source: fileMenu.value.source }))
function fileAction(mode) {
  const { item, source } = fileMenu.value; fileMenu.value.open = false
  if (!item) return
  if (mode === 'play') setPreview(item, source)
  else if (mode === 'info') showInfo(item, source)
  else if (mode === 'reveal') doReveal(item, source)
  else if (mode === 'delete') deleteFile(item, source)
}
async function doReveal(item, source) {
  try {
    if (source === 'download') await api.reveal({ download_yt_id: entryYtId(item) })
    else if (item.kind === 'track') await api.reveal({ track_id: item.id })
    else if (item.kind === 'file') await api.reveal({ folder_identity: item.raw.folder_identity, relative_path: item.raw.relative_path })
  } catch (e) { error.value = String(e) }
}
function showInfo(item, source) {
  const r = item.raw
  if (source === 'download') { fileInfo.value = { title: 'Downloaded file', lines: [['YouTube id', entryYtId(item)], ['Location', 'Download folder']] }; return }
  if (item.kind === 'file') fileInfo.value = { title: 'File info', lines: [['File', r.basename], ['Path', r.relative_path], ['Size', formatBytes(r.file_size)]] }
  else fileInfo.value = { title: 'File info', lines: [['File', r.filename], ['Artist', r.artist || '—'], ['Title', r.title || '—'], ['Duration', r.duration ? `${Math.round(r.duration)}s` : '—']] }
}
function deleteFile(item, source) {
  if (source === 'download') { downloadDeleteConfirm.value = { item }; return }
  if (item.kind === 'file') { error.value = 'Add this file to Library first to manage its deletion.'; return }
  previewDeleteOne(item)   // mp3-folder file: route through the approved-delete flow
}
async function previewDeleteOne(item) {
  try { deletePreview.value = await api.deletePreview([item.id]) }
  catch (e) { error.value = String(e) }
}
async function confirmDownloadDelete() {
  const item = downloadDeleteConfirm.value.item; downloadDeleteConfirm.value = null
  try { await api.downloadDelete([entryYtId(item)]); notice.value = 'Downloaded file deleted.'; invalidateData(); await load() }
  catch (e) { error.value = String(e) }
}
async function openReview(row) {
  opening.value = row.id
  try { reviewTrack(await api.track(row.id)) } catch (e) { error.value = String(e) }
  finally { opening.value = null }
}
async function addFileToLibrary(file) {
  matching.value = `f${file.folder_identity}-${file.relative_path}`
  error.value = ''; notice.value = ''
  try {
    const r = await api.addFilesToLibrary([{ folder_identity: file.folder_identity, relative_path: file.relative_path }])
    const res = r.results[0]
    notice.value = res.added ? `Added ${file.basename} to Library.` : `Not added: ${res.reason}.`
    invalidateData(); await load()
  } catch (e) { error.value = String(e) }
  finally { matching.value = null }
}
async function addAllUntracked() {
  const files = filtered.value.filter((e) => e.kind === 'file').map((e) => ({ folder_identity: e.raw.folder_identity, relative_path: e.raw.relative_path }))
  if (!files.length) return
  addingFiles.value = true; error.value = ''; notice.value = ''
  try {
    const r = await api.addFilesToLibrary(files)
    notice.value = `Added ${r.added} of ${files.length} files to Library.`
    invalidateData(); await load()
  } catch (e) { error.value = String(e) }
  finally { addingFiles.value = false }
}
async function sendToWorkspace() {
  try { await Promise.all(selectedTrackRows.value.map((row) => api.workspaceLibrary(row.id))); selected.value = []; invalidateData(); await load() }
  catch (e) { error.value = String(e) }
}
async function addSelectedFiles() {
  if (!selectedFileRefs.value.length) return
  addingFiles.value = true; error.value = ''; notice.value = ''
  try { const r = await api.addFilesToLibrary(selectedFileRefs.value); notice.value = `Added ${r.added} to Library.`; selected.value = []; invalidateData(); await load() }
  catch (e) { error.value = String(e) } finally { addingFiles.value = false }
}
async function sendSelectedFiles() {
  if (!selectedFileRefs.value.length) return
  addingFiles.value = true; error.value = ''; notice.value = ''
  try { const r = await api.workspaceAddFiles(selectedFileRefs.value); notice.value = `Sent ${r.added} to Workspace.`; selected.value = []; invalidateData(); await load() }
  catch (e) { error.value = String(e) } finally { addingFiles.value = false }
}
async function previewDelete() {
  if (!deletable.value.length) return
  try { deletePreview.value = await api.deletePreview(deletable.value.map((row) => row.id)) }
  catch (e) { error.value = String(e) }
}
async function confirmDelete() {
  if (!deletePreview.value) return
  try {
    deleteBusy.value = true
    const result = await api.deleteTracks({ track_ids: deletePreview.value.targets.map((target) => target.track_id), token: deletePreview.value.token, confirm: 'DELETE' })
    deleteOutcome.value = deletionMessage(result)
    invalidateData()
    deletePreview.value = null; audit.value = (await api.deleteAudit()).audit; await load()
  } catch (e) {
    deleteOutcome.value = 'Deletion did not complete. Targets were reloaded; review audit before retrying.'
    error.value = String(e)
    deletePreview.value = null
    try { audit.value = (await api.deleteAudit()).audit; await load() } catch (reloadError) { error.value += `; reload failed: ${reloadError}` }
  } finally { deleteBusy.value = false }
}
async function matchSavedLink() {
  if (!matchingLink.value || !matchFile.value?.tracks?.length) return
  const track = matchFile.value.tracks[0]
  try {
    await api.savedLinkMatch({ saved_link_id: matchingLink.value.id, track_id: track.track_id, folder_identity: matchFile.value.folder_identity, relative_path: matchFile.value.relative_path })
    matchingLink.value = null; matchFile.value = null; invalidateData(); await load()
  } catch (e) { error.value = String(e) }
}
function clearFocus() { libraryFocusIds.value = null }
onMounted(load)
useTabRefresh('library', load)
</script>

<template>
  <v-toolbar density="comfortable" class="mb-3 px-2 rounded-lg" color="surface" border>
    <v-checkbox-btn :model-value="allVisible" density="compact" aria-label="Select all visible" @update:model-value="toggleAll" />
    <span class="text-caption text-medium-emphasis mr-1">{{ selected.length ? `${selected.length} selected` : `${filtered.length} shown` }}</span>
    <v-btn icon variant="text" :loading="verifying" aria-label="Verify links" @click="verify(selectedTrackIds.length ? selectedTrackIds : null)">
      <v-icon>mdi-refresh</v-icon><v-tooltip activator="parent" location="bottom">Verify links{{ selectedTrackIds.length ? ` (${selectedTrackIds.length})` : ' (all)' }}</v-tooltip>
    </v-btn>
    <v-btn v-if="selectedTrackIds.length" icon color="primary" variant="text" aria-label="Send exact tracks to Workspace" @click="sendToWorkspace">
      <v-icon>mdi-send</v-icon><v-tooltip activator="parent" location="bottom">Send {{ selectedTrackIds.length }} exact tracks to Workspace</v-tooltip>
    </v-btn>
    <v-btn v-if="selectedFileRefs.length" icon color="primary" variant="text" :loading="addingFiles" aria-label="Add files to Library" @click="addSelectedFiles">
      <v-icon>mdi-plus-box-multiple</v-icon><v-tooltip activator="parent" location="bottom">Add {{ selectedFileRefs.length }} files to Library</v-tooltip>
    </v-btn>
    <v-btn v-if="selectedFileRefs.length" icon color="primary" variant="text" :loading="addingFiles" aria-label="Send files to Workspace" @click="sendSelectedFiles">
      <v-icon>mdi-folder-upload-outline</v-icon><v-tooltip activator="parent" location="bottom">Send {{ selectedFileRefs.length }} files to Workspace</v-tooltip>
    </v-btn>
    <v-btn v-if="deletable.length" icon color="error" variant="text" aria-label="Delete approved local files" @click="previewDelete">
      <v-icon>mdi-broom</v-icon><v-tooltip activator="parent" location="bottom">Delete {{ deletable.length }} approved local files (disk)</v-tooltip>
    </v-btn>
    <v-btn v-if="selectedTrackIds.length" icon color="error" variant="text" aria-label="Remove from Library" @click="askRemove(selectedTrackIds)">
      <v-icon>mdi-delete-outline</v-icon><v-tooltip activator="parent" location="bottom">Remove {{ selectedTrackIds.length }} from Library</v-tooltip>
    </v-btn>
    <v-btn v-if="fileCount && !selected.length" icon variant="text" color="primary" :loading="addingFiles" aria-label="Add all untracked to Library" @click="addAllUntracked"><v-icon>mdi-plus-box-multiple</v-icon><v-tooltip activator="parent" location="bottom">Add {{ fileCount }} to Library</v-tooltip></v-btn>
    <v-text-field v-model="query" placeholder="Search title, artist, file, #id…" density="compact" variant="solo-filled" flat hide-details clearable prepend-inner-icon="mdi-magnify" class="mx-2" style="flex:1 1 auto" />
    <LabelFilterMenu :attrs="FILTER_ATTRS" :filter="labelFilter" :count="activeFilterCount" @cycle="cycleFilter" @clear="labelFilter = {}" />
    <v-menu>
      <template #activator="{ props }">
        <v-btn icon variant="text" aria-label="Sort" v-bind="props"><v-icon>mdi-sort</v-icon><v-tooltip activator="parent" location="bottom">Sort</v-tooltip></v-btn>
      </template>
      <v-list density="compact">
        <v-list-item v-for="opt in [{title:'Title A–Z',key:'title',order:'asc'},{title:'Title Z–A',key:'title',order:'desc'},{title:'Author',key:'author',order:'asc'}]" :key="opt.title" :active="sortBy?.key===opt.key && sortBy?.order===opt.order" :title="opt.title" @click="sortBy={key:opt.key,order:opt.order}" />
      </v-list>
    </v-menu>
  </v-toolbar>
  <v-alert v-if="libraryFocusIds" type="info" variant="tonal" class="mb-3">
    <div class="d-flex align-center">Showing {{ libraryFocusIds.length }} track(s) from Workspace.<v-spacer /><v-btn size="small" variant="text" prepend-icon="mdi-close" @click="clearFocus">Clear</v-btn></div>
  </v-alert>
  <v-alert v-if="error" type="error" closable class="mb-3" @click:close="error=''">{{ error }}</v-alert>
  <v-alert v-if="notice" type="info" variant="tonal" closable class="mb-3" @click:close="notice=''">{{ notice }}</v-alert>
  <v-alert v-if="deleteOutcome" type="info" variant="tonal" closable class="mb-3" @click:close="deleteOutcome=''">{{ deleteOutcome }}</v-alert>

  <v-card variant="outlined">
    <div v-if="loading" class="pa-12 text-center"><v-progress-circular indeterminate color="primary" /></div>
    <template v-else>
      <v-list v-if="filtered.length" lines="two" density="compact">
        <template v-for="item in paged" :key="item.key">
          <v-list-item>
            <template #prepend>
              <v-checkbox-btn v-if="item.kind !== 'saved'" :model-value="selected.includes(item.key)" density="compact" :aria-label="`Select ${item.title}`" @update:model-value="toggle(item.key)" />
              <span v-else class="select-spacer" />
            </template>
            <v-list-item-title class="font-weight-medium">{{ item.title }}<span v-if="item.kind === 'track'" class="text-caption text-medium-emphasis ml-2">#{{ item.id }}</span></v-list-item-title>
            <v-list-item-subtitle>
              <span v-if="item.author">{{ item.author }}</span>
              <a v-else-if="item.kind === 'saved'" :href="item.detail" target="_blank" rel="noopener noreferrer">{{ item.detail }}</a>
              <span v-else class="text-medium-emphasis">{{ item.detail || '—' }}</span>
              <span v-if="item.author && item.detail" class="text-medium-emphasis"> · {{ item.detail }}</span>
            </v-list-item-subtitle>
            <template #append>
              <LabelRow :labels="item.labels" class="mx-1" @label-click="(label, ev) => onLabel(item, label, ev)" />
              <v-btn v-if="item.kind === 'saved'" size="small" variant="tonal" @click="matchingLink = item.raw; matchFile = null">Match local file</v-btn>
              <v-menu v-if="item.kind === 'track'">
                <template #activator="{ props }">
                  <v-btn icon="mdi-dots-vertical" size="small" variant="text" aria-label="Track actions" v-bind="props" />
                </template>
                <v-list density="compact" min-width="160">
                  <v-list-item prepend-icon="mdi-eye-check-outline" title="Review" @click="openReview(item.raw)" />
                  <v-list-item prepend-icon="mdi-delete-outline" title="Remove from Library" base-color="error" @click="askRemove([item.id])" />
                </v-list>
              </v-menu>
            </template>
          </v-list-item>
          <MediaPreview :preview="previewFor(item)" />
        </template>
      </v-list>
      <div v-else class="pa-6 text-medium-emphasis">Nothing matches this filter.</div>
      <div v-if="filtered.length" class="d-flex align-center justify-space-between px-3 py-2 border-t">
        <v-select v-model="perPage" :items="[25,50,100,200]" density="compact" variant="plain" hide-details style="max-width:88px" />
        <v-pagination v-model="page" :length="pageCount" density="comfortable" :total-visible="6" />
      </div>
    </template>
  </v-card>

  <v-dialog v-model="matchingLink" max-width="620"><v-card v-if="matchingLink"><v-card-title>Match saved link to local track</v-card-title><v-card-text><div class="text-body-2 mb-3">Choose existing Library file. This preserves exact folder identity.</div><v-list lines="two"><v-list-item v-for="file in localFiles.filter((item) => item.tracks.length)" :key="`${file.folder_identity}-${file.relative_path}`" :active="matchFile === file" @click="matchFile = file"><v-list-item-title>{{ fileLabel(file) }}</v-list-item-title><v-list-item-subtitle>{{ file.tracks.map((track) => `${track.artist || ''} ${track.title || track.filename || ''}`).join(', ') }}</v-list-item-subtitle></v-list-item></v-list></v-card-text><v-card-actions><v-spacer /><v-btn variant="text" @click="matchingLink = null">Cancel</v-btn><v-btn color="primary" :disabled="!matchFile" @click="matchSavedLink">Match exact file</v-btn></v-card-actions></v-card></v-dialog>
  <TypedConfirmDialog :model-value="!!deletePreview" :title="`Delete ${deletePreview?.targets.length} approved local files?`" :loading="deleteBusy" @update:model-value="(v) => { if (!v) deletePreview = null }" @confirm="confirmDelete">
    <p>This action deletes only selected approved Library files. No arbitrary path can be entered.</p>
    <v-list density="compact" class="my-3"><v-list-item v-for="target in deletePreview?.targets || []" :key="target.track_id" :title="target.relative_path" /></v-list>
  </TypedConfirmDialog>
  <v-alert v-if="audit.length" variant="tonal" type="info" class="mt-3">Last deletion audit: {{ audit.filter((entry) => entry.outcome === 'deleted').length }} deleted, {{ audit.filter((entry) => entry.outcome !== 'deleted').length }} rejected.</v-alert>

  <ActionMenu v-model="ytMenu.open" :target="ytMenu.target" :items="YT_MENU_ITEMS" @select="ytAction" />
  <ActionMenu v-model="fileMenu.open" :target="fileMenu.target" :items="fileMenuList" @select="fileAction" />
  <ActionMenu v-model="statusMenu.open" :target="statusMenu.target" :items="STATUS_MENU_ITEMS" @select="statusAction" />
  <ActionMenu v-model="untrackedMenu.open" :target="untrackedMenu.target" :items="UNTRACKED_MENU_ITEMS" @select="untrackedAction" />
  <InfoDialog v-model="fileInfo" />
  <ConfirmDialog :model-value="!!downloadDeleteConfirm" title="Delete downloaded file?" confirm-label="Delete" :max-width="440" @update:model-value="(v) => { if (!v) downloadDeleteConfirm = null }" @confirm="confirmDownloadDelete">
    Deletes the file in your download folder. The Library entry and any mp3-folder file stay.
  </ConfirmDialog>

  <ConfirmDialog :model-value="!!removeConfirm" :title="`Remove ${removeConfirm?.count} from Library?`" confirm-label="Remove" :loading="removing" @update:model-value="(v) => { if (!v) removeConfirm = null }" @confirm="confirmRemove">
    Deletes the Library {{ removeConfirm?.count === 1 ? 'entry' : 'entries' }} and any downloaded file(s). Your mp3-folder files are not touched. This can't be undone.
  </ConfirmDialog>
</template>

<style scoped>
.select-spacer { display: inline-block; width: 40px; }
.border-b { border-bottom: 1px solid rgba(255, 255, 255, 0.08); }
.border-t { border-top: 1px solid rgba(255, 255, 255, 0.08); }
</style>
