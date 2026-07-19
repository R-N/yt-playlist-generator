<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { api } from './api'
import { invalidateData, useTabRefresh } from './nav'
import TypedConfirmDialog from './TypedConfirmDialog.vue'
import ConfirmDialog from './ConfirmDialog.vue'

const state = ref({})
const form = ref({ DISCORD_BOT_TOKEN: '', DISCORD_CHANNEL_ID: '', ACOUSTID_API_KEY: '' })
const folders = ref([])
const picking = ref(false)
const downloadFolder = ref('')
const pickingDownload = ref(false)
const linkFinding = ref({ YT_SEARCH_TOP_N: '', TASK_DELAY_MIN: '', TASK_DELAY_MAX: '', YT_MIN_SCORE: '', LOCAL_MIN_SCORE: '', SEARCH_RESULT_LIMIT: '' })
const advanced = ref({ DELETE_TOKEN_TTL: '', CLEANUP_EXTENSIONS: '' })
const saving = ref(false)
const saved = ref(false)
const error = ref('')
const cleanup = ref(null)
const cleanupResult = ref(null)
const scripts = ref([])
const scriptStates = ref({})
const maintenanceConfirm = ref(null)
let poll = null
const advancedScripts = computed(() => scripts.value.filter((script) =>
  ['searcher', 'filter_local_quality', 'acoustid_enrich', 'check_untracked'].includes(script.name)))
const SCRIPT_LABELS = {
  searcher: 'Match local library', filter_local_quality: 'Check local quality', acoustid_enrich: 'Cross-check AcoustID',
  check_untracked: 'Refresh untracked list', cleanup_tracked: 'Delete verified sources',
  url_extractor: 'Extract dump.csv', playlist_generator: 'Build playlist links', downloader: 'Download ids.txt',
}

async function load() {
  try {
    state.value = await api.getSettings()
    form.value.DISCORD_CHANNEL_ID = state.value.DISCORD_CHANNEL_ID?.preview || ''
    try { const list = JSON.parse(state.value.MP3_FOLDERS_JSON?.preview || '[]'); folders.value = Array.isArray(list) ? list : [] } catch { folders.value = [] }
    downloadFolder.value = state.value.DOWNLOAD_FOLDER?.preview || ''
    for (const k of Object.keys(linkFinding.value)) linkFinding.value[k] = state.value[k]?.preview || ''
    for (const k of Object.keys(advanced.value)) advanced.value[k] = state.value[k]?.preview || ''
    scripts.value = await api.scripts()
    await Promise.all(scripts.value.map(async (script) => { scriptStates.value[script.name] = await api.scriptState(script.name, 80) }))
  } catch (e) { error.value = String(e) }
}
async function save() {
  saving.value = true; saved.value = false; error.value = ''
  const body = {}
  for (const [key, value] of Object.entries(form.value)) if (value !== '') body[key] = value
  try { state.value = await api.saveSettings(body); form.value.DISCORD_BOT_TOKEN = ''; form.value.ACOUSTID_API_KEY = ''; saved.value = true }
  catch (e) { error.value = String(e) }
  finally { saving.value = false }
}
async function saveLinkFinding() {
  saving.value = true; error.value = ''; saved.value = false
  try { state.value = await api.saveSettings({ ...linkFinding.value }); saved.value = true }
  catch (e) { error.value = String(e) }
  finally { saving.value = false }
}
async function saveAdvanced() {
  saving.value = true; error.value = ''; saved.value = false
  try { state.value = await api.saveSettings({ ...advanced.value }); saved.value = true }
  catch (e) { error.value = String(e) }
  finally { saving.value = false }
}
function removeFolder(index) { folders.value.splice(index, 1) }
function addManual() { folders.value.push('') }
async function addFolder() {
  picking.value = true; error.value = ''
  try {
    const { path } = await api.pickFolder()
    if (path && !folders.value.includes(path)) folders.value.push(path)
  } catch (e) { error.value = 'Native folder picker unavailable — add the path manually. (' + e + ')' }
  finally { picking.value = false }
}
async function pickDownloadFolder() {
  pickingDownload.value = true; error.value = ''
  try { const { path } = await api.pickFolder(); if (path) downloadFolder.value = path }
  catch (e) { error.value = 'Native folder picker unavailable — type the path. (' + e + ')' }
  finally { pickingDownload.value = false }
}
async function saveDownloadFolder() {
  saving.value = true; error.value = ''; saved.value = false
  try { state.value = await api.saveSettings({ DOWNLOAD_FOLDER: downloadFolder.value }); saved.value = true; invalidateData() }
  catch (e) { error.value = String(e) }
  finally { saving.value = false }
}
async function rescan() {
  const values = folders.value.map((folder) => folder.trim()).filter(Boolean)
  if (!values.length) { emptyFoldersConfirm.value = true; return }
  await saveFolders(values)
}
const emptyFoldersConfirm = ref(false)
async function saveFolders(values) {
  saving.value = true; error.value = ''; saved.value = false
  try { state.value = await api.saveSettings({ MP3_FOLDERS_JSON: values }); saved.value = true; invalidateData() }
  catch (e) { error.value = String(e) }
  finally { saving.value = false }
}
function confirmEmptyFolders() { emptyFoldersConfirm.value = false; saveFolders([]) }
async function previewCleanup() {
  try { cleanup.value = await api.cleanupPreview() }
  catch (e) { error.value = String(e) }
}
async function runCleanup() {
  if (!cleanup.value) return
  try { cleanupResult.value = await api.cleanupDownloads({ token: cleanup.value.token, confirm: 'DELETE' }); cleanup.value = null; invalidateData() }
  catch (e) { error.value = String(e); cleanup.value = null; invalidateData() }
}
async function runMaintenance(script) {
  if (script.destructive) { maintenanceConfirm.value = script; return }
  await executeMaintenance(script)
}
async function executeMaintenance(script) {
  maintenanceConfirm.value = null
  try { scriptStates.value[script.name] = await api.scriptRun(script.name); invalidateData() }
  catch (e) { error.value = String(e) }
}
function confirmMaintenance() { executeMaintenance(maintenanceConfirm.value) }
function scriptStatus(name) { return scriptStates.value[name]?.status || 'idle' }
function scriptLabel(name) { return SCRIPT_LABELS[name] || name }
onMounted(() => { load(); poll = setInterval(async () => {
  for (const script of scripts.value) {
    const before = scriptStatus(script.name)
    if (!['running', 'stopping', 'finalizing'].includes(before)) continue
    const next = await api.scriptState(script.name, 80)
    scriptStates.value[script.name] = next
    if (!['running', 'stopping', 'finalizing'].includes(next.status)) invalidateData()
  }
}, 1500) })
onUnmounted(() => clearInterval(poll))
useTabRefresh('settings', load)
</script>

<template>
  <v-container max-width="760">
    <div class="text-h4 mb-1">Settings</div>
    <div class="text-body-2 text-medium-emphasis mb-5">Secrets stay masked. Music folders are validated before local catalog is replaced.</div>
    <v-alert v-if="error" type="error" closable class="mb-4" @click:close="error=''">{{ error }}</v-alert>

    <v-card variant="outlined" class="pa-4 mb-5">
      <div class="text-subtitle-1 mb-1">Music folders</div>
      <div class="text-caption text-medium-emphasis mb-3">Absolute, existing, non-overlapping folders. Pick with the OS dialog or edit inline. Rescan updates Library and Local Files.</div>
      <div v-if="!folders.length" class="text-caption text-medium-emphasis mb-3">No folders configured — local features are off until you add one.</div>
      <div v-for="(folder, index) in folders" :key="index" class="d-flex align-center ga-2 mb-2">
        <v-text-field v-model="folders[index]" density="compact" variant="outlined" hide-details prepend-inner-icon="mdi-folder-outline" placeholder="C:\\Music" />
        <v-btn icon="mdi-close" variant="text" size="small" aria-label="Remove folder" @click="removeFolder(index)" />
      </div>
      <div class="d-flex flex-wrap ga-2 mt-3">
        <v-btn variant="tonal" :loading="picking" prepend-icon="mdi-folder-plus-outline" @click="addFolder">Add folder</v-btn>
        <v-btn variant="text" prepend-icon="mdi-pencil-plus-outline" @click="addManual">Add manually</v-btn>
        <v-spacer />
        <v-btn color="primary" :loading="saving" prepend-icon="mdi-folder-search" @click="rescan">Save and rescan</v-btn>
      </div>
    </v-card>

    <v-card variant="outlined" class="pa-4 mb-5">
      <div class="text-subtitle-1 mb-1">Download folder</div>
      <div class="text-caption text-medium-emphasis mb-3">Where audio downloads are saved. Failed-download cleanup operates on this folder. Absolute, existing. Blank = repo <code>downloads/</code>.</div>
      <div class="d-flex align-center ga-2">
        <v-text-field v-model="downloadFolder" density="compact" variant="outlined" hide-details prepend-inner-icon="mdi-folder-download-outline" placeholder="E:\\Music\\downloads" />
        <v-btn variant="tonal" :loading="pickingDownload" prepend-icon="mdi-folder-plus-outline" @click="pickDownloadFolder">Pick</v-btn>
        <v-btn color="primary" :loading="saving" @click="saveDownloadFolder">Save</v-btn>
      </div>
    </v-card>

    <v-card variant="outlined" class="pa-4 mb-5">
      <div class="text-subtitle-1 mb-1">Link finding</div>
      <div class="text-caption text-medium-emphasis mb-3">Auto find-YouTube / find-local tasks. Top-N = how many search hits to score before picking the best. Delay paces network fetches (seconds, randomized between min and max) to dodge rate limits. Min scores are the lowest match score an auto-find will accept and apply — lower accepts weaker matches (YouTube score may be negative; local is a 0–100 filename ratio).</div>
      <div class="d-flex align-center ga-2 flex-wrap">
        <v-text-field v-model="linkFinding.YT_SEARCH_TOP_N" type="number" min="1" max="10" density="compact" variant="outlined" hide-details label="Search top-N" placeholder="3" style="min-width:120px" />
        <v-text-field v-model="linkFinding.TASK_DELAY_MIN" type="number" min="0" step="0.5" density="compact" variant="outlined" hide-details label="Delay min (s)" placeholder="1.5" style="min-width:120px" />
        <v-text-field v-model="linkFinding.TASK_DELAY_MAX" type="number" min="0" step="0.5" density="compact" variant="outlined" hide-details label="Delay max (s)" placeholder="4" style="min-width:120px" />
        <v-text-field v-model="linkFinding.YT_MIN_SCORE" type="number" density="compact" variant="outlined" hide-details label="YouTube min score" placeholder="0" style="min-width:120px" />
        <v-text-field v-model="linkFinding.LOCAL_MIN_SCORE" type="number" min="0" max="100" density="compact" variant="outlined" hide-details label="Local min score" placeholder="60" style="min-width:120px" />
        <v-text-field v-model="linkFinding.SEARCH_RESULT_LIMIT" type="number" min="1" max="50" density="compact" variant="outlined" hide-details label="Picker results" placeholder="10" style="min-width:120px" />
        <v-btn color="primary" :loading="saving" @click="saveLinkFinding">Save</v-btn>
      </div>
    </v-card>

    <v-card variant="outlined" class="pa-4 mb-5">
      <div class="text-subtitle-1 mb-1">Advanced</div>
      <div class="text-caption text-medium-emphasis mb-3">Delete-token TTL = seconds a delete confirmation stays valid before you must re-preview (min 5). Cleanup extensions = which file suffixes the download cleanup treats as junk (comma-separated; zero-byte files are always swept).</div>
      <div class="d-flex align-center ga-2 flex-wrap">
        <v-text-field v-model="advanced.DELETE_TOKEN_TTL" type="number" min="5" density="compact" variant="outlined" hide-details label="Delete-token TTL (s)" placeholder="60" style="min-width:160px" />
        <v-text-field v-model="advanced.CLEANUP_EXTENSIONS" density="compact" variant="outlined" hide-details label="Cleanup extensions" placeholder=".mp4, .webm, .m4a, .part …" style="min-width:280px" />
        <v-btn color="primary" :loading="saving" @click="saveAdvanced">Save</v-btn>
      </div>
    </v-card>

    <v-card variant="outlined" class="pa-4 mb-5"><div class="text-subtitle-1 mb-1">Credentials</div><div class="text-caption text-medium-emphasis mb-3">Used by Discord harvest and MusicBrainz enrichment jobs.</div><v-text-field v-model="form.DISCORD_BOT_TOKEN" label="Discord bot token" type="password" autocomplete="off" :placeholder="state.DISCORD_BOT_TOKEN?.set ? 'set — leave blank to keep' : 'not set'" /><v-text-field v-model="form.DISCORD_CHANNEL_ID" label="Default Discord channel ID" /><v-text-field v-model="form.ACOUSTID_API_KEY" label="AcoustID API key" type="password" autocomplete="off" :placeholder="state.ACOUSTID_API_KEY?.set ? 'set — leave blank to keep' : 'not set'" /><v-btn color="primary" :loading="saving" @click="save">Save credentials</v-btn><v-alert v-if="saved" type="success" variant="tonal" class="mt-3">Saved.</v-alert></v-card>

    <v-card variant="outlined" class="pa-4 mb-5"><div class="text-subtitle-1 text-error mb-1">Cleanup downloads</div><div class="text-body-2 text-medium-emphasis mb-3">Preview and remove only failed, partial, or zero-byte files inside the configured downloads folder. This does not run generic Pipeline controls.</div><v-btn color="error" variant="tonal" prepend-icon="mdi-delete-alert" @click="previewCleanup">Preview cleanup</v-btn><v-alert v-if="cleanupResult" variant="tonal" :type="cleanupResult.status === 'done' ? 'success' : 'warning'" class="mt-3">Deleted {{ cleanupResult.deleted }}, rejected {{ cleanupResult.rejected }}.</v-alert></v-card>

    <v-expansion-panels variant="accordion"><v-expansion-panel title="Advanced maintenance"><v-expansion-panel-text>
      <div class="text-caption text-medium-emphasis mb-4">Unreplaced global maintenance remains available here. Run only when its context is satisfied; technical output stays behind each status.</div>
      <v-row><v-col v-for="script in advancedScripts" :key="script.name" cols="12" md="6"><v-card variant="outlined" class="pa-3 h-100"><div class="d-flex align-center ga-2"><v-icon :color="script.destructive ? 'error' : 'primary'">{{ script.destructive ? 'mdi-delete-alert' : 'mdi-cog-play-outline' }}</v-icon><strong>{{ scriptLabel(script.name) }}</strong><v-spacer /><v-chip size="x-small" variant="tonal">{{ scriptStatus(script.name) }}</v-chip></div><div class="text-caption text-medium-emphasis mt-2">{{ script.desc }}</div><v-btn class="mt-3" size="small" :color="script.destructive ? 'error' : 'primary'" variant="tonal" :disabled="['running','stopping','finalizing'].includes(scriptStatus(script.name))" @click="runMaintenance(script)">{{ script.destructive ? 'Delete verified sources' : 'Run maintenance' }}</v-btn></v-card></v-col></v-row>
    </v-expansion-panel-text></v-expansion-panel></v-expansion-panels>

    <TypedConfirmDialog :model-value="!!cleanup" :title="`Cleanup ${cleanup?.targets.length} download files?`" @update:model-value="(v) => { if (!v) cleanup = null }" @confirm="runCleanup">
      <p>Only failed, partial, or zero-byte files are included. Review exact paths before continuing.</p>
      <v-list density="compact" class="my-3"><v-list-item v-for="target in cleanup?.targets || []" :key="target.relative_path" :title="target.relative_path" /></v-list>
    </TypedConfirmDialog>
    <ConfirmDialog v-model="emptyFoldersConfirm" title="Disable local file operations?" confirm-label="Save empty list" confirm-color="warning" @confirm="confirmEmptyFolders">
      Saving an empty folder list clears the local catalog. Library local matches, Local Files, Untracked, and local matching operations stay disabled until folders are added again.
    </ConfirmDialog>
    <TypedConfirmDialog :model-value="!!maintenanceConfirm" :title="maintenanceConfirm ? scriptLabel(maintenanceConfirm.name) : ''" @update:model-value="(v) => { if (!v) maintenanceConfirm = null }" @confirm="confirmMaintenance">
      <p>This maintenance action deletes verified source files. Type DELETE to continue.</p>
    </TypedConfirmDialog>
  </v-container>
</template>
