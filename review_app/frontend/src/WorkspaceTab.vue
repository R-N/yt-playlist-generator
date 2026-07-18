<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { api } from './api'
import {
  selectedItems, fileDownload, preferredRun, batchSnapshotIds, ACTIVE_RUN_STATUSES,
  duplicateMessage, skippedDuplicateCount,
  itemMeta, isDead, isEnriched, healthLabel, itemTitle, itemName, itemAuthor, formatViews, formatDuration, formatUploadDate,
  nameMatchScore,
} from './workspace'
import { buildLabels, FILTER_ATTRS, matchesLabelFilter } from './labels'
import { useLabelFilter, usePagination, useSelection, useRowActions, ytUrl, YT_MENU_ITEMS, STATUS_MENU_ITEMS, fileMenuItems } from './curation'
import CurationList from './CurationList.vue'
import LabelFilterMenu from './LabelFilterMenu.vue'
import ActionMenu from './ActionMenu.vue'
import InfoDialog from './InfoDialog.vue'

// Workspace items can't be saved-links or untracked files — hide those attrs.
const WS_FILTER_ATTRS = FILTER_ATTRS.filter((attr) => !['saved', 'untracked'].includes(attr.key))
const FILE_MENU_ITEMS = fileMenuItems()   // Workspace does not manage deletion, so no delete row
import { activeTab, invalidateData, reviewTrack, showInLibrary, useTabRefresh } from './nav'

const items = ref([])
const batches = ref([])
const dialog = ref(false)
const loading = ref(false)
const working = ref('')
const verifying = ref(false)
const error = ref('')
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
const findLocalOpen = ref(false)
const findLocalResults = ref([])
const findYtOpen = ref(false)
const findYtItems = ref([])
const ytMenu = ref({ open: false, target: [0, 0], row: null })
const fileMenu = ref({ open: false, target: [0, 0], row: null })
const statusMenu = ref({ open: false, target: [0, 0], row: null })
// Shared action logic (curation.js). Rows carry the entity-specific data below.
const { preview, fileInfo, ytAction, fileAction, statusAction } = useRowActions({
  onError: (e) => { error.value = String(e) },
  openReview: async (row) => { try { reviewTrack(await api.track(row.trackId)) } catch (e) { error.value = String(e) } },
})

const liveItems = computed(() => items.value.filter((item) => !isDead(item)))
const { selected, allSelected, toggle, toggleAll } = useSelection(computed(() => liveItems.value.map((item) => item.id)))
const chosen = computed(() => selectedItems(items.value, selected.value))
const busy = computed(() => !chosen.value.length || !!working.value)
const displayItems = computed(() => {
  const q = query.value.trim().toLowerCase()
  let list = items.value.filter((item) =>
    matchesLabelFilter(new Set(labelsFor(item).map((label) => label.key)), labelFilter.value) &&
    (!q || `${itemTitle(item)} ${item.youtube_id}`.toLowerCase().includes(q)))
  if (sortBy.value === 'title') list = [...list].sort((a, b) => itemTitle(a).localeCompare(itemTitle(b)))
  else if (sortBy.value === 'views') list = [...list].sort((a, b) => (itemMeta(b).view_count || 0) - (itemMeta(a).view_count || 0))
  return list
})
const { page, perPage, pageCount, paged: pagedItems } = usePagination(displayItems, [labelFilter, query, sortBy])
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
  fileSrc: wsLocalSrc(item), downloadSrc: null,
  revealArg: () => item.relative_path
    ? { folder_identity: item.folder_identity, relative_path: item.relative_path }
    : { track_id: item.track_id },
  infoFor: () => item.relative_path
    ? { title: 'File info', lines: [['File', item.relative_path.split(/[\\/]/).pop()], ['Path', item.relative_path]] }
    : { title: 'File info', lines: [['File', item.track_filename || '—'], ['Artist', item.track_artist || '—'], ['Title', item.track_title || '—'], ['Local files', String(item.local_count || 0)]] },
})))

async function load() {
  loading.value = true
  try {
    items.value = (await api.workspace()).items
    const runs = (await api.workspaceRuns()).runs
    const durableRun = preferredRun(runs)
    run.value = durableRun ? await api.workspaceRun(durableRun.id) : null
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

async function removeItems(ids) {
  if (!ids.length) return
  try { await api.workspaceRemove(ids); selected.value = selected.value.filter((id) => !ids.includes(id)); invalidateData(); await load() }
  catch (e) { error.value = String(e) }
}
async function saveToLibrary(ids = selected.value) {
  await act('Saving', async () => { await api.workspaceSaveLinks(ids); invalidateData() })
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
  return buildLabels({
    aliveLink: isEnriched(item) && !isDead(item), deadLink: isDead(item),
    localCount: item.local_count || (item.relative_path ? 1 : 0),
    untracked: !!item.relative_path, check: item.track_check,
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
  if (label.key === 'youtube' || label.key === 'dead') ytMenu.value = { open: true, target, row }
  else if (label.key === 'local' || label.key === 'untracked') fileMenu.value = { open: true, target, row }
  else if (label.key === 'confirmed' || label.key === 'rejected') statusMenu.value = { open: true, target, row }
}
function itemSearchName(item) { return itemMeta(item).title || itemTitle(item) || item.youtube_id }
async function findLocal() {
  finding.value = 'local'; error.value = ''
  try {
    const files = (await api.localFiles()).files
    findLocalResults.value = chosen.value.map((item) => ({
      item, matches: files
        .map((file) => ({ file, score: nameMatchScore(itemSearchName(item), file.basename) }))
        .filter((match) => match.score >= 2)
        .sort((a, b) => b.score - a.score)
        .slice(0, 5),
    }))
    findLocalOpen.value = true
  } catch (e) { error.value = String(e) } finally { finding.value = '' }
}
function findYoutube() {
  findYtItems.value = chosen.value.filter((item) => isDead(item) || !item.youtube_id)
  findYtOpen.value = true
}
function ytSearchUrl(item) { return 'https://www.youtube.com/results?search_query=' + encodeURIComponent(itemSearchName(item)) }
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
    :closable="run.status === 'done' || run.status === 'failed'" @click:close="run = null">
    Audio download: <strong>{{ run.status }}</strong>. {{ run.error_text || `${run.items?.length || 0} snapshotted items` }}
  </v-alert>
  <v-alert v-if="batchDuplicates" type="warning" variant="tonal" class="mb-4">{{ duplicateMessage('Playlist', batchDuplicates) }}</v-alert>
  <v-alert v-if="exportDuplicates" type="warning" variant="tonal" class="mb-4">{{ duplicateMessage('Download', exportDuplicates) }}</v-alert>
  <v-alert v-if="runDuplicates" type="warning" variant="tonal" class="mb-4">{{ duplicateMessage('Audio download', runDuplicates) }}</v-alert>
  <v-alert v-if="error" type="error" closable class="mb-4" @click:close="error=''">{{ error }}</v-alert>

  <v-toolbar density="comfortable" class="mb-3 px-2 rounded-lg" color="surface" border>
    <v-checkbox-btn :model-value="allSelected" aria-label="Select all live items" @update:model-value="toggleAll" />
    <v-btn icon variant="text" :loading="finding === 'local'" :disabled="busy" aria-label="Find local file" @click="findLocal">
      <v-icon>mdi-folder-search-outline</v-icon><v-tooltip activator="parent" location="bottom">Find local file</v-tooltip>
    </v-btn>
    <v-btn icon variant="text" :disabled="busy" aria-label="Find YouTube link" @click="findYoutube">
      <v-icon>mdi-link-variant-plus</v-icon><v-tooltip activator="parent" location="bottom">Find YouTube link (dead)</v-tooltip>
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
    <template #actions="{ row }">
      <v-menu>
        <template #activator="{ props }">
          <v-btn icon="mdi-dots-vertical" size="small" variant="text" aria-label="Item actions" v-bind="props" />
        </template>
        <v-list density="compact" min-width="180">
          <template v-if="row.raw.youtube_url && !row.dead">
            <v-list-item prepend-icon="mdi-open-in-new" title="Open in new tab" :href="row.raw.youtube_url" target="_blank" rel="noopener noreferrer" />
            <v-list-item prepend-icon="mdi-content-copy" title="Copy link" @click="copy(row.raw.youtube_url)" />
            <v-list-item prepend-icon="mdi-bookmark-plus-outline" title="Save to Library" @click="saveToLibrary([row.key])" />
          </template>
          <v-list-item v-if="!row.raw.youtube_url || row.dead" prepend-icon="mdi-magnify" title="Find YouTube link" :href="ytSearchUrl(row.raw)" target="_blank" rel="noopener noreferrer" />
          <v-list-item v-if="row.raw.track_id" prepend-icon="mdi-library" title="Show in library" @click="showInLibrary([row.raw.track_id])" />
          <v-list-item prepend-icon="mdi-delete-outline" title="Remove" base-color="error" @click="removeItems([row.key])" />
        </v-list>
      </v-menu>
    </template>
    <template #empty>
      <v-empty-state icon="mdi-youtube-play" title="Workspace is empty" text="Open Import to add YouTube IDs, or send exact tracks from Library." action-text="Open Import" @click:action="activeTab = 'import'" />
    </template>
  </CurationList>

  <ActionMenu v-model="ytMenu.open" :target="ytMenu.target" :items="YT_MENU_ITEMS" @select="(mode) => { ytAction(mode, ytMenu.row); ytMenu.open = false }" />
  <ActionMenu v-model="fileMenu.open" :target="fileMenu.target" :items="FILE_MENU_ITEMS" @select="(mode) => { fileAction(mode, fileMenu.row); fileMenu.open = false }" />
  <ActionMenu v-model="statusMenu.open" :target="statusMenu.target" :items="STATUS_MENU_ITEMS" @select="(mode) => { statusAction(mode, statusMenu.row); statusMenu.open = false }" />
  <InfoDialog v-model="fileInfo" />

  <v-dialog v-model="findLocalOpen" max-width="720" scrollable>
    <v-card>
      <v-card-title class="d-flex align-center">Find local file<v-spacer /><v-btn icon="mdi-close" variant="text" size="small" aria-label="Close" @click="findLocalOpen = false" /></v-card-title>
      <v-divider />
      <v-card-text>
        <div class="text-caption text-medium-emphasis mb-3">Name-overlap matches in your configured folders. Discovery only — no linking applied yet.</div>
        <div v-for="row in findLocalResults" :key="row.item.id" class="mb-4">
          <div class="font-weight-medium">{{ itemTitle(row.item) }}</div>
          <v-list v-if="row.matches.length" density="compact">
            <v-list-item v-for="match in row.matches" :key="`${match.file.folder_identity}-${match.file.relative_path}`" :title="match.file.basename" :subtitle="match.file.relative_path" />
          </v-list>
          <div v-else class="text-caption text-medium-emphasis">No local file matched.</div>
        </div>
        <div v-if="!findLocalResults.length" class="text-medium-emphasis">Select items first.</div>
      </v-card-text>
    </v-card>
  </v-dialog>

  <v-dialog v-model="findYtOpen" max-width="620" scrollable>
    <v-card>
      <v-card-title class="d-flex align-center">Find YouTube link<v-spacer /><v-btn icon="mdi-close" variant="text" size="small" aria-label="Close" @click="findYtOpen = false" /></v-card-title>
      <v-divider />
      <v-card-text>
        <div class="text-caption text-medium-emphasis mb-3">Dead links among the selection (alive links skipped). Verify links first if health is unknown.</div>
        <v-list v-if="findYtItems.length" density="compact" lines="two">
          <v-list-item v-for="item in findYtItems" :key="item.id" :title="itemTitle(item)" :subtitle="healthLabel(item) || 'No YouTube link'">
            <template #append><v-btn size="small" variant="tonal" :href="ytSearchUrl(item)" target="_blank" rel="noopener noreferrer" append-icon="mdi-open-in-new">Search YouTube</v-btn></template>
          </v-list-item>
        </v-list>
        <div v-else class="text-medium-emphasis">No dead links in the current selection.</div>
      </v-card-text>
    </v-card>
  </v-dialog>

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

