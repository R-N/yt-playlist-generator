<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { api } from './api'
import { keyToAction, fmt, advanceIndex, prevIndex, youtubeEmbed, confidenceColor } from './review'

const counts = ref({ total: 0, unreviewed: 0, approved: 0, rejected: 0 })
const queue = ref([])
const idx = ref(0)
const status = ref('unreviewed')
const loading = ref(false)
const error = ref('')
const exporting = ref('')
const details = ref(false)

const current = computed(() => queue.value[idx.value] || null)
const reviewed = computed(() => counts.value.approved + counts.value.rejected)
const progress = computed(() => {
  const t = counts.value.total || 0
  return t ? Math.round((reviewed.value / t) * 100) : 0
})
const pill = computed(() => {
  const c = current.value
  if (!c) return null
  if (c.check === 1) return { color: 'success', text: 'approved' }
  if (c.check === 0) return { color: 'error', text: 'rejected' }
  return { color: 'grey', text: 'unreviewed' }
})

async function refreshCounts() {
  counts.value = await api.counts()
}

async function loadQueue() {
  loading.value = true
  error.value = ''
  try {
    const data = await api.rows(status.value, 200, 0)
    queue.value = data.rows
    idx.value = 0
  } catch (e) {
    error.value = String(e)
  } finally {
    loading.value = false
  }
}

async function decide(approve) {
  const t = current.value
  if (!t) return
  try {
    await api.decide(t.id, approve)
    t.check = approve ? 1 : 0
    advance()
    refreshCounts()
  } catch (e) {
    error.value = String(e)
  }
}

function advance() {
  const { idx: next, reload } = advanceIndex(idx.value, queue.value.length)
  if (reload) loadQueue()
  else idx.value = next
}

function prev() {
  idx.value = prevIndex(idx.value)
}

async function doExport() {
  exporting.value = 'working'
  try {
    const r = await api.export()
    exporting.value = `saved (snapshot ${r.snapshot}, ${r.rows} rows)`
  } catch (e) {
    exporting.value = ''
    error.value = String(e)
  }
}

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
  <!-- Progress + toolbar -->
  <div class="mb-5">
    <div class="d-flex align-center mb-1" style="gap:12px">
      <div class="text-h6">Review matches</div>
      <v-spacer />
      <v-select
        v-model="status" :items="['unreviewed','approved','rejected','all']"
        density="compact" variant="solo-filled" flat hide-details
        prepend-inner-icon="mdi-filter-variant" style="max-width:180px"
        @update:model-value="loadQueue" />
      <v-btn :loading="exporting==='working'" variant="tonal" color="primary"
        prepend-icon="mdi-content-save" @click="doExport">Export</v-btn>
    </div>

    <v-progress-linear :model-value="progress" color="primary" height="10"
      rounded bg-opacity="0.15" />
    <div class="d-flex align-center mt-1 text-caption" style="gap:14px">
      <span class="font-weight-medium">{{ progress }}% reviewed</span>
      <span class="text-medium-emphasis">{{ reviewed }} / {{ counts.total }}</span>
      <v-spacer />
      <span class="text-success">✓ {{ counts.approved }}</span>
      <span class="text-error">✗ {{ counts.rejected }}</span>
      <span class="text-medium-emphasis">? {{ counts.unreviewed }}</span>
    </div>
  </div>

  <v-alert v-if="error" type="error" closable class="mb-4" @click:close="error=''">
    {{ error }}
  </v-alert>
  <v-snackbar :model-value="!!exporting && exporting!=='working'" timeout="3000"
    color="success" @update:model-value="exporting=''">{{ exporting }}</v-snackbar>

  <div v-if="loading" class="text-center pa-12">
    <v-progress-circular indeterminate color="primary" size="48" />
  </div>

  <!-- Review card -->
  <v-card v-else-if="current" class="pa-5" elevation="4">
    <div class="d-flex align-center mb-4" style="gap:10px">
      <v-chip size="small" variant="tonal">{{ idx + 1 }} / {{ queue.length }} in batch</v-chip>
      <v-spacer />
      <v-chip v-if="pill" :color="pill.color" size="small" variant="flat">{{ pill.text }}</v-chip>
    </div>

    <div style="position:relative">
      <v-row>
        <v-col cols="12" md="6">
          <div class="panel panel-local">
            <div class="panel-head">
              <v-icon size="18" color="primary">mdi-folder-music</v-icon> your file
            </div>
            <div class="text-h6 mb-1">{{ current.artist }} – {{ current.title }}</div>
            <div class="text-caption text-medium-emphasis mb-3">{{ current.filename }}</div>
            <v-chip v-if="current.local_better" color="warning" size="small" variant="tonal" class="mb-3">
              local ≥ threshold ({{ fmt(current.local_bitrate) }}k)
            </v-chip>
            <audio v-if="current.has_local" :src="api.audioUrl(current.id)" controls preload="none" />
            <v-alert v-else type="warning" density="compact" variant="tonal">local file not found</v-alert>
          </div>
        </v-col>

        <v-col cols="12" md="6">
          <div class="panel panel-yt">
            <div class="panel-head">
              <v-icon size="18" color="error">mdi-youtube</v-icon> youtube candidate
            </div>
            <div class="text-h6 mb-1">{{ current.yt_title || current.yt_channel }}</div>
            <div class="text-caption text-medium-emphasis mb-3">
              {{ current.yt_channel }}<span v-if="current.yt_views"> · {{ current.yt_views }} views</span>
            </div>
            <iframe v-if="current.yt_id" width="100%" height="200" style="border:0;border-radius:10px"
              :src="youtubeEmbed(current.yt_id)" allow="encrypted-media" allowfullscreen />
            <audio v-if="current.yt_id" :src="api.ytAudioUrl(current.yt_id)"
              controls preload="none" class="mt-2" />
            <div class="text-caption text-medium-emphasis mt-1">candidate audio · loads on play</div>
          </div>
        </v-col>
      </v-row>
      <div class="vs-badge d-none d-md-grid">VS</div>
    </div>

    <!-- MusicBrainz verdict -->
    <v-alert v-if="current.mb_title || current.mb_artist"
      :color="confidenceColor(current.mb_confidence)" variant="tonal" density="comfortable" class="mt-4"
      :icon="String(current.mb_suggest)==='1' ? 'mdi-thumb-up' : 'mdi-database-check'">
      <div class="text-caption text-uppercase font-weight-bold">
        MusicBrainz · {{ current.mb_confidence }} match
        <span v-if="String(current.mb_suggest)==='1'"> · suggests approve</span>
      </div>
      <div><strong>{{ current.mb_artist }}</strong> – {{ current.mb_title }}
        <span class="text-caption text-medium-emphasis ml-2">AcoustID {{ fmt(current.ac_score, 2) }}</span>
      </div>
    </v-alert>

    <!-- Raw match metrics: tucked away, not front and center -->
    <div class="mt-3">
      <v-btn size="small" variant="text" :append-icon="details?'mdi-chevron-up':'mdi-chevron-down'"
        @click="details=!details">Match details</v-btn>
      <v-expand-transition>
        <div v-show="details" class="d-flex flex-wrap text-caption text-medium-emphasis mt-2" style="gap:18px">
          <span>score {{ fmt(current.score) }}</span>
          <span>sim_artist {{ fmt(current.sim_artist, 2) }}</span>
          <span>sim_artist_title {{ fmt(current.sim_artist_title, 2) }}</span>
          <span>sim_title {{ fmt(current.sim_title, 2) }}</span>
          <span>fmt {{ current.audio_format }} {{ fmt(current.audio_bitrate) }}k</span>
        </div>
      </v-expand-transition>
    </div>

    <v-divider class="my-4" />
    <div class="d-flex align-center justify-center" style="gap:14px">
      <v-btn color="error" size="large" min-width="150" prepend-icon="mdi-close" @click="decide(false)">
        Reject
        <v-chip size="x-small" class="ml-2" variant="outlined">R / ←</v-chip>
      </v-btn>
      <v-btn variant="text" prepend-icon="mdi-undo" @click="prev">Back <v-chip size="x-small" class="ml-1" variant="outlined">↑</v-chip></v-btn>
      <v-btn color="success" size="large" min-width="150" prepend-icon="mdi-check" @click="decide(true)">
        Approve
        <v-chip size="x-small" class="ml-2" variant="outlined">A / →</v-chip>
      </v-btn>
    </div>
  </v-card>

  <v-card v-else class="pa-12 text-center" variant="tonal">
    <v-icon size="48" color="success" class="mb-2">mdi-check-all</v-icon>
    <div class="text-h6">Nothing to review in “{{ status }}”.</div>
    <div class="text-medium-emphasis">Pick another filter or export your marks.</div>
  </v-card>
</template>
