<script setup>
import { ref, computed, onMounted } from 'vue'
import { api } from './api'
import { reviewTrack, activeTab, invalidateData, libraryFocusIds, useTabRefresh } from './nav'
import { deletionMessage, formatBytes } from './workspace'
import { buildLabels, FILTER_ATTRS, matchesLabelFilter } from './labels'
import { useLabelFilter, usePagination, useSelection, useRowActions, ytUrl, YT_MENU_ITEMS, STATUS_MENU_ITEMS, UNTRACKED_MENU_ITEMS, fileMenuItems } from './curation'
import CurationList from './CurationList.vue'
import VerifyScopeDialog from './VerifyScopeDialog.vue'
import LabelFilterMenu from './LabelFilterMenu.vue'
import ActionMenu from './ActionMenu.vue'
import InfoDialog from './InfoDialog.vue'
import TypedConfirmDialog from './TypedConfirmDialog.vue'
import ConfirmDialog from './ConfirmDialog.vue'

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
const verifyDialog = ref(false)
const removeConfirm = ref(null)   // { ids, count } pending confirmation
const removing = ref(false)
const ytMenu = ref({ open: false, target: [0, 0], row: null })
const fileMenu = ref({ open: false, target: [0, 0], row: null, source: 'local' })
const downloadDeleteConfirm = ref(null)
const statusMenu = ref({ open: false, target: [0, 0], row: null })
const untrackedMenu = ref({ open: false, target: [0, 0], row: null })
// Shared action logic (curation.js). Rows carry the entity data; deleteFile is
// injected because Library's approved/download delete flows are screen-specific.
const { preview, fileInfo, ytAction, fileAction, statusAction } = useRowActions({
  onError: (e) => { error.value = String(e) },
  openReview: (row) => openReview(row.raw),
  deleteFile: (row, source) => deleteFile(row, source),
})

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
// Fold the three-kind entry into one subtitle string; saved links become a link.
function librarySubtitle(e) { return e.author ? (e.detail ? `${e.author} · ${e.detail}` : e.author) : (e.detail || '') }
const listRows = computed(() => paged.value.map((e) => ({
  ...e, selectable: e.kind !== 'saved',
  subtitle: librarySubtitle(e), subtitleHref: e.kind === 'saved' ? e.detail : null,
  // Action contract read by useRowActions.
  ytUrl: ytUrl(e.raw), ytId: entryYtId(e),
  trackId: e.kind === 'track' ? e.id : null,
  setCheck: (v) => { const r = rows.value.find((x) => x.id === e.id); if (r) r.check = v },
  fileSrc: localSrc(e), downloadSrc: entryYtId(e) ? api.downloadAudioUrl(entryYtId(e)) : null,
  revealArg: (source) => source === 'download' ? { download_yt_id: entryYtId(e) }
    : e.kind === 'track' ? { track_id: e.id }
    : { folder_identity: e.raw.folder_identity, relative_path: e.raw.relative_path },
  infoFor: (source) => source === 'download'
    ? { title: 'Downloaded file', lines: [['YouTube id', entryYtId(e)], ['Location', 'Download folder']] }
    : e.kind === 'file'
      ? { title: 'File info', lines: [['File', e.raw.basename], ['Path', e.raw.relative_path], ['Size', formatBytes(e.raw.file_size)]] }
      : { title: 'File info', lines: [['File', e.raw.filename], ['Artist', e.raw.artist || '—'], ['Title', e.raw.title || '—'], ['Duration', e.raw.duration ? `${Math.round(e.raw.duration)}s` : '—']] },
})))
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
// Verify is a paced background task now (see Activity). scope: 'all' | 'unverified'.
async function startVerify(scope) {
  verifying.value = true; error.value = ''
  try {
    await api.verifyLibraryTask(scope)
    verifyDialog.value = false
    activeTab.value = 'activity'
  } catch (e) { error.value = e.message?.includes('409') ? 'A verify task is already running (see Activity).' : String(e) }
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
function onLabel(row, label, ev) {
  const target = [ev.clientX, ev.clientY]
  if (label.key === 'youtube' || label.key === 'dead') ytMenu.value = { open: true, target, row }
  else if (label.key === 'local') fileMenu.value = { open: true, target, row, source: 'local' }
  else if (label.key === 'downloaded') fileMenu.value = { open: true, target, row, source: 'download' }
  else if (label.key === 'confirmed' || label.key === 'rejected') statusMenu.value = { open: true, target, row }
  else if (label.key === 'untracked') untrackedMenu.value = { open: true, target, row }
}
async function untrackedAction(mode) {
  const row = untrackedMenu.value.row; untrackedMenu.value.open = false
  if (!row) return
  if (mode === 'add') await addFileToLibrary(row.raw)
  else if (mode === 'send') await sendFileToWorkspace(row.raw)
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
// File menu delete row exists here (Library manages deletion); source picks the
// wording. The play/info/reveal branches live in the shared fileAction.
const fileMenuList = computed(() => fileMenuItems({ deletable: true, source: fileMenu.value.source }))
// Injected into useRowActions.fileAction. Download = simple confirm (app output);
// mp3-folder file = approved-delete flow; untracked file must be added first.
function deleteFile(row, source) {
  if (source === 'download') { downloadDeleteConfirm.value = { row }; return }
  if (row.kind === 'file') { error.value = 'Add this file to Library first to manage its deletion.'; return }
  previewDeleteOne(row)
}
async function previewDeleteOne(row) {
  try { deletePreview.value = await api.deletePreview([row.id]) }
  catch (e) { error.value = String(e) }
}
async function confirmDownloadDelete() {
  const row = downloadDeleteConfirm.value.row; downloadDeleteConfirm.value = null
  try { await api.downloadDelete([row.ytId]); notice.value = 'Downloaded file deleted.'; invalidateData(); await load() }
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
    <v-btn icon variant="text" :loading="loading" aria-label="Refresh" @click="load">
      <v-icon>mdi-refresh</v-icon><v-tooltip activator="parent" location="bottom">Refresh list</v-tooltip>
    </v-btn>
    <v-btn icon variant="text" :loading="verifying" aria-label="Verify links" @click="verifyDialog = true">
      <v-icon>mdi-link-variant</v-icon><v-tooltip activator="parent" location="bottom">Verify links (background task)</v-tooltip>
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

  <div v-if="loading" class="pa-12 text-center"><v-progress-circular indeterminate color="primary" /></div>
  <CurationList v-else
    :rows="listRows" :selected="selected" :preview-for="preview.previewFor"
    v-model:page="page" v-model:per-page="perPage" :page-count="pageCount" :has-items="entries.length > 0"
    @toggle="(row) => toggle(row.key)" @label="onLabel">
    <template #badge="{ row }"><span v-if="row.kind === 'track'" class="text-caption text-medium-emphasis ml-2">#{{ row.id }}</span></template>
    <template #actions="{ row }">
      <v-btn v-if="row.kind === 'saved'" size="small" variant="tonal" @click="matchingLink = row.raw; matchFile = null">Match local file</v-btn>
      <v-menu v-else-if="row.kind === 'track'">
        <template #activator="{ props }">
          <v-btn icon="mdi-dots-vertical" size="small" variant="text" aria-label="Track actions" v-bind="props" />
        </template>
        <v-list density="compact" min-width="160">
          <v-list-item prepend-icon="mdi-eye-check-outline" title="Review" @click="openReview(row.raw)" />
          <v-list-item prepend-icon="mdi-delete-outline" title="Remove from Library" base-color="error" @click="askRemove([row.id])" />
        </v-list>
      </v-menu>
    </template>
    <template #empty><div class="pa-6 text-medium-emphasis">Nothing here yet.</div></template>
  </CurationList>

  <v-dialog v-model="matchingLink" max-width="620"><v-card v-if="matchingLink"><v-card-title>Match saved link to local track</v-card-title><v-card-text><div class="text-body-2 mb-3">Choose existing Library file. This preserves exact folder identity.</div><v-list lines="two"><v-list-item v-for="file in localFiles.filter((item) => item.tracks.length)" :key="`${file.folder_identity}-${file.relative_path}`" :active="matchFile === file" @click="matchFile = file"><v-list-item-title>{{ fileLabel(file) }}</v-list-item-title><v-list-item-subtitle>{{ file.tracks.map((track) => `${track.artist || ''} ${track.title || track.filename || ''}`).join(', ') }}</v-list-item-subtitle></v-list-item></v-list></v-card-text><v-card-actions><v-spacer /><v-btn variant="text" @click="matchingLink = null">Cancel</v-btn><v-btn color="primary" :disabled="!matchFile" @click="matchSavedLink">Match exact file</v-btn></v-card-actions></v-card></v-dialog>
  <TypedConfirmDialog :model-value="!!deletePreview" :title="`Delete ${deletePreview?.targets.length} approved local files?`" :loading="deleteBusy" @update:model-value="(v) => { if (!v) deletePreview = null }" @confirm="confirmDelete">
    <p>This action deletes only selected approved Library files. No arbitrary path can be entered.</p>
    <v-list density="compact" class="my-3"><v-list-item v-for="target in deletePreview?.targets || []" :key="target.track_id" :title="target.relative_path" /></v-list>
  </TypedConfirmDialog>
  <v-alert v-if="audit.length" variant="tonal" type="info" class="mt-3">Last deletion audit: {{ audit.filter((entry) => entry.outcome === 'deleted').length }} deleted, {{ audit.filter((entry) => entry.outcome !== 'deleted').length }} rejected.</v-alert>

  <ActionMenu v-model="ytMenu.open" :target="ytMenu.target" :items="YT_MENU_ITEMS" @select="(mode) => { ytAction(mode, ytMenu.row); ytMenu.open = false }" />
  <ActionMenu v-model="fileMenu.open" :target="fileMenu.target" :items="fileMenuList" @select="(mode) => { fileAction(mode, fileMenu.row, fileMenu.source); fileMenu.open = false }" />
  <ActionMenu v-model="statusMenu.open" :target="statusMenu.target" :items="STATUS_MENU_ITEMS" @select="(mode) => { statusAction(mode, statusMenu.row); statusMenu.open = false }" />
  <ActionMenu v-model="untrackedMenu.open" :target="untrackedMenu.target" :items="UNTRACKED_MENU_ITEMS" @select="untrackedAction" />
  <InfoDialog v-model="fileInfo" />
  <VerifyScopeDialog v-model="verifyDialog" :busy="verifying" @pick="startVerify" />
  <ConfirmDialog :model-value="!!downloadDeleteConfirm" title="Delete downloaded file?" confirm-label="Delete" :max-width="440" @update:model-value="(v) => { if (!v) downloadDeleteConfirm = null }" @confirm="confirmDownloadDelete">
    Deletes the file in your download folder. The Library entry and any mp3-folder file stay.
  </ConfirmDialog>

  <ConfirmDialog :model-value="!!removeConfirm" :title="`Remove ${removeConfirm?.count} from Library?`" confirm-label="Remove" :loading="removing" @update:model-value="(v) => { if (!v) removeConfirm = null }" @confirm="confirmRemove">
    Deletes the Library {{ removeConfirm?.count === 1 ? 'entry' : 'entries' }} and any downloaded file(s). Your mp3-folder files are not touched. This can't be undone.
  </ConfirmDialog>
</template>

