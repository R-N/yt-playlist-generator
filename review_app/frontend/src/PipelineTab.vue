<script setup>
import { ref, reactive, onMounted, onUnmounted } from 'vue'
import { api } from './api'

// Friendly name / icon / pipeline stage per script. The backend catalog gives
// the raw name + description + destructive flag; this is just presentation.
const META = {
  url_extractor:        { label: 'Extract from dump.csv',  icon: 'mdi-file-delimited-outline', stage: 'Harvest' },
  playlist_generator:   { label: 'Build playlist links',   icon: 'mdi-playlist-play',          stage: 'Harvest' },
  downloader:           { label: 'Download audio',         icon: 'mdi-download',               stage: 'Acquire' },
  searcher:             { label: 'Match local library',    icon: 'mdi-magnify-scan',           stage: 'Match' },
  filter_local_quality: { label: 'Flag low-quality',       icon: 'mdi-quality-high',           stage: 'Match' },
  acoustid_enrich:      { label: 'AcoustID cross-check',   icon: 'mdi-fingerprint',            stage: 'Enrich' },
  check_untracked:      { label: 'List untracked files',   icon: 'mdi-clipboard-list-outline', stage: 'Maintain' },
  cleanup_downloads:    { label: 'Clean failed downloads', icon: 'mdi-broom',                  stage: 'Maintain' },
  cleanup_tracked:      { label: 'Delete verified sources', icon: 'mdi-delete-sweep',          stage: 'Maintain' },
}
const STAGES = ['Harvest', 'Acquire', 'Match', 'Enrich', 'Maintain']

const catalog = ref([])
const states = reactive({})        // name -> job state (status, artifacts, lines, ...)
const showLog = reactive({})       // name -> bool
const error = ref('')
let poll = null

const meta = (n) => META[n] || { label: n, icon: 'mdi-cog', stage: 'Maintain' }
const stageScripts = (stage) => catalog.value.filter((s) => meta(s.name).stage === stage)
const statusColor = (s) =>
  ({ running: 'info', done: 'success', failed: 'error', stopped: 'warning' }[s] || 'grey')

async function loadCatalog() {
  catalog.value = await api.scripts()
  await Promise.all(catalog.value.map((s) => refresh(s.name)))
}

async function refresh(name) {
  try {
    states[name] = await api.scriptState(name, 200)
  } catch (e) {
    error.value = String(e)
  }
}

async function run(s) {
  error.value = ''
  if (s.destructive) {
    const answer = window.prompt(
      `"${meta(s.name).label}" DELETES files on disk and cannot be undone.\nType DELETE to run it.`)
    if (answer !== 'DELETE') { error.value = 'cancelled'; return }
  }
  try {
    await api.scriptRun(s.name)
    await refresh(s.name)
  } catch (e) {
    error.value = String(e)
  }
}

async function stop(name) {
  try { await api.scriptStop(name); await refresh(name) }
  catch (e) { error.value = String(e) }
}

async function copyLinks(links) {
  try { await navigator.clipboard.writeText(links.join('\n')) } catch {}
}

onMounted(() => {
  loadCatalog()
  // Poll only running jobs; localhost + cheap, keeps result stats fresh live.
  poll = setInterval(() => {
    for (const s of catalog.value) if (states[s.name]?.status === 'running') refresh(s.name)
  }, 1500)
})
onUnmounted(() => clearInterval(poll))
</script>

<template>
  <div class="d-flex align-center mb-1" style="gap:10px">
    <div class="text-h6">Pipeline</div>
    <div class="text-caption text-medium-emphasis">
      Run a step, see its result. Each runs on the server; you can leave the tab.
    </div>
  </div>

  <v-alert v-if="error" type="error" density="compact" class="mb-3" closable @click:close="error=''">
    {{ error }}
  </v-alert>

  <div v-for="stage in STAGES" :key="stage">
    <template v-if="stageScripts(stage).length">
      <div class="text-overline text-medium-emphasis mt-4 mb-1">{{ stage }}</div>
      <v-row>
        <v-col v-for="s in stageScripts(stage)" :key="s.name" cols="12" md="6">
          <v-card class="pa-4 h-100" variant="outlined">
            <div class="d-flex align-center mb-1" style="gap:10px">
              <v-icon :color="s.destructive ? 'error' : 'primary'">{{ meta(s.name).icon }}</v-icon>
              <div class="text-subtitle-1 font-weight-medium">{{ meta(s.name).label }}</div>
              <v-spacer />
              <v-chip v-if="states[s.name]" :color="statusColor(states[s.name].status)"
                size="small" variant="tonal">
                <v-progress-circular v-if="states[s.name].status==='running'" indeterminate
                  size="12" width="2" class="mr-1" />
                {{ states[s.name].status }}
              </v-chip>
            </div>
            <div class="text-caption text-medium-emphasis mb-3">{{ s.desc }}</div>

            <!-- Human result: counts + clickable playlist links -->
            <div v-if="states[s.name]?.artifacts?.length" class="mb-3">
              <div class="d-flex flex-wrap" style="gap:8px">
                <v-chip v-for="a in states[s.name].artifacts" :key="a.file"
                  size="small" :variant="a.exists ? 'tonal' : 'text'"
                  :color="a.exists ? 'primary' : undefined">
                  <span class="font-weight-bold mr-1">{{ a.count }}</span> {{ a.label }}
                </v-chip>
              </div>
              <template v-for="a in states[s.name].artifacts" :key="a.file + '-links'">
                <div v-if="a.kind==='links' && a.links?.length" class="mt-2">
                  <div class="d-flex align-center">
                    <div class="text-caption text-medium-emphasis">Playlist links</div>
                    <v-btn size="x-small" variant="text" prepend-icon="mdi-content-copy"
                      class="ml-2" @click="copyLinks(a.links)">Copy</v-btn>
                  </div>
                  <a v-for="(l, i) in a.links" :key="i" :href="l" target="_blank" rel="noopener"
                    class="d-block text-caption text-truncate text-primary">{{ l }}</a>
                </div>
              </template>
            </div>

            <div class="d-flex align-center" style="gap:8px">
              <v-btn :color="s.destructive ? 'error' : 'primary'" variant="flat"
                :prepend-icon="s.destructive ? 'mdi-delete-alert' : 'mdi-play'"
                :disabled="states[s.name]?.status==='running'" @click="run(s)">Run</v-btn>
              <v-btn v-if="states[s.name]?.status==='running'" color="warning" variant="tonal"
                prepend-icon="mdi-stop" @click="stop(s.name)">Stop</v-btn>
              <v-spacer />
              <v-btn size="small" variant="text"
                :append-icon="showLog[s.name] ? 'mdi-chevron-up' : 'mdi-chevron-down'"
                @click="showLog[s.name] = !showLog[s.name]">Log</v-btn>
            </div>

            <v-expand-transition>
              <v-sheet v-show="showLog[s.name]" color="black" rounded class="pa-2 mt-3"
                style="max-height:260px; overflow:auto; font-family:monospace; font-size:12px">
                <div v-if="!states[s.name]?.lines?.length" class="text-grey">— no output yet —</div>
                <div v-for="(l, i) in states[s.name]?.lines || []" :key="i"
                  class="text-green-lighten-2" style="white-space:pre-wrap">{{ l }}</div>
              </v-sheet>
            </v-expand-transition>
          </v-card>
        </v-col>
      </v-row>
    </template>
  </div>
</template>
