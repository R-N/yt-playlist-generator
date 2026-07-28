<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { api } from './api'
import { activeTab, focusTrack, invalidateData, reviewTrack } from './nav'
import { keyToAction, fmt, advanceIndex, prevIndex, youtubeEmbed, confidenceColor } from './review'
import { formatDuration } from './workspace'
import { buildLabels } from './labels'
import { useRowActions, useSearchPicker, useForceSet, useMembershipActions, useAudioDownload, useLocalDelete, ytUrl, ytMenuItems, STATUS_MENU_ITEMS, libraryLabelMenu, workspaceLabelMenu, fileMenuItems, downloadMenuItems } from './curation'
import LabelRow from './LabelRow.vue'
import ActionMenu from './ActionMenu.vue'
import MediaPreview from './MediaPreview.vue'
import SearchPickerDialog from './SearchPickerDialog.vue'
import ForceSetDialog from './ForceSetDialog.vue'
import InfoEditDialog from './InfoEditDialog.vue'
import FormatDialog from './FormatDialog.vue'
import DownloadRunAlert from './DownloadRunAlert.vue'
import TypedConfirmDialog from './TypedConfirmDialog.vue'
import LyricsDialog from './LyricsDialog.vue'
import InfoDialog from './InfoDialog.vue'
import ConfirmDialog from './ConfirmDialog.vue'
import LyricsView from './LyricsView.vue'

const counts = ref({ total: 0, unreviewed: 0, approved: 0, rejected: 0 })
const queue = ref([])
const idx = ref(0)
const status = ref('unreviewed')
const loading = ref(false)
const finding = ref(false)
const error = ref('')
const notice = ref('')
const fromLibrary = ref(false)   // reviewing a single track handed over by the Library
const wsItems = ref([])          // workspace items, for the In-Workspace membership label

const current = computed(() => queue.value[idx.value] || null)
const pctSim = (v) => (v == null || v === '' ? 0 : Math.round(Number(v) * 100))

// Approval checklist: reviewer marks which parts are verified-correct. Recorded with
// the decision. Approve requires YouTube checked (backend enforces too).
const checklist = ref({ youtube: false, local: false, lyrics: false, metadata: false })
const hasMetadata = computed(() => { const t = current.value; return !!(t && (t.mb_title || t.mb_artist)) })
const canApprove = computed(() => !!(current.value?.yt_id && checklist.value.youtube))
function checkedParts() { return Object.keys(checklist.value).filter((k) => checklist.value[k]) }
function resetChecklist() { checklist.value = { youtube: false, local: false, lyrics: false, metadata: false } }
// track_id -> workspace item id (present = staged in Workspace)
const wsByTrack = computed(() => new Map(wsItems.value.filter((it) => it.track_id).map((it) => [it.track_id, it.id])))

function trackTitle(t) { return t.title || t.yt_title || t.filename || t.yt_id || '(untitled)' }
function trackAuthor(t) { return t.artist || t.yt_channel || '' }
function trackEntity(row) { return { kind: 'track', id: row.id, artist: trackAuthor(row.raw), title: row.raw.title || row.raw.yt_title || '' } }

async function refreshCounts() { counts.value = await api.counts() }
async function loadWs() { try { wsItems.value = (await api.workspace()).items } catch { /* labels degrade to no In-Workspace */ } }

async function loadQueue() {
  loading.value = true
  error.value = ''
  try {
    const [data] = await Promise.all([api.rows(status.value, 200, 0), loadWs()])
    queue.value = data.rows
    idx.value = 0
  } catch (e) {
    error.value = String(e)
  } finally {
    loading.value = false
  }
}

// Refetch the track currently under review (after a label action) + workspace membership.
async function refreshCurrent() {
  const t = current.value
  if (t) { try { queue.value.splice(idx.value, 1, await api.track(t.id)) } catch { /* keep old */ } }
  await loadWs()
}

// Approve/reject opens the checklist modal first; the actual write happens on confirm.
const decideDialog = ref({ open: false, approve: true })
function decide(approve) {
  if (!current.value) return
  resetChecklist()
  decideDialog.value = { open: true, approve }
}
async function confirmDecide() {
  const t = current.value
  const approve = decideDialog.value.approve
  if (!t) return
  if (approve && !canApprove.value) return
  try {
    await api.decide(t.id, approve, checkedParts())
    invalidateData()
    t.check = approve ? 1 : 0
    decideDialog.value.open = false
    if (fromLibrary.value) exitFocus()      // one-off from the list: done, go back
    else advance()
    refreshCounts()
  } catch (e) {
    error.value = String(e)
  }
}

// Review a single track handed over from the Library list.
watch(focusTrack, (t) => {
  if (!t) return
  queue.value = [t]
  idx.value = 0
  fromLibrary.value = true
  loadWs()
})
watch(activeTab, (tab, previous) => {
  if (tab === 'review' && tab !== previous && !focusTrack.value) {
    refreshCounts()
    loadQueue()
  }
})

function exitFocus() {
  focusTrack.value = null
  fromLibrary.value = false
  loadQueue()
}

function backToLibrary() {
  focusTrack.value = null
  fromLibrary.value = false
  activeTab.value = 'library'
}

function advance() {
  const { idx: next, reload } = advanceIndex(idx.value, queue.value.length)
  if (reload) loadQueue()
  else idx.value = next
}

function prev() { idx.value = prevIndex(idx.value) }

// Replace this track's YouTube candidate with the best fresh search hit (skips ids
// already rejected for it). Applied as unreviewed; the panel updates in place.
async function findAnother() {
  const t = current.value
  if (!t) return
  finding.value = true; error.value = ''
  try {
    const updated = await api.reviewFindYoutube(t.id)
    queue.value.splice(idx.value, 1, updated)
    invalidateData()
    refreshCounts()
  } catch (e) {
    error.value = e.message?.includes('404') ? 'No new YouTube match found (all hits rejected or none scored).' : String(e)
  } finally { finding.value = false }
}

// ── Shared label action hub (same menus as Workspace / Library) ──────────────
const { preview, fileInfo, ytAction, fileAction, statusAction } = useRowActions({
  onError: (e) => { error.value = String(e) },
  onNotice: (m) => { notice.value = m },
  openReview: (row) => reviewTrack(row.raw),
  deleteFile: (row, source) => deleteFile(row, source),
  reload: refreshCurrent,
})
// Single-item download (YouTube-label) + file deletes, shared (curation.js).
const { fmtDialog: dlFmt, dlRun, askDownload, chooseFormat, dismissRun } = useAudioDownload({
  onError: (e) => { error.value = String(e) }, onNotice: (m) => { notice.value = m }, reload: refreshCurrent,
})
const { deletePreview, deleteBusy, deleteOutcome, downloadConfirm, previewLocal, confirmLocal, askDownloadDelete, confirmDownloadDelete } = useLocalDelete({
  onError: (e) => { error.value = String(e) }, onNotice: (m) => { notice.value = m }, reload: refreshCurrent,
})
function deleteFile(row, source) {
  if (source === 'download') { askDownloadDelete([row.ytId]); return }
  if (row.raw.check !== 1) { error.value = 'Approve the track before deleting its local file.'; return }
  previewLocal([row.id])
}
const membership = useMembershipActions({
  onError: (e) => { error.value = String(e) },
  onNotice: (m) => { notice.value = m },
  reload: refreshCurrent,
  removeLibrary: (trackId) => { libRemoveConfirm.value = { trackId } },
  removeWorkspace: async (itemId) => { try { await api.workspaceRemove([itemId]); invalidateData(); await refreshCurrent() } catch (e) { error.value = String(e) } },
})
const { picker, openYoutube, openLocal, onPick } = useSearchPicker({ onError: (e) => { error.value = String(e) }, reload: refreshCurrent })
const { fset, openSetYoutube, pickLocalFile, apply: applyForceSet, setValue: setForceValue } = useForceSet({ onError: (e) => { error.value = String(e) }, reload: refreshCurrent })

const ytMenu = ref({ open: false, target: [0, 0], row: null, items: [] })
const fileMenu = ref({ open: false, target: [0, 0], row: null, source: 'local', items: [] })
const statusMenu = ref({ open: false, target: [0, 0], row: null })
const memberMenu = ref({ open: false, target: [0, 0], label: null, items: [], row: null })
const libRemoveConfirm = ref(null)
const info = ref({ open: false, title: '', data: {}, editable: [] })
const lyrics = ref({ open: false, kind: 'workspace', id: null, title: '' })

// The current track normalized into the shared row shape (labels + action contract).
const reviewRow = computed(() => {
  const t = current.value
  if (!t) return null
  const wsId = wsByTrack.value.get(t.id) ?? null
  const labels = buildLabels({
    hasLink: !!t.yt_id, aliveLink: t.yt_health === 'ok',
    deadLink: t.yt_health === 'dead' || t.yt_health === 'private',
    localCount: t.has_local ? 1 : 0, downloaded: !!t.downloaded, check: t.check,
    ytId: t.yt_id, inLibrary: t.id, inWorkspace: wsId,
  })
  // Always expose the workspace label in Review (lyrics/metadata hub) even when the
  // track isn't staged; its actions then operate on the track, plus Send to workspace.
  if (wsId == null) labels.push({ key: 'inworkspace', label: 'Workspace', color: 'deep-purple-accent-3', icon: 'mdi-tray-full', tooltip: 'Lyrics / metadata', workspaceItemId: null, trackId: t.id })
  return {
    kind: 'track', key: `t${t.id}`, id: t.id, raw: t, labels,
    ytUrl: ytUrl(t), ytId: t.yt_id, trackId: t.id,
    verifyKind: 'track', verifyId: t.id,
    setCheck: (v) => { t.check = v },
    embed: (source) => api.trackEmbed(t.id, source),
    fileSrc: api.audioUrl(t.id), downloadSrc: t.yt_id ? api.downloadAudioUrl(t.yt_id) : null,
    revealArg: (source) => source === 'download' ? { download_yt_id: t.yt_id } : { track_id: t.id },
    infoFor: (source) => source === 'download'
      ? { title: 'Downloaded file', lines: [['YouTube id', t.yt_id], ['Location', 'Download folder']] }
      : { title: 'File info', lines: [['File', t.filename], ['Artist', t.artist || '—'], ['Title', t.title || '—']] },
  }
})

function onLabel(row, label, ev) {
  const target = [ev.clientX, ev.clientY]
  const t = row.raw
  if (label.key === 'youtube' || label.key === 'dead') ytMenu.value = { open: true, target, row, items: ytMenuItems(row.ytId, { canFindLocal: !t.has_local, canSetLocal: true }) }
  else if (label.key === 'local') fileMenu.value = { open: true, target, row, source: 'local', items: fileMenuItems({ deletable: true, source: 'local', canFindYoutube: !row.ytId, canSetYoutube: true, canEmbed: true }) }
  else if (label.key === 'downloaded') fileMenu.value = { open: true, target, row, source: 'download', items: downloadMenuItems({ deletable: true }) }
  else if (label.key === 'confirmed' || label.key === 'rejected') statusMenu.value = { open: true, target, row }
  else if (label.key === 'inlibrary') memberMenu.value = { open: true, target, label, row, items: libraryLabelMenu({ trackId: label.trackId, onScreen: 'review', inWorkspace: wsByTrack.value.has(label.trackId) }) }
  else if (label.key === 'inworkspace') memberMenu.value = { open: true, target, label, row, items: workspaceLabelMenu({ onScreen: 'review', inLibrary: true, staged: label.workspaceItemId != null }) }
}
function onYtMenu(mode, row) {
  if (mode === 'find-local') openLocal({ title: trackTitle(row.raw), query: trackTitle(row.raw), target: { kind: 'track', id: row.id } })
  else if (mode === 'pick-local') pickLocalFile(trackEntity(row))
  else if (mode === 'download') askDownload([row.ytId])
  else ytAction(mode, row)
}
function onFileMenu(mode, row, source) {
  if (mode === 'find-youtube') openYoutube({ title: trackTitle(row.raw), artist: trackAuthor(row.raw), songTitle: row.raw.title || row.raw.yt_title || row.raw.filename || '', target: { kind: 'track', id: row.id } })
  else if (mode === 'set-youtube') openSetYoutube(trackEntity(row))
  else fileAction(mode, row, source)
}
// In Review the reviewed entity is the track itself, so lyrics/metadata always target
// the track (kind='track'); membership ops (save/show/remove/send) use the label.
async function onMemberMenu(mode) {
  const { row, label } = memberMenu.value
  if (mode === 'info') return openInfo(row)
  if (mode === 'view-lyrics') { lyrics.value = { open: true, kind: 'track', id: row.id, title: trackTitle(row.raw) }; return }
  if (mode === 'find-lyrics') {
    try { const r = await api.entityFindLyrics('track', row.id); notice.value = r.found ? 'Found lyrics.' : 'No lyrics found.'; if (rightTab.value === 'lyrics') await loadLyrics() }
    catch (e) { error.value = String(e) }
    return
  }
  if (mode === 'find-metadata') {
    try { await api.entityFindMetadata('track', row.id); notice.value = 'Applied MusicBrainz metadata.'; await refreshCurrent() }
    catch (e) { error.value = e.message?.includes('404') ? 'No confident MusicBrainz match.' : String(e) }
    return
  }
  membership.run(mode, label)
}
async function openInfo(row) {
  try {
    const full = await api.track(row.id)
    info.value = { open: true, title: trackTitle(row.raw), data: full, editable: ['artist', 'title', 'yt_id', 'yt_title', 'yt_channel'] }
  } catch (e) { error.value = String(e) }
}
async function saveInfo(fields) {
  try { await api.trackPatch(info.value.data.id, fields); info.value.open = false; invalidateData(); await refreshCurrent() }
  catch (e) { error.value = String(e) }
}

// ── Right panel: Embed | Lyrics (synced scroll to whichever audio plays) ─────
const rightTab = ref('embed')
const lyricsState = ref({ loading: false, found: false, synced: false, lyrics: '', error: '' })
const fileTags = ref({})            // artist/title/album read from the actual file
const playTime = ref(null)          // seconds of the currently-playing audio; null = idle
const activeAudio = ref(null)       // 'local' | 'yt'
const localAudio = ref(null)
const ytAudio = ref(null)
const hasFileTags = computed(() => !!(fileTags.value.tag_artist || fileTags.value.tag_title || fileTags.value.tag_album))
// "Your file" header = the local file's own identity only: our saved metadata (DB
// artist/title columns) -> the file's embedded tags -> the file NAME. No YouTube
// fallback — the YouTube candidate has its own section below. Tagless rips (null DB
// + no ID3) still carry their title in the filename, so use the cleaned stem there.
function cleanStem(filename) {
  return String(filename || '')
    .replace(/\.[a-z0-9]+$/i, '')                     // extension
    .replace(/^(www\.)?y2meta\.com\s*-\s*/i, '')      // common ripper prefix
    .replace(/\s*[[(](\d+\s*k(bps)?|\d+p|audio|official.*?)[)\]]\s*$/i, '')  // (256 kbps) / [1080p] tail
    .trim()
}
const fileArtist = computed(() => { const t = current.value; return t && (t.artist || fileTags.value.tag_artist || '') })
const fileTitle = computed(() => {
  const t = current.value
  if (!t) return ''
  return t.title || fileTags.value.tag_title || cleanStem(t.filename)
})
const titleSource = computed(() => {
  const t = current.value
  if (!t || t.title || t.artist) return ''           // straight from the DB row (our saved metadata)
  if (fileTags.value.tag_title || fileTags.value.tag_artist) return 'file tags'
  return fileTitle.value ? 'file name' : ''
})

async function loadLyrics(refresh = false) {
  const t = current.value
  if (!t) return
  lyricsState.value = { ...lyricsState.value, loading: true, error: '' }
  try {
    const r = await api.entityLyrics('track', t.id, refresh)
    lyricsState.value = { loading: false, found: r.found, synced: r.synced, lyrics: r.lyrics, error: '' }
  } catch (e) { lyricsState.value = { ...lyricsState.value, loading: false, error: String(e) } }
}
async function onSaveLyrics(text) {
  const t = current.value
  if (!t) return
  try {
    const r = await api.entitySaveLyrics('track', t.id, text)
    lyricsState.value = { loading: false, found: r.found, synced: r.synced, lyrics: r.lyrics, error: '' }
    notice.value = 'Saved lyrics.'
  } catch (e) { error.value = String(e) }
}
async function loadFileTags() {
  const t = current.value
  if (!t) { fileTags.value = {}; return }
  try { fileTags.value = await api.entityFileTags('track', t.id) } catch { fileTags.value = {} }
}
watch(() => current.value?.id, () => {
  playTime.value = null; activeAudio.value = null
  resetChecklist()
  lyricsState.value = { loading: false, found: false, synced: false, lyrics: '', error: '' }
  loadFileTags()
  if (rightTab.value === 'lyrics') loadLyrics()
})
watch(rightTab, (tab) => { if (tab === 'lyrics' && !lyricsState.value.found && !lyricsState.value.loading) loadLyrics() })
// Only one of the two audios plays at a time; the lyrics follow the live one.
function onAudioPlay(which) {
  activeAudio.value = which
  const other = which === 'local' ? ytAudio.value : localAudio.value
  if (other && !other.paused) other.pause()
}
function onAudioTime(which, e) { if (activeAudio.value === which) playTime.value = e.target.currentTime }

function onKey(e) {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return
  const action = keyToAction(e.key)
  if (action === 'approve') decide(true)
  else if (action === 'reject') decide(false)
  else if (action === 'back') prev()
}

onMounted(() => {
  refreshCounts()
  loadQueue()
  window.addEventListener('keydown', onKey)
})
onUnmounted(() => window.removeEventListener('keydown', onKey))
</script>

<template>
  <!-- Single row: filter, inline match metrics, icon decide buttons. -->
  <div class="review-toolbar d-flex align-center flex-wrap mb-4" style="gap:6px 14px">
    <v-select v-model="status" :items="['unreviewed','approved','rejected','all']"
      density="compact" variant="solo-filled" flat hide-details
      prepend-inner-icon="mdi-filter-variant" style="min-width:200px;max-width:220px"
      @update:model-value="loadQueue" />
    <v-spacer />
    <LabelRow v-if="reviewRow" :labels="reviewRow.labels" @label-click="(label, ev) => onLabel(reviewRow, label, ev)" />
    <v-btn icon variant="text" :loading="finding" :disabled="!current" aria-label="Find another YouTube link" @click="findAnother">
      <v-icon>mdi-link-variant-plus</v-icon><v-tooltip activator="parent" location="bottom">Find another YouTube link (skips rejected)</v-tooltip>
    </v-btn>
    <v-btn icon color="error" :disabled="!current" aria-label="Reject (R / ←)" @click="decide(false)">
      <v-icon>mdi-close</v-icon><v-tooltip activator="parent" location="bottom">Reject · R / ←</v-tooltip>
    </v-btn>
    <v-btn icon variant="text" aria-label="Back (↑)" @click="prev">
      <v-icon>mdi-undo</v-icon><v-tooltip activator="parent" location="bottom">Back · ↑</v-tooltip>
    </v-btn>
    <v-btn icon color="success" :disabled="!current" aria-label="Approve (A / →)" @click="decide(true)">
      <v-icon>mdi-check</v-icon><v-tooltip activator="parent" location="bottom">Approve · A / →</v-tooltip>
    </v-btn>
  </div>

  <v-alert v-if="fromLibrary" type="info" variant="tonal" density="compact" class="mb-4">
    <div class="d-flex align-center">
      Reviewing one track from the Library.
      <v-spacer />
      <v-btn size="small" variant="text" prepend-icon="mdi-arrow-left" @click="backToLibrary">Back to Library</v-btn>
    </div>
  </v-alert>
  <v-alert v-if="error" type="error" closable class="mb-4" @click:close="error=''">{{ error }}</v-alert>
  <v-alert v-if="notice" type="success" variant="tonal" closable class="mb-4" @click:close="notice=''">{{ notice }}</v-alert>
  <v-alert v-if="deleteOutcome" type="info" variant="tonal" closable class="mb-4" @click:close="deleteOutcome=''">{{ deleteOutcome }}</v-alert>
  <DownloadRunAlert :run="dlRun" @dismiss="dismissRun" />

  <div v-if="loading" class="text-center pa-12">
    <v-progress-circular indeterminate color="primary" size="48" />
  </div>

  <v-row v-else-if="current">
    <!-- LEFT: your file (top), YouTube candidate audio (bottom), match detail below. -->
    <v-col cols="12" md="6">
      <v-card variant="outlined" class="pa-4 mb-3">
        <v-card-subtitle class="px-0"><v-icon size="18" color="primary" class="mr-2">mdi-folder-music</v-icon>Your file</v-card-subtitle>
        <div class="text-h6 mb-1">{{ fileArtist || '—' }} – {{ fileTitle || '—' }}</div>
        <div class="text-caption text-medium-emphasis mb-2">{{ current.filename }}</div>
        <div class="d-flex flex-wrap align-center text-caption text-medium-emphasis mb-3" style="gap:2px 14px">
          <span v-if="current.duration"><v-icon size="13">mdi-clock-outline</v-icon> {{ formatDuration(current.duration) }}</span>
          <span v-if="current.audio_format">{{ current.audio_format }}<span v-if="current.audio_bitrate"> · {{ fmt(current.audio_bitrate) }}k</span></span>
          <span v-if="fileTags.tag_album"><v-icon size="13">mdi-album</v-icon> {{ fileTags.tag_album }}</span>
          <span v-if="hasFileTags"><v-icon size="13">mdi-tag-outline</v-icon> file tags: {{ fileTags.tag_artist || '—' }} – {{ fileTags.tag_title || '—' }}</span>
          <span v-else-if="fileTags.has_file === false" class="text-warning">local file missing</span>
          <span v-else>no embedded file tags</span>
          <span v-if="titleSource" class="text-disabled">· via {{ titleSource }}</span>
        </div>
        <v-chip v-if="current.local_better" color="warning" size="small" variant="tonal" class="mb-3">
          local ≥ threshold ({{ fmt(current.local_bitrate) }}k)
        </v-chip>
        <audio v-if="current.has_local" ref="localAudio" :src="api.audioUrl(current.id)" controls preload="none"
          @play="onAudioPlay('local')" @timeupdate="(e) => onAudioTime('local', e)" />
        <v-alert v-else type="warning" density="compact" variant="tonal">local file not found</v-alert>
      </v-card>

      <v-card variant="outlined" class="pa-4 mb-3">
        <v-card-subtitle class="px-0"><v-icon size="18" color="error" class="mr-2">mdi-youtube</v-icon>YouTube candidate</v-card-subtitle>
        <div class="text-h6 mb-1">{{ current.yt_title || current.yt_channel }}</div>
        <div class="text-caption text-medium-emphasis mb-3">
          {{ current.yt_channel }}<span v-if="current.yt_views"> · {{ current.yt_views }} views</span><span v-if="current.yt_likes"> · {{ current.yt_likes }} likes</span>
        </div>
        <audio v-if="current.yt_id" ref="ytAudio" :src="api.ytAudioUrl(current.yt_id)" controls preload="none"
          @play="onAudioPlay('yt')" @timeupdate="(e) => onAudioTime('yt', e)" />
      </v-card>

      <!-- MusicBrainz verdict -->
      <v-alert v-if="current.mb_title || current.mb_artist"
        :color="confidenceColor(current.mb_confidence)" variant="tonal" density="comfortable" class="mb-2"
        :icon="String(current.mb_suggest)==='1' ? 'mdi-thumb-up' : 'mdi-database-check'">
        <div class="text-caption text-uppercase font-weight-bold">
          MusicBrainz · {{ current.mb_confidence }} match
          <span v-if="String(current.mb_suggest)==='1'"> · suggests approve</span>
        </div>
        <div><strong>{{ current.mb_artist }}</strong> – {{ current.mb_title }}
          <span class="text-caption text-medium-emphasis ml-2">AcoustID {{ fmt(current.ac_score, 2) }}</span>
        </div>
      </v-alert>

      <!-- Play from a label menu surfaces here (shared preview). -->
      <MediaPreview v-if="reviewRow" :preview="preview.previewFor(reviewRow)" />

      <!-- Match metrics (moved off the crowded top bar). -->
      <v-card variant="tonal" class="pa-3">
        <div class="d-flex align-center flex-wrap" style="gap:6px 18px">
          <span class="metric"><span class="metric-label">Score</span>{{ fmt(current.score) }}</span>
          <span class="metric"><span class="metric-label">Artist</span>{{ pctSim(current.sim_artist) }}%</span>
          <span class="metric"><span class="metric-label">Art+Title</span>{{ pctSim(current.sim_artist_title) }}%</span>
          <span class="metric"><span class="metric-label">Title</span>{{ pctSim(current.sim_title) }}%</span>
          <span class="metric"><span class="metric-label">Format</span>{{ current.audio_format || '—' }} {{ fmt(current.audio_bitrate) }}k</span>
        </div>
      </v-card>
    </v-col>

    <!-- RIGHT: Embed | Lyrics. Lyrics scroll with whichever audio is playing. -->
    <v-col cols="12" md="6">
      <v-tabs v-model="rightTab" density="compact" class="mb-2">
        <v-tab value="embed" prepend-icon="mdi-youtube">Embed</v-tab>
        <v-tab value="lyrics" prepend-icon="mdi-script-text-outline">Lyrics</v-tab>
      </v-tabs>
      <v-window v-model="rightTab">
        <v-window-item value="embed">
          <iframe v-if="current.yt_id" width="100%" style="border:0;border-radius:12px;height:min(70vh,520px)"
            :src="youtubeEmbed(current.yt_id)" allow="encrypted-media" allowfullscreen />
          <v-alert v-else type="warning" variant="tonal" density="compact">no YouTube id</v-alert>
        </v-window-item>
        <v-window-item value="lyrics">
          <div class="d-flex align-center mb-1">
            <v-chip v-if="lyricsState.synced" size="x-small" color="primary" variant="tonal">synced · scrolls with playback</v-chip>
            <v-spacer />
            <v-btn icon="mdi-refresh" variant="text" size="small" :loading="lyricsState.loading" aria-label="Re-fetch lyrics online" @click="loadLyrics(true)" />
          </div>
          <v-progress-linear v-if="lyricsState.loading" indeterminate color="primary" class="mb-2" />
          <v-alert v-if="lyricsState.error" type="error" density="compact" variant="tonal">{{ lyricsState.error }}</v-alert>
          <template v-else>
            <LyricsView :text="lyricsState.lyrics" :current-time="playTime" editable @save="onSaveLyrics" @error="error = $event" />
            <div v-if="!lyricsState.loading && !lyricsState.found" class="text-medium-emphasis text-body-2 mt-1">No lyrics found — use the pencil to add them, or Refresh to search online.</div>
          </template>
        </v-window-item>
      </v-window>
    </v-col>
  </v-row>

  <v-card v-else class="pa-12 text-center" variant="tonal">
    <v-icon size="48" color="success" class="mb-2">mdi-check-all</v-icon>
    <div class="text-h6">Nothing to review in “{{ status }}”.</div>
    <div class="text-medium-emphasis">Pick another filter.</div>
  </v-card>

  <ActionMenu v-model="ytMenu.open" :target="ytMenu.target" :items="ytMenu.items" @select="(mode) => { onYtMenu(mode, ytMenu.row); ytMenu.open = false }" />
  <ActionMenu v-model="fileMenu.open" :target="fileMenu.target" :items="fileMenu.items" @select="(mode) => { onFileMenu(mode, fileMenu.row, fileMenu.source); fileMenu.open = false }" />
  <ActionMenu v-model="statusMenu.open" :target="statusMenu.target" :items="STATUS_MENU_ITEMS" @select="(mode) => { statusAction(mode, statusMenu.row); statusMenu.open = false }" />
  <ActionMenu v-model="memberMenu.open" :target="memberMenu.target" :items="memberMenu.items" @select="(mode) => { onMemberMenu(mode); memberMenu.open = false }" />
  <SearchPickerDialog v-model="picker.open" :mode="picker.mode" :title="picker.title" :initial-query="picker.query" :artist="picker.artist" :song-title="picker.songTitle" @pick="onPick" />
  <ForceSetDialog :state="fset" @update:open="fset.open = $event" @update:value="setForceValue" @apply="applyForceSet" />
  <InfoEditDialog v-model="info.open" :title="info.title" :data="info.data" :editable="info.editable" @save="saveInfo" @error="error = $event" />
  <LyricsDialog v-model="lyrics.open" :kind="lyrics.kind" :id="lyrics.id" :title="lyrics.title" />
  <!-- Approval checklist modal: mark verified-correct parts, then confirm the decision. -->
  <v-dialog v-model="decideDialog.open" max-width="420">
    <v-card>
      <v-card-title class="d-flex align-center ga-2">
        <v-icon :color="decideDialog.approve ? 'success' : 'error'">{{ decideDialog.approve ? 'mdi-check' : 'mdi-close' }}</v-icon>
        {{ decideDialog.approve ? 'Approve' : 'Reject' }} — checklist
      </v-card-title>
      <v-card-text>
        <div class="text-body-2 text-medium-emphasis mb-2">Mark the parts you verified as correct (recorded with the decision).</div>
        <v-checkbox v-model="checklist.youtube" :disabled="!current?.yt_id" label="YouTube" color="success" density="compact" hide-details />
        <v-checkbox v-model="checklist.local" :disabled="!current?.has_local" label="Local file" color="success" density="compact" hide-details />
        <v-checkbox v-model="checklist.lyrics" :disabled="!lyricsState.found" label="Lyrics" color="success" density="compact" hide-details />
        <v-checkbox v-model="checklist.metadata" :disabled="!hasMetadata" label="Metadata" color="success" density="compact" hide-details />
        <div v-if="decideDialog.approve && !canApprove" class="text-caption text-warning mt-2">Check YouTube to approve.</div>
      </v-card-text>
      <v-card-actions>
        <v-spacer />
        <v-btn variant="text" @click="decideDialog.open = false">Cancel</v-btn>
        <v-btn :color="decideDialog.approve ? 'success' : 'error'" variant="tonal"
          :disabled="decideDialog.approve && !canApprove" @click="confirmDecide">
          {{ decideDialog.approve ? 'Approve' : 'Reject' }}
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
  <InfoDialog v-model="fileInfo" />
  <FormatDialog :model-value="dlFmt.open" :busy="dlFmt.busy" title="Download audio" @update:model-value="dlFmt.open = $event" @pick="chooseFormat" />
  <TypedConfirmDialog :model-value="!!deletePreview" :title="`Delete ${deletePreview?.targets.length} approved local file?`" :loading="deleteBusy" @update:model-value="(v) => { if (!v) deletePreview = null }" @confirm="confirmLocal">
    <p>Deletes the approved local file for this track on disk. No arbitrary path can be entered.</p>
    <v-list density="compact" class="my-3"><v-list-item v-for="target in deletePreview?.targets || []" :key="target.track_id" :title="target.relative_path" /></v-list>
  </TypedConfirmDialog>
  <ConfirmDialog :model-value="!!downloadConfirm" title="Delete downloaded file?" confirm-label="Delete" :max-width="440" @update:model-value="(v) => { if (!v) downloadConfirm = null }" @confirm="confirmDownloadDelete">
    Deletes the file in your download folder. The Library entry and any mp3-folder file stay.
  </ConfirmDialog>
  <ConfirmDialog :model-value="!!libRemoveConfirm" title="Remove from Library?" confirm-label="Remove" @update:model-value="(v) => { if (!v) libRemoveConfirm = null }" @confirm="async () => { const id = libRemoveConfirm.trackId; libRemoveConfirm = null; try { await api.libraryRemove([id]); invalidateData(); await refreshCurrent() } catch (e) { error.value = String(e) } }">
    Deletes the Library entry and any downloaded file(s). Your mp3-folder files are not touched.
  </ConfirmDialog>
</template>

<style scoped>
.metric { font-size: .85rem; font-weight: 600; white-space: nowrap; }
.metric-label {
  font-size: .62rem; text-transform: uppercase; letter-spacing: .05em;
  opacity: .55; margin-right: 5px; font-weight: 500;
}
</style>
