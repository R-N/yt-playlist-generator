<script setup>
import { computed, ref, watch } from 'vue'
import { api } from './api'
import { invalidateData } from './nav'
import { parsePaste } from './import'
import { useSelection } from './curation'

const flow = ref('paste')
const untracked = ref([])
const { selected: selectedFiles, toggle } = useSelection()   // fkey list
const previewKey = ref(null)
const filesBusy = ref('')
const fileNotice = ref('')
const fkey = (file) => `${file.folder_identity}|${file.relative_path}`
const toggleFile = (file) => toggle(fkey(file))
const selectedFileRefs = computed(() => untracked.value.filter((file) => selectedFiles.value.includes(fkey(file)))
  .map((file) => ({ folder_identity: file.folder_identity, relative_path: file.relative_path })))
async function loadUntracked() {
  try { untracked.value = (await api.localFiles()).files.filter((file) => !file.tracks?.length) }
  catch (e) { error.value = String(e) }
}
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
          <template v-if="selectedFiles.length">
            <v-chip size="small" variant="tonal">{{ selectedFiles.length }} selected</v-chip>
            <v-btn size="small" variant="tonal" color="primary" :loading="filesBusy === 'add'" prepend-icon="mdi-plus-box-multiple" @click="addFiles(selectedFileRefs)">Add to Library</v-btn>
            <v-btn size="small" variant="tonal" color="primary" :loading="filesBusy === 'send'" prepend-icon="mdi-send" @click="sendFiles(selectedFileRefs)">Send to Workspace</v-btn>
          </template>
          <v-btn size="small" variant="text" icon="mdi-refresh" aria-label="Rescan" @click="loadUntracked" />
        </div>
        <v-alert v-if="fileNotice" type="info" variant="tonal" closable class="mb-3" @click:close="fileNotice = ''">{{ fileNotice }}</v-alert>
        <v-list v-if="untracked.length" lines="two" density="compact">
          <template v-for="file in untracked" :key="fkey(file)">
            <v-list-item>
              <template #prepend>
                <v-checkbox-btn :model-value="selectedFiles.includes(fkey(file))" density="compact" @update:model-value="toggleFile(file)" />
              </template>
              <v-list-item-title>{{ file.basename }}</v-list-item-title>
              <v-list-item-subtitle>{{ file.relative_path }}</v-list-item-subtitle>
              <template #append>
                <v-btn icon="mdi-play-circle-outline" size="small" variant="text" aria-label="Preview" @click="previewKey = previewKey === fkey(file) ? null : fkey(file)" />
                <v-btn icon="mdi-plus-box" size="small" variant="text" color="primary" :loading="filesBusy === 'add'" aria-label="Add to Library" @click="addFiles([{ folder_identity: file.folder_identity, relative_path: file.relative_path }])" />
                <v-btn icon="mdi-send" size="small" variant="text" color="primary" :loading="filesBusy === 'send'" aria-label="Send to Workspace" @click="sendFiles([{ folder_identity: file.folder_identity, relative_path: file.relative_path }])" />
              </template>
            </v-list-item>
            <div v-if="previewKey === fkey(file)" class="px-4 pb-2">
              <audio :src="api.localAudioUrl(file.folder_identity, file.relative_path)" controls preload="none" style="width:100%" />
            </div>
          </template>
        </v-list>
        <v-empty-state v-else icon="mdi-check-all" title="No untracked files" text="Every local file already has a Library entry." />
      </v-window-item>
    </v-window>
  </v-card>

  <v-alert v-if="added" :type="added.invalid.length ? 'warning' : 'success'" variant="tonal" class="mt-4">Workspace updated: {{ added.added.length }} added, {{ added.duplicates.length }} already present, {{ added.invalid.length }} invalid.</v-alert>
  <v-alert v-if="error" type="error" closable class="mt-4" @click:close="error = ''">{{ error }}</v-alert>
</template>
