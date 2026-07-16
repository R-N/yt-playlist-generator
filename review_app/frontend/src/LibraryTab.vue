<script setup>
import { ref, computed, onMounted } from 'vue'
import { api } from './api'
import { reviewTrack } from './nav'
import { STATE_META, stateMeta, filterLibrary } from './review'

const rows = ref([])
const loading = ref(false)
const error = ref('')
const opening = ref(null)      // id being fetched for review
const state = ref('all')
const query = ref('')

const stateFilters = [
  { value: 'all', label: 'All' },
  ...Object.entries(STATE_META).map(([value, m]) => ({ value, label: m.label })),
]

// Live count per state, for the filter chips (so you see the shape of the library).
const stateCounts = computed(() => {
  const c = { all: rows.value.length }
  for (const r of rows.value) c[r.state] = (c[r.state] || 0) + 1
  return c
})

const filtered = computed(() => filterLibrary(rows.value, state.value, query.value))

const headers = [
  { title: 'State', key: 'state', width: 130 },
  { title: 'Track', key: 'title', sortable: true },
  { title: 'Source', key: 'filename', sortable: false },
  { title: '', key: 'actions', sortable: false, align: 'end', width: 110 },
]

async function load() {
  loading.value = true
  error.value = ''
  try {
    rows.value = (await api.library()).rows
  } catch (e) {
    error.value = String(e)
  } finally {
    loading.value = false
  }
}

async function openReview(row) {
  opening.value = row.id
  try {
    reviewTrack(await api.track(row.id))   // fetch full row, hand to Review view
  } catch (e) {
    error.value = String(e)
  } finally {
    opening.value = null
  }
}

function trackTitle(r) {
  return [r.artist, r.title].filter(Boolean).join(' – ') || r.filename || '(untitled)'
}
function source(r) {
  return r.has_local ? r.filename : (r.yt_channel || r.yt_title || r.yt_id || '—')
}

onMounted(load)
</script>

<template>
  <div class="d-flex align-center mb-3" style="gap:12px">
    <div class="text-h6">Library</div>
    <v-chip size="small" variant="tonal">{{ rows.length }} tracks</v-chip>
    <v-spacer />
    <v-text-field v-model="query" placeholder="Search artist, title, file…"
      density="compact" variant="solo-filled" flat hide-details clearable
      prepend-inner-icon="mdi-magnify" style="max-width:320px" />
  </div>

  <v-chip-group v-model="state" mandatory selected-class="text-primary" class="mb-2">
    <v-chip v-for="f in stateFilters" :key="f.value" :value="f.value"
      size="small" variant="outlined">
      <v-icon v-if="f.value!=='all'" start size="14">{{ stateMeta(f.value).icon }}</v-icon>
      {{ f.label }}
      <span class="ml-1 text-medium-emphasis">{{ stateCounts[f.value] || 0 }}</span>
    </v-chip>
  </v-chip-group>

  <v-alert v-if="error" type="error" closable class="mb-3" @click:close="error=''">{{ error }}</v-alert>

  <v-card v-if="loading" class="pa-12 text-center" variant="tonal">
    <v-progress-circular indeterminate color="primary" size="40" />
  </v-card>

  <v-data-table v-else :headers="headers" :items="filtered" :items-per-page="25"
    density="comfortable" class="rounded-lg" hover
    :items-per-page-options="[25, 50, 100, 200]">
    <template #item.state="{ item }">
      <v-chip :color="stateMeta(item.state).color" size="small" variant="tonal">
        <v-icon start size="14">{{ stateMeta(item.state).icon }}</v-icon>
        {{ stateMeta(item.state).label }}
      </v-chip>
    </template>
    <template #item.title="{ item }">
      <div class="font-weight-medium">{{ trackTitle(item) }}</div>
    </template>
    <template #item.filename="{ item }">
      <span class="text-caption text-medium-emphasis">{{ source(item) }}</span>
    </template>
    <template #item.actions="{ item }">
      <v-btn size="small" variant="tonal" color="primary" :loading="opening===item.id"
        append-icon="mdi-arrow-right" @click="openReview(item)">Review</v-btn>
    </template>
    <template #no-data>
      <div class="pa-6 text-medium-emphasis">No tracks match this filter.</div>
    </template>
  </v-data-table>
</template>
