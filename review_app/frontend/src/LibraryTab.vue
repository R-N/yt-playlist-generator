<script setup>
import { ref, computed, onMounted } from 'vue'
import { api } from './api'
import { reviewTrack, activeTab, invalidateData, libraryFocusIds, useTabRefresh } from './nav'
import { formatBytes } from './workspace'
import { buildLabels, FILTER_ATTRS, matchesLabelFilter } from './labels'
import { useLabelFilter, usePagination, useSelection, useRowActions, useFilePicker, useMembershipActions, useSearchPicker, useForceSet, useAudioDownload, useLocalDelete, ytUrl, ytMenuItems, YT_MENU_ITEMS, STATUS_MENU_ITEMS, untrackedMenuItems, libraryLabelMenu, workspaceLabelMenu, fileMenuItems, downloadMenuItems, withNewBase } from './curation'
import CurationList from './CurationList.vue'
import VerifyScopeDialog from './VerifyScopeDialog.vue'
import LabelFilterMenu from './LabelFilterMenu.vue'
import ActionMenu from './ActionMenu.vue'
import SearchPickerDialog from './SearchPickerDialog.vue'
import ForceSetDialog from './ForceSetDialog.vue'
import InfoEditDialog from './InfoEditDialog.vue'
import FormatDialog from './FormatDialog.vue'
import DownloadRunAlert from './DownloadRunAlert.vue'
import LyricsDialog from './LyricsDialog.vue'
import InfoDialog from './InfoDialog.vue'
import TypedConfirmDialog from './TypedConfirmDialog.vue'
import ConfirmDialog from './ConfirmDialog.vue'

const rows = ref([])
const savedLinks = ref([])
const localFiles = ref([])
const wsItems = ref([])   // workspace items, for cross-membership labels + untracked exclusion
const { labelFilter, activeFilterCount, cycleFilter } = useLabelFilter()
const query = ref('')
const sortBy = ref(null)   // { key, order }
const loading = ref(false)
const opening = ref(null)
const matching = ref(null)
const addingFiles = ref(false)
const error = ref('')
const notice = ref('')
const matchingLink = ref(null)
const matchFile = ref(null)
const verifying = ref(false)
const verifyDialog = ref(false)
const removeConfirm = ref(null)   // { ids, count } pending confirmation
const removing = ref(false)
const ytMenu = ref({ open: false, target: [0, 0], row: null })
const fileMenu = ref({ open: false, target: [0, 0], row: null, source: 'local' })
const statusMenu = ref({ open: false, target: [0, 0], row: null })
const untrackedMenu = ref({ open: false, target: [0, 0], row: null })
const ytMenuState = ref({ items: YT_MENU_ITEMS })
const memberMenu = ref({ open: false, target: [0, 0], label: null, items: [] })
// track_id -> workspace item id (present = staged in Workspace); file refs staged in WS.
const wsByTrack = computed(() => new Map(wsItems.value.filter((it) => it.track_id).map((it) => [it.track_id, it.id])))
const wsFileRefs = computed(() => new Set(wsItems.value.filter((it) => it.relative_path).map((it) => `${it.folder_identity}|${it.relative_path}`)))
// Shared action logic (curation.js). Rows carry the entity data; deleteFile is
// injected because Library's approved/download delete flows are screen-specific.
const { preview, fileInfo, ytAction, fileAction, statusAction } = useRowActions({
  onError: (e) => { error.value = String(e) },
  onNotice: (m) => { notice.value = m },
  openReview: (row) => openReview(row.raw),
  deleteFile: (row, source) => deleteFile(row, source),
  reload: async () => { invalidateData(); await load() },
})
// Membership-label actions (In Library / In Workspace). Remove-from-library routes to
// the typed confirm; remove-from-workspace drops the workspace item (files untouched).
const membership = useMembershipActions({
  onError: (e) => { error.value = String(e) },
  onNotice: (m) => { notice.value = m },
  reload: load,
  removeLibrary: (trackId) => askRemove([trackId]),
  removeWorkspace: async (itemId) => { try { await api.workspaceRemove([itemId]); invalidateData(); await load() } catch (e) { error.value = String(e) } },
})
const lyrics = ref({ open: false, kind: 'workspace', id: null, title: '' })
// Interactive "Find on YouTube" / "Find local file" pickers (see SearchPickerDialog).
const { picker, openYoutube, openLocal, onPick } = useSearchPicker({
  onError: (e) => { error.value = String(e) }, reload: load,
})
// Force-set: "Set YouTube link…" / "Pick local file…" (verify + confirm w/ score).
const { fset, openSetYoutube, pickLocalFile, apply: applyForceSet, setValue: setForceValue } = useForceSet({
  onError: (e) => { error.value = String(e) }, reload: load,
})
// Single-item download (YouTube-label button) + shared file-delete flows (curation.js).
// Download finish → light refresh of just the track rows (downloaded label), not the full
// 4-way load (saved links + local files + workspace).
const { fmtDialog: dlFmt, dlRun, askDownload, chooseFormat, dismissRun } = useAudioDownload({
  onError: (e) => { error.value = String(e) }, onNotice: (m) => { notice.value = m }, reload: refreshRows,
})
const { deletePreview, deleteBusy, deleteOutcome, audit, downloadConfirm, previewLocal, confirmLocal, askDownloadDelete, confirmDownloadDelete } = useLocalDelete({
  onError: (e) => { error.value = String(e) }, onNotice: (m) => { notice.value = m }, reload: load,
})
const trackEntity = (row) => ({ kind: 'track', id: row.id, artist: trackAuthor(row.raw), title: row.raw.title || row.raw.yt_title || row.raw.filename || '' })
const info = ref({ open: false, title: '', data: {}, editable: [] })
async function openInfo(row) {
  try {
    const full = await api.track(row.id)
    info.value = { open: true, title: trackTitle(row.raw), data: full, editable: ['artist', 'title', 'yt_id', 'yt_title', 'yt_channel'] }
  } catch (e) { error.value = String(e) }
}
async function saveInfo(fields) {
  // info.value.data is a detached copy (api.track) — patch it AND the visible row in rows.value.
  // Peers refresh on re-entry (nav.useTabRefresh), so no reload/invalidateData needed here.
  try {
    await api.trackPatch(info.value.data.id, fields)
    Object.assign(info.value.data, fields)
    const r = rows.value.find((x) => x.id === info.value.data.id)
    if (r) Object.assign(r, fields)
    info.value.open = false
  } catch (e) { error.value = String(e) }
}

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
      ytId: row.yt_id, inLibrary: row.id, inWorkspace: wsByTrack.value.get(row.id) ?? null,
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
  const files = localFiles.value.filter((file) => !file.tracks?.length
    && !wsFileRefs.value.has(`${file.folder_identity}|${file.relative_path}`)).map((file) => {
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
  verifyKind: e.kind === 'track' ? 'track' : null, verifyId: e.kind === 'track' ? e.id : null,
  setCheck: (v) => { const r = rows.value.find((x) => x.id === e.id); if (r) r.check = v },
  embed: e.kind === 'track' ? (source) => api.trackEmbed(e.id, source) : null,
  fileSrc: localSrc(e), downloadSrc: entryYtId(e) ? api.downloadAudioUrl(entryYtId(e)) : null,
  revealArg: (source) => source === 'download' ? { download_yt_id: entryYtId(e) }
    : e.kind === 'track' ? { track_id: e.id }
    : { folder_identity: e.raw.folder_identity, relative_path: e.raw.relative_path },
  infoFor: (source) => source === 'download'
    ? { title: 'Downloaded file', lines: [['YouTube id', entryYtId(e)], ['Location', 'Download folder']] }
    : e.kind === 'file'
      ? { title: 'File info', lines: [['File', e.raw.basename], ['Path', e.raw.relative_path], ['Size', formatBytes(e.raw.file_size)]] }
      : { title: 'File info', lines: [['File', e.raw.filename], ['Artist', e.raw.artist || '—'], ['Title', e.raw.title || '—'], ['Duration', e.raw.duration ? `${Math.round(e.raw.duration)}s` : '—']] },
  onRenamed: (name, source) => {   // patch this row in place after Romanize filename (no full reload)
    if (source === 'download') return
    if (e.kind === 'file') { e.raw.basename = name; e.raw.relative_path = withNewBase(e.raw.relative_path, name) }
    else if (e.kind === 'track') e.raw.filename = name
  },
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
    const [library, links, files, ws] = await Promise.all([api.library(), api.savedLinks(), api.localFiles(), api.workspace()])
    rows.value = library.rows; savedLinks.value = links.links; localFiles.value = files.files; wsItems.value = ws.items
  } catch (e) { error.value = String(e) }
  finally { loading.value = false }
}
// Light refresh: just the track rows (e.g. a download finished → downloaded label), no
// spinner, no saved-links/local-files/workspace refetch.
async function refreshRows() {
  try { rows.value = (await api.library()).rows } catch (e) { error.value = String(e) }
}
// Add absolute-path files straight into the Library as tracks; shared picker.
const { picking, pickFiles } = useFilePicker({
  target: 'library',
  onDone: async () => { invalidateData(); await load() },
  onError: (e) => { error.value = String(e) },
})
// Verify is a paced background task now (see Activity). scope: 'all' | 'unverified'.
async function startVerify(scope, ids = null) {
  verifying.value = true; error.value = ''
  try {
    await api.verifyLibraryTask(scope, ids)
    verifyDialog.value = false
    activeTab.value = 'activity'
  } catch (e) { error.value = e.message?.includes('409') ? 'A verify task is already running (see Activity).' : String(e) }
  finally { verifying.value = false }
}
// Verify labels: re-check the SELECTED tracks' link health + local/download (dead link on an
// approved track -> unreviewed). No selection -> ask scope (all vs unverified).
function startVerifyLabels() {
  if (selectedTrackIds.value.length) startVerify('all', selectedTrackIds.value)
  else verifyDialog.value = true
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
  if (label.key === 'youtube' || label.key === 'dead') { ytMenuState.value = { items: ytMenuItems(row.ytId, { canFindLocal: row.kind === 'track' && !row.raw.has_local, canSetLocal: row.kind === 'track' }) }; ytMenu.value = { open: true, target, row } }
  else if (label.key === 'local') fileMenu.value = { open: true, target, row, source: 'local', items: fileMenuItems({ deletable: true, source: 'local', canFindYoutube: row.kind === 'track' && !row.ytId, canSetYoutube: row.kind === 'track', canEmbed: row.kind === 'track' }) }
  else if (label.key === 'downloaded') fileMenu.value = { open: true, target, row, source: 'download', items: downloadMenuItems({ deletable: true }) }
  else if (label.key === 'confirmed' || label.key === 'rejected') statusMenu.value = { open: true, target, row }
  else if (label.key === 'untracked') untrackedMenu.value = { open: true, target, row }
  else if (label.key === 'inlibrary') memberMenu.value = { open: true, target, label, row, items: libraryLabelMenu({ trackId: label.trackId, onScreen: 'library', inWorkspace: wsByTrack.value.has(label.trackId) }) }
  else if (label.key === 'inworkspace') memberMenu.value = { open: true, target, label, row, items: workspaceLabelMenu({ onScreen: 'library', inLibrary: true }) }
}
function onFileMenu(mode, row, source) {
  if (mode === 'find-youtube') openYoutube({ title: trackTitle(row.raw), artist: trackAuthor(row.raw), songTitle: row.raw.title || row.raw.yt_title || row.raw.filename || '', target: { kind: 'track', id: row.id } })
  else if (mode === 'set-youtube') openSetYoutube(trackEntity(row))
  else fileAction(mode, row, source)   // embed-metadata + play/info/reveal/delete all handled here
}
function onYtMenu(mode, row) {
  if (mode === 'find-local') openLocal({ title: trackTitle(row.raw), query: trackTitle(row.raw), target: { kind: 'track', id: row.id } })
  else if (mode === 'pick-local') pickLocalFile(trackEntity(row))
  else if (mode === 'download') askDownload([row.ytId])
  else ytAction(mode, row)
}
function onMemberMenu(mode) {
  if (mode === 'info') openInfo(memberMenu.value.row)
  else if (mode === 'view-lyrics') lyrics.value = { open: true, kind: 'workspace', id: memberMenu.value.label.workspaceItemId, title: trackTitle(memberMenu.value.row.raw) }
  else membership.run(mode, memberMenu.value.label)
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
// Injected into useRowActions.fileAction. Download = simple confirm (app output);
// mp3-folder file = approved-delete flow (both live in useLocalDelete); untracked file
// has no Library row so it must be added first.
function deleteFile(row, source) {
  if (source === 'download') { askDownloadDelete([row.ytId]); return }
  if (row.kind === 'file') { error.value = 'Add this file to Library first to manage its deletion.'; return }
  previewLocal([row.id])
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
// One "Send to Workspace" for the whole selection: exact tracks via workspaceLibrary,
// untracked files via workspaceAddFiles. (Merged from the old two separate buttons.)
async function sendSelectionToWorkspace() {
  const trackIds = selectedTrackIds.value
  const fileRefs = selectedFileRefs.value
  if (!trackIds.length && !fileRefs.length) return
  addingFiles.value = true; error.value = ''; notice.value = ''
  try {
    await Promise.all(trackIds.map((id) => api.workspaceLibrary(id)))
    if (fileRefs.length) await api.workspaceAddFiles(fileRefs)
    notice.value = `Sent ${trackIds.length + fileRefs.length} to Workspace.`
    selected.value = []; invalidateData(); await load()
  } catch (e) { error.value = String(e) } finally { addingFiles.value = false }
}
async function addSelectedFiles() {
  if (!selectedFileRefs.value.length) return
  addingFiles.value = true; error.value = ''; notice.value = ''
  try { const r = await api.addFilesToLibrary(selectedFileRefs.value); notice.value = `Added ${r.added} to Library.`; selected.value = []; invalidateData(); await load() }
  catch (e) { error.value = String(e) } finally { addingFiles.value = false }
}
const previewDelete = () => previewLocal(deletable.value.map((row) => row.id))
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
  <v-toolbar density="comfortable" class="responsive-toolbar mb-3 px-2 rounded-lg" color="surface" border>
    <v-checkbox-btn :model-value="allVisible" density="compact" aria-label="Select all visible" @update:model-value="toggleAll" />
    <span class="text-caption text-medium-emphasis mr-1">{{ selected.length ? `${selected.length} selected` : `${filtered.length} shown` }}</span>
    <v-btn icon variant="text" :loading="loading" aria-label="Refresh" @click="load">
      <v-icon>mdi-refresh</v-icon><v-tooltip activator="parent" location="bottom">Refresh list</v-tooltip>
    </v-btn>
    <v-btn icon variant="text" :loading="picking" aria-label="Add files" @click="pickFiles">
      <v-icon>mdi-file-plus-outline</v-icon><v-tooltip activator="parent" location="bottom">Add files from disk as tracks (absolute path)</v-tooltip>
    </v-btn>
    <v-btn icon variant="text" :loading="verifying" aria-label="Verify labels" @click="startVerifyLabels">
      <v-icon>mdi-check-decagram-outline</v-icon><v-tooltip activator="parent" location="bottom">Verify labels — link health + local/download for selection (background)</v-tooltip>
    </v-btn>
    <v-btn v-if="selectedTrackIds.length || selectedFileRefs.length" icon color="primary" variant="text" :loading="addingFiles" aria-label="Send to Workspace" @click="sendSelectionToWorkspace">
      <v-icon>mdi-send</v-icon><v-tooltip activator="parent" location="bottom">Send {{ selectedTrackIds.length + selectedFileRefs.length }} to Workspace</v-tooltip>
    </v-btn>
    <v-btn v-if="selectedFileRefs.length" icon color="primary" variant="text" :loading="addingFiles" aria-label="Add files to Library" @click="addSelectedFiles">
      <v-icon>mdi-plus-box-multiple</v-icon><v-tooltip activator="parent" location="bottom">Add {{ selectedFileRefs.length }} files to Library</v-tooltip>
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
    </template>
    <template #empty><div class="pa-6 text-medium-emphasis">Nothing here yet.</div></template>
  </CurationList>

  <v-dialog v-model="matchingLink" max-width="620"><v-card v-if="matchingLink"><v-card-title>Match saved link to local track</v-card-title><v-card-text><div class="text-body-2 mb-3">Choose existing Library file. This preserves exact folder identity.</div><v-list lines="two"><v-list-item v-for="file in localFiles.filter((item) => item.tracks.length)" :key="`${file.folder_identity}-${file.relative_path}`" :active="matchFile === file" @click="matchFile = file"><v-list-item-title>{{ fileLabel(file) }}</v-list-item-title><v-list-item-subtitle>{{ file.tracks.map((track) => `${track.artist || ''} ${track.title || track.filename || ''}`).join(', ') }}</v-list-item-subtitle></v-list-item></v-list></v-card-text><v-card-actions><v-spacer /><v-btn variant="text" @click="matchingLink = null">Cancel</v-btn><v-btn color="primary" :disabled="!matchFile" @click="matchSavedLink">Match exact file</v-btn></v-card-actions></v-card></v-dialog>
  <DownloadRunAlert :run="dlRun" @dismiss="dismissRun" />
  <v-alert v-if="deleteOutcome" type="info" variant="tonal" closable class="mt-3" @click:close="deleteOutcome = ''">{{ deleteOutcome }}</v-alert>
  <TypedConfirmDialog :model-value="!!deletePreview" :title="`Delete ${deletePreview?.targets.length} approved local files?`" :loading="deleteBusy" @update:model-value="(v) => { if (!v) deletePreview = null }" @confirm="confirmLocal">
    <p>This action deletes only selected approved Library files. No arbitrary path can be entered.</p>
    <v-list density="compact" class="my-3"><v-list-item v-for="target in deletePreview?.targets || []" :key="target.track_id" :title="target.relative_path" /></v-list>
  </TypedConfirmDialog>
  <v-alert v-if="audit.length" variant="tonal" type="info" class="mt-3">Last deletion audit: {{ audit.filter((entry) => entry.outcome === 'deleted').length }} deleted, {{ audit.filter((entry) => entry.outcome !== 'deleted').length }} rejected.</v-alert>

  <ActionMenu v-model="ytMenu.open" :target="ytMenu.target" :items="ytMenuState.items" @select="(mode) => { onYtMenu(mode, ytMenu.row); ytMenu.open = false }" />
  <ActionMenu v-model="memberMenu.open" :target="memberMenu.target" :items="memberMenu.items" @select="(mode) => { onMemberMenu(mode); memberMenu.open = false }" />
  <ActionMenu v-model="fileMenu.open" :target="fileMenu.target" :items="fileMenu.items" @select="(mode) => { onFileMenu(mode, fileMenu.row, fileMenu.source); fileMenu.open = false }" />
  <SearchPickerDialog v-model="picker.open" :mode="picker.mode" :title="picker.title" :initial-query="picker.query" :artist="picker.artist" :song-title="picker.songTitle" @pick="onPick" />
  <ForceSetDialog :state="fset" @update:open="fset.open = $event" @update:value="setForceValue" @apply="applyForceSet" />
  <InfoEditDialog v-model="info.open" :title="info.title" :data="info.data" :editable="info.editable" @save="saveInfo" @error="error = $event" />
  <LyricsDialog v-model="lyrics.open" :kind="lyrics.kind" :id="lyrics.id" :title="lyrics.title" />
  <ActionMenu v-model="statusMenu.open" :target="statusMenu.target" :items="STATUS_MENU_ITEMS" @select="(mode) => { statusAction(mode, statusMenu.row); statusMenu.open = false }" />
  <ActionMenu v-model="untrackedMenu.open" :target="untrackedMenu.target" :items="untrackedMenuItems()" @select="untrackedAction" />
  <InfoDialog v-model="fileInfo" />
  <VerifyScopeDialog v-model="verifyDialog" :busy="verifying" @pick="startVerify" />
  <FormatDialog :model-value="dlFmt.open" :busy="dlFmt.busy" title="Download audio" @update:model-value="dlFmt.open = $event" @pick="chooseFormat" />
  <ConfirmDialog :model-value="!!downloadConfirm" title="Delete downloaded file?" confirm-label="Delete" :max-width="440" @update:model-value="(v) => { if (!v) downloadConfirm = null }" @confirm="confirmDownloadDelete">
    Deletes the file in your download folder. The Library entry and any mp3-folder file stay.
  </ConfirmDialog>

  <ConfirmDialog :model-value="!!removeConfirm" :title="`Remove ${removeConfirm?.count} from Library?`" confirm-label="Remove" :loading="removing" @update:model-value="(v) => { if (!v) removeConfirm = null }" @confirm="confirmRemove">
    Deletes the Library {{ removeConfirm?.count === 1 ? 'entry' : 'entries' }} and any downloaded file(s). Your mp3-folder files are not touched. This can't be undone.
  </ConfirmDialog>
</template>
