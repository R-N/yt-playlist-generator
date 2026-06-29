<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { api } from './api'

const scripts = ref([])         // catalog: {name, script, desc, destructive}
const selected = ref(null)      // active script name
const job = ref({ status: 'idle', lines: [], returncode: null })
const error = ref('')
let poll = null

const current = computed(() => scripts.value.find((s) => s.name === selected.value) || null)

async function loadCatalog() {
  scripts.value = await api.scripts()
  if (scripts.value.length && !selected.value) select(scripts.value[0].name)
}

async function select(name) {
  selected.value = name
  await refresh()
}

async function refresh() {
  if (!selected.value) return
  try {
    job.value = await api.scriptState(selected.value, 400)
  } catch (e) {
    error.value = String(e)
  }
}

async function run() {
  error.value = ''
  // destructive scripts delete files on disk — require a typed confirmation
  if (current.value?.destructive) {
    const answer = window.prompt(
      `"${selected.value}" DELETES files on disk and cannot be undone.\n` +
      `Type DELETE to run it.`)
    if (answer !== 'DELETE') { error.value = 'cancelled'; return }
  }
  try {
    await api.scriptRun(selected.value)
    await refresh()
  } catch (e) {
    error.value = String(e)
  }
}

async function stop() {
  try {
    await api.scriptStop(selected.value)
    await refresh()
  } catch (e) {
    error.value = String(e)
  }
}

const statusColor = (s) =>
  ({ running: 'blue', done: 'green', failed: 'red', stopped: 'orange' }[s] || 'grey')

onMounted(() => {
  loadCatalog()
  poll = setInterval(refresh, 1500)   // live-ish log + status
})
onUnmounted(() => clearInterval(poll))
</script>

<template>
  <v-container fluid>
    <div class="text-h6 mb-1">Pipeline scripts</div>
    <div class="text-caption text-grey mb-4">
      Run a repo script as a background job and watch its output. Each script's
      behavior is set by the constants at the top of its file (and the keys on the
      Settings tab); this just launches and monitors it. Long jobs (download, scan)
      keep running on the server even if you switch tabs.
    </div>

    <v-alert v-if="error" type="error" class="mb-3" closable @click:close="error=''">
      {{ error }}
    </v-alert>

    <v-row>
      <v-col cols="12" md="4">
        <v-list density="compact" nav>
          <v-list-item v-for="s in scripts" :key="s.name"
            :active="s.name === selected" @click="select(s.name)">
            <v-list-item-title>
              {{ s.name }}
              <v-icon v-if="s.destructive" color="red" size="x-small" icon="mdi-delete-alert" />
            </v-list-item-title>
            <v-list-item-subtitle>{{ s.desc }}</v-list-item-subtitle>
          </v-list-item>
        </v-list>
      </v-col>

      <v-col cols="12" md="8">
        <v-card v-if="selected" class="pa-3">
          <div class="d-flex align-center mb-2">
            <div class="text-subtitle-1">{{ selected }}</div>
            <v-chip :color="statusColor(job.status)" size="small" class="ml-3">
              {{ job.status }}<span v-if="job.returncode!=null"> ({{ job.returncode }})</span>
            </v-chip>
            <v-chip v-if="current?.destructive" color="red" size="small" class="ml-2">
              deletes files
            </v-chip>
            <v-spacer />
            <v-btn :color="current?.destructive ? 'red' : 'primary'" size="small" class="mr-2"
              :disabled="job.status==='running'" @click="run">Run</v-btn>
            <v-btn color="orange" size="small" variant="tonal"
              :disabled="job.status!=='running'" @click="stop">Stop</v-btn>
          </div>
          <v-sheet color="black" class="pa-2" rounded
            style="height: 420px; overflow:auto; font-family: monospace; font-size: 12px">
            <div v-if="!job.lines.length" class="text-grey">— no output yet —</div>
            <div v-for="(l, i) in job.lines" :key="i" class="text-green-lighten-2"
              style="white-space: pre-wrap">{{ l }}</div>
          </v-sheet>
        </v-card>
      </v-col>
    </v-row>
  </v-container>
</template>
