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

const current = computed(() => queue.value[idx.value] || null)

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
  if (e.target.tagName === 'INPUT') return
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
  <v-container>
    <div class="d-flex align-center flex-wrap mb-3" style="gap:8px">
      <v-chip color="green">✓ {{ counts.approved }}</v-chip>
      <v-chip color="red">✗ {{ counts.rejected }}</v-chip>
      <v-chip color="grey">? {{ counts.unreviewed }}</v-chip>
      <v-spacer />
      <v-select
        v-model="status" :items="['unreviewed','approved','rejected','all']"
        density="compact" hide-details style="max-width: 160px"
        @update:model-value="loadQueue" />
      <v-btn :loading="exporting==='working'" variant="tonal" @click="doExport">
        Export CSV
      </v-btn>
    </div>

    <v-alert v-if="error" type="error" closable class="mb-3" @click:close="error=''">
      {{ error }}
    </v-alert>
    <v-alert v-if="exporting && exporting!=='working'" type="success" class="mb-3">
      {{ exporting }}
    </v-alert>

    <div v-if="loading" class="text-center pa-8">
      <v-progress-circular indeterminate />
    </div>

    <v-card v-else-if="current" class="pa-4">
      <div class="text-overline">
        {{ idx + 1 }} / {{ queue.length }} in batch
        <span v-if="current.check===1" class="text-green"> · approved</span>
        <span v-else-if="current.check===0" class="text-red"> · rejected</span>
      </div>

      <v-row>
        <v-col cols="12" md="6">
          <div class="text-subtitle-2 text-grey">YOUR FILE</div>
          <div class="text-h6">{{ current.artist }} – {{ current.title }}</div>
          <div class="text-caption text-grey mb-2">{{ current.filename }}</div>
          <v-chip v-if="current.local_better" color="amber" size="small" class="mb-2">
            local ≥ threshold ({{ fmt(current.local_bitrate) }}k)
          </v-chip>
          <audio v-if="current.has_local" :src="api.audioUrl(current.id)"
            controls preload="none" style="width:100%" />
          <v-alert v-else type="warning" density="compact">local file not found</v-alert>
        </v-col>

        <v-col cols="12" md="6">
          <div class="text-subtitle-2 text-grey">YOUTUBE CANDIDATE</div>
          <div class="text-h6">{{ current.yt_channel }}</div>
          <div class="text-caption text-grey mb-2">{{ current.yt_title }}</div>
          <iframe v-if="current.yt_id" width="100%" height="220"
            :src="youtubeEmbed(current.yt_id)"
            frameborder="0" allow="encrypted-media" allowfullscreen />
        </v-col>
      </v-row>

      <v-alert v-if="current.mb_title || current.mb_artist"
        :color="confidenceColor(current.mb_confidence)"
        variant="tonal" density="compact" class="mt-3">
        <div class="text-caption">
          MUSICBRAINZ · AcoustID {{ fmt(current.ac_score, 2) }} ·
          <span class="text-uppercase font-weight-bold">{{ current.mb_confidence }}</span>
          <span v-if="String(current.mb_suggest) === '1'"> · suggests APPROVE</span>
        </div>
        <div><strong>{{ current.mb_artist }}</strong> – {{ current.mb_title }}</div>
      </v-alert>

      <v-divider class="my-3" />
      <div class="d-flex flex-wrap text-caption text-grey" style="gap:16px">
        <span>score {{ fmt(current.score) }}</span>
        <span>sim_artist {{ fmt(current.sim_artist) }}</span>
        <span>sim_artist_title {{ fmt(current.sim_artist_title) }}</span>
        <span>sim_title {{ fmt(current.sim_title) }}</span>
        <span>fmt {{ current.audio_format }} {{ fmt(current.audio_bitrate) }}k</span>
        <span>views {{ current.yt_views }}</span>
      </div>

      <div class="d-flex justify-center mt-4" style="gap:12px">
        <v-btn color="red" size="large" @click="decide(false)">
          Reject <span class="text-caption ml-1">(R / ←)</span>
        </v-btn>
        <v-btn variant="text" @click="prev">Back (↑)</v-btn>
        <v-btn color="green" size="large" @click="decide(true)">
          Approve <span class="text-caption ml-1">(A / →)</span>
        </v-btn>
      </div>
    </v-card>

    <v-card v-else class="pa-8 text-center">
      <div class="text-h6">Nothing to review in “{{ status }}”.</div>
    </v-card>
  </v-container>
</template>
