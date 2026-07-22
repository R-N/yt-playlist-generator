<script setup>
import { computed, ref, watch } from 'vue'
import { api } from './api'
import { invalidateData } from './nav'
import { parsePaste } from './import'
import { useSelection, usePagination, useRowActions, useFilePicker, fileMenuItems, untrackedMenuItems, withNewBase } from './curation'
import { buildLabels } from './labels'
import CurationList from './CurationList.vue'
import ActionMenu from './ActionMenu.vue'
import InfoDialog from './InfoDialog.vue'

const flow = ref('paste')
const untracked = ref([])
const { selected: selectedFiles, toggle } = useSelection()   // fkey list
const filesBusy = ref('')
const fileNotice = ref('')
const fkey = (file) => `${file.folder_identity}|${file.relative_path}`
const selectedFileRefs = computed(() => untracked.value.filter((file) => selectedFiles.value.includes(fkey(file)))
  .map((file) => ({ folder_identity: file.folder_identity, relative_path: file.relative_path })))

// Same list look AND same action logic as Workspace/Library. Untracked files
// only need the file menu (play/info/reveal); no yt/status/delete here.
const { page, perPage, pageCount, paged } = usePagination(untracked)
const listRows = computed(() => paged.value.map((file) => ({
  key: fkey(file), raw: file, selectable: true,
  title: file.basename, subtitle: file.relative_path,
  labels: buildLabels({ localCount: 1, untracked: true }),
  fileSrc: api.localAudioUrl(file.folder_identity, file.relative_path),
  revealArg: () => ({ folder_identity: file.folder_identity, relative_path: file.relative_path }),
  infoFor: () => ({ title: 'File info', lines: [['File', file.basename], ['Path', file.relative_path]] }),
  onRenamed: (name) => { file.basename = name; file.relative_path = withNewBase(file.relative_path, name) },   // patch in place, no reload
})))
const FILE_MENU_ITEMS = fileMenuItems()   // no delete row for untracked files
const fileMenu = ref({ open: false, target: [0, 0], row: null })
const untrackedMenu = ref({ open: false, target: [0, 0], row: null })
const { preview, fileInfo, fileAction } = useRowActions({ onError: (e) => { error.value = String(e) }, onNotice: (m) => { fileNotice.value = m }, reload: () => loadUntracked() })
// 'untracked' badge -> add/send; other badges (local file) -> play/info/reveal.
function onLabel(row, label, ev) {
  const target = [ev.clientX, ev.clientY]
  if (label.key === 'untracked') untrackedMenu.value = { open: true, target, row }
  else fileMenu.value = { open: true, target, row }
}
function untrackedAction(mode) {
  const row = untrackedMenu.value.row; untrackedMenu.value.open = false
  if (!row) return
  const refs = [{ folder_identity: row.raw.folder_identity, relative_path: row.raw.relative_path }]
  if (mode === 'add') addFiles(refs)
  else if (mode === 'send') sendFiles(refs)
}
async function loadUntracked() {
  try {
    const [files, staged, ws] = await Promise.all([api.localFiles(), api.stagedFiles(), api.workspace()])
    // A file already staged in Workspace leaves the untracked list (it's been imported).
    const inWs = new Set(ws.items.filter((it) => it.relative_path).map((it) => `${it.folder_identity}|${it.relative_path}`))
    const catalog = files.files.filter((file) => !file.tracks?.length && !inWs.has(fkey(file)))
    const seen = new Set(catalog.map(fkey))
    untracked.value = [...catalog, ...staged.files.filter((file) => !seen.has(fkey(file)) && !inWs.has(fkey(file)))]
  } catch (e) { error.value = String(e) }
}
// Import stages picked files in server memory as untracked-only (see /api/files/add).
const { picking, pickFiles } = useFilePicker({
  target: 'untracked',
  onDone: (r) => { fileNotice.value = `Staged ${r.added} untracked file${r.added === 1 ? '' : 's'}.`; loadUntracked() },
  onError: (e) => { error.value = String(e) },
})
async function addFiles(refs) {
  if (!refs.length) return
  filesBusy.value = 'add'; error.value = ''; fileNotice.value = ''
  try { const r = await api.addFilesToLibrary(refs); fileNotice.value = `Added ${r.added} of ${refs.length} to Library.`; selectedFiles.value = []; invalidateData(); await loadUntracked() }
  catch (e) { error.value = String(e) } finally { filesBusy.value = '' }
}
async function sendFiles(refs) {
  if (!refs.length) return
  filesBusy.value = 'send'; error.value = ''; fileNotice.value = ''
  try { const r = await api.workspaceAddFiles(refs); fileNotice.value = `Sent ${r.added} of ${refs.length} to Workspace.`; selectedFiles.value = []; invalidateData(); await loadUntracked() }
  catch (e) { error.value = String(e) } finally { filesBusy.value = '' }
}
watch(flow, (value) => { if (value === 'files') loadUntracked() })
const text = ref('')
const channelId = ref('')
const author = ref('')
const writeFiles = ref(true)
const loading = ref(false)
const error = ref('')
const result = ref(null)
const added = ref(null)
const parsed = computed(() => flow.value === 'paste' ? parsePaste(text.value) : [])
const validIds = computed(() => flow.value === 'paste' ? parsed.value.filter((item) => item.status === 'valid').map((item) => item.youtube_id) : (result.value?.ids || []))
const invalid = computed(() => parsed.value.filter((item) => item.status === 'invalid'))
const duplicates = computed(() => parsed.value.filter((item) => item.status === 'duplicate'))

async function harvest() {
  loading.value = true; error.value = ''; result.value = null; added.value = null
  try { result.value = await api.discordFetch({ channel_id: channelId.value || null, author: author.value || null, write_files: writeFiles.value }) }
  catch (e) { error.value = String(e) }
  finally { loading.value = false }
}
async function doImport() {
  if (!validIds.value.length) return
  loading.value = true; error.value = ''
  try { added.value = await api.workspaceImport(validIds.value.join('\n')); invalidateData() }
  catch (e) { error.value = String(e) }
  finally { loading.value = false }
}
function reset() { result.value = null; added.value = null }
function copy(value) { navigator.clipboard?.writeText(value) }
</script>

<template>
  <div class="text-h4 mb-4">Import</div>

  <v-card>
    <v-tabs v-model="flow" color="primary" grow>
      <v-tab value="paste" prepend-icon="mdi-link-variant">Paste YouTube</v-tab>
      <v-tab value="discord" prepend-icon="mdi-forum-outline">Discord</v-tab>
      <v-tab value="files" prepend-icon="mdi-file-question-outline">Untracked files</v-tab>
    </v-tabs>
    <v-divider />
    <v-window v-model="flow" @update:model-value="reset">
      <v-window-item value="paste" class="pa-5">
        <v-textarea v-model="text" label="YouTube URLs or 11-character IDs" rows="7" auto-grow variant="outlined" hide-details placeholder="https://www.youtube.com/watch?v=dQw4w9WgXcQ&#10;dQw4w9WgXcQ" />
        <div class="d-flex align-center flex-wrap ga-2 mt-3">
          <v-chip size="small" color="success" variant="tonal">{{ validIds.length }} valid</v-chip>
          <v-chip size="small" :color="duplicates.length ? 'warning' : undefined" variant="tonal">{{ duplicates.length }} duplicate</v-chip>
          <v-chip size="small" :color="invalid.length ? 'error' : undefined" variant="tonal">{{ invalid.length }} invalid</v-chip>
          <v-spacer />
          <v-btn color="primary" :loading="loading" :disabled="!validIds.length" prepend-icon="mdi-import" @click="doImport">Import</v-btn>
        </div>
        <v-expansion-panels v-if="invalid.length || duplicates.length" variant="accordion" class="mt-4">
          <v-expansion-panel v-if="duplicates.length" :title="`${duplicates.length} duplicate`"><v-expansion-panel-text><div v-for="item in duplicates" :key="item.input">{{ item.input }}</div></v-expansion-panel-text></v-expansion-panel>
          <v-expansion-panel v-if="invalid.length" :title="`${invalid.length} invalid`"><v-expansion-panel-text><div v-for="item in invalid" :key="item.input">{{ item.input }}</div></v-expansion-panel-text></v-expansion-panel>
        </v-expansion-panels>
      </v-window-item>

      <v-window-item value="discord" class="pa-5">
        <v-row>
          <v-col cols="12" md="6"><v-text-field v-model="channelId" label="Channel ID" hint="Blank uses saved default" persistent-hint /></v-col>
          <v-col cols="12" md="6"><v-text-field v-model="author" label="Author filter (optional)" hint="Blank includes everyone" persistent-hint /></v-col>
        </v-row>
        <v-switch v-model="writeFiles" color="primary" label="Also write pipeline files (ids.txt, urls.txt, playlists.txt)" hide-details />
        <div class="d-flex justify-end mt-4"><v-btn color="primary" variant="tonal" :loading="loading" prepend-icon="mdi-cloud-download" @click="harvest">Fetch and extract</v-btn></div>
        <v-alert v-if="result" type="success" variant="tonal" class="mt-4">Scanned {{ result.messages }} messages · found <strong>{{ result.count }}</strong> unique video IDs.</v-alert>
        <v-card v-if="result?.ids?.length" variant="tonal" class="mt-4 pa-4">
          <div class="d-flex align-center mb-2"><div class="text-subtitle-2">Extracted IDs ({{ result.ids.length }})</div><v-spacer /><v-btn size="small" variant="text" prepend-icon="mdi-content-copy" @click="copy(result.ids.join('\n'))">Copy</v-btn></div>
          <v-textarea :model-value="result.ids.join('\n')" readonly rows="6" density="compact" hide-details variant="outlined" />
          <v-btn class="mt-4" color="primary" :loading="loading" prepend-icon="mdi-import" @click="doImport">Import {{ validIds.length }}</v-btn>
        </v-card>
      </v-window-item>

      <v-window-item value="files" class="pa-5">
        <div class="d-flex align-center mb-3 flex-wrap ga-2">
          <div class="text-body-2 text-medium-emphasis">Local files with no Library entry.</div>
          <v-spacer />
          <v-btn size="small" variant="tonal" :loading="picking" prepend-icon="mdi-file-plus-outline" @click="pickFiles">Add files</v-btn>
          <template v-if="selectedFiles.length">
            <v-chip size="small" variant="tonal">{{ selectedFiles.length }} selected</v-chip>
            <v-btn size="small" icon variant="text" color="primary" :loading="filesBusy === 'add'" aria-label="Add to Library" @click="addFiles(selectedFileRefs)"><v-icon>mdi-plus-box-multiple</v-icon><v-tooltip activator="parent" location="bottom">Add {{ selectedFiles.length }} to Library</v-tooltip></v-btn>
            <v-btn size="small" icon variant="text" color="primary" :loading="filesBusy === 'send'" aria-label="Send to Workspace" @click="sendFiles(selectedFileRefs)"><v-icon>mdi-send</v-icon><v-tooltip activator="parent" location="bottom">Send {{ selectedFiles.length }} to Workspace</v-tooltip></v-btn>
          </template>
          <v-btn size="small" variant="text" icon="mdi-refresh" aria-label="Rescan" @click="loadUntracked" />
        </div>
        <v-alert v-if="fileNotice" type="info" variant="tonal" closable class="mb-3" @click:close="fileNotice = ''">{{ fileNotice }}</v-alert>
        <CurationList v-if="untracked.length"
          :rows="listRows" :selected="selectedFiles" :preview-for="preview.previewFor"
          v-model:page="page" v-model:per-page="perPage" :page-count="pageCount" :has-items="untracked.length > 0"
          @toggle="(row) => toggle(row.key)" @label="onLabel" />
        <v-empty-state v-else icon="mdi-check-all" title="No untracked files" text="Every local file already has a Library entry." />
        <ActionMenu v-model="fileMenu.open" :target="fileMenu.target" :items="FILE_MENU_ITEMS" @select="(mode) => { fileAction(mode, fileMenu.row); fileMenu.open = false }" />
        <ActionMenu v-model="untrackedMenu.open" :target="untrackedMenu.target" :items="untrackedMenuItems()" @select="untrackedAction" />
        <InfoDialog v-model="fileInfo" />
      </v-window-item>
    </v-window>
  </v-card>

  <v-alert v-if="added" :type="added.invalid.length ? 'warning' : 'success'" variant="tonal" class="mt-4">Workspace updated: {{ added.added.length }} added, {{ added.duplicates.length }} already present, {{ added.invalid.length }} invalid.</v-alert>
  <v-alert v-if="error" type="error" closable class="mt-4" @click:close="error = ''">{{ error }}</v-alert>
</template>
