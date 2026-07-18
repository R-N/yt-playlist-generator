<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { api } from './api'
import { activeTab, focusTrack, invalidateData } from './nav'
import { keyToAction, fmt, advanceIndex, prevIndex, youtubeEmbed, confidenceColor } from './review'

const counts = ref({ total: 0, unreviewed: 0, approved: 0, rejected: 0 })
const queue = ref([])
const idx = ref(0)
const status = ref('unreviewed')
const loading = ref(false)
const error = ref('')
const fromLibrary = ref(false)   // reviewing a single track handed over by the Library

const current = computed(() => queue.value[idx.value] || null)
const pctSim = (v) => (v == null || v === '' ? 0 : Math.round(Number(v) * 100))

async function refreshCounts() { counts.value = await api.counts() }

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
    invalidateData()
    t.check = approve ? 1 : 0
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
  <div class="d-flex align-center flex-wrap mb-4" style="gap:6px 14px">
    <v-select v-model="status" :items="['unreviewed','approved','rejected','all']"
      density="compact" variant="solo-filled" flat hide-details
      prepend-inner-icon="mdi-filter-variant" style="min-width:200px;max-width:220px"
      @update:model-value="loadQueue" />
    <template v-if="current">
      <span class="metric"><span class="metric-label">Score</span>{{ fmt(current.score) }}</span>
      <span class="metric"><span class="metric-label">Artist</span>{{ pctSim(current.sim_artist) }}%</span>
      <span class="metric"><span class="metric-label">Art+Title</span>{{ pctSim(current.sim_artist_title) }}%</span>
      <span class="metric"><span class="metric-label">Title</span>{{ pctSim(current.sim_title) }}%</span>
      <span class="metric"><span class="metric-label">Format</span>{{ current.audio_format || '—' }} {{ fmt(current.audio_bitrate) }}k</span>
    </template>
    <v-spacer />
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

  <div v-if="loading" class="text-center pa-12">
    <v-progress-circular indeterminate color="primary" size="48" />
  </div>

  <v-row v-else-if="current">
    <!-- LEFT: your file (top), YouTube candidate audio (bottom), match detail below. -->
    <v-col cols="12" md="6">
      <v-card variant="outlined" class="pa-4 mb-3">
        <v-card-subtitle class="px-0"><v-icon size="18" color="primary" class="mr-2">mdi-folder-music</v-icon>Your file</v-card-subtitle>
        <div class="text-h6 mb-1">{{ current.artist }} – {{ current.title }}</div>
        <div class="text-caption text-medium-emphasis mb-3">{{ current.filename }}</div>
        <v-chip v-if="current.local_better" color="warning" size="small" variant="tonal" class="mb-3">
          local ≥ threshold ({{ fmt(current.local_bitrate) }}k)
        </v-chip>
        <audio v-if="current.has_local" :src="api.audioUrl(current.id)" controls preload="none" />
        <v-alert v-else type="warning" density="compact" variant="tonal">local file not found</v-alert>
      </v-card>

      <v-card variant="outlined" class="pa-4 mb-3">
        <v-card-subtitle class="px-0"><v-icon size="18" color="error" class="mr-2">mdi-youtube</v-icon>YouTube candidate</v-card-subtitle>
        <div class="text-h6 mb-1">{{ current.yt_title || current.yt_channel }}</div>
        <div class="text-caption text-medium-emphasis mb-3">
          {{ current.yt_channel }}<span v-if="current.yt_views"> · {{ current.yt_views }} views</span><span v-if="current.yt_likes"> · {{ current.yt_likes }} likes</span>
        </div>
        <audio v-if="current.yt_id" :src="api.ytAudioUrl(current.yt_id)" controls preload="none" />
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
    </v-col>

    <!-- RIGHT: just the embed. -->
    <v-col cols="12" md="6">
      <iframe v-if="current.yt_id" width="100%" style="border:0;border-radius:12px;height:min(70vh,520px)"
        :src="youtubeEmbed(current.yt_id)" allow="encrypted-media" allowfullscreen />
      <v-alert v-else type="warning" variant="tonal" density="compact">no YouTube id</v-alert>
    </v-col>
  </v-row>

  <v-card v-else class="pa-12 text-center" variant="tonal">
    <v-icon size="48" color="success" class="mb-2">mdi-check-all</v-icon>
    <div class="text-h6">Nothing to review in “{{ status }}”.</div>
    <div class="text-medium-emphasis">Pick another filter.</div>
  </v-card>
</template>

<style scoped>
.metric { font-size: .85rem; font-weight: 600; white-space: nowrap; }
.metric-label {
  font-size: .62rem; text-transform: uppercase; letter-spacing: .05em;
  opacity: .55; margin-right: 5px; font-weight: 500;
}
</style>
