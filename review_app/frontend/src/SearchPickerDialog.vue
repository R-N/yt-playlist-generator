<script setup>
// Interactive search picker shared by "Find on YouTube" (mode 'youtube') and
// "Find local file" (mode 'local'). Shows up to 10 candidates re-ranked by the
// review score (score shown), with an editable search bar to redo the search.
// The parent applies the chosen result via @pick — this dialog never writes.
import { ref, watch } from 'vue'
import { api } from './api'
import { formatViews } from './workspace'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  mode: { type: String, default: 'youtube' },   // 'youtube' | 'local'
  title: { type: String, default: '' },          // the row this is for (context line)
  initialQuery: { type: String, default: '' },
  artist: { type: String, default: '' },          // yt scoring target (fixed while query edits)
  songTitle: { type: String, default: '' },
})
const emit = defineEmits(['update:modelValue', 'pick'])

const query = ref('')
const results = ref([])
const loading = ref(false)
const error = ref('')

async function search() {
  loading.value = true; error.value = ''
  try {
    const r = props.mode === 'youtube'
      ? await api.searchYoutube({ query: query.value, artist: props.artist, title: props.songTitle })
      : await api.searchLocal({ query: query.value })
    results.value = r.results || []
  } catch (e) { error.value = String(e); results.value = [] }
  finally { loading.value = false }
}
watch(() => props.modelValue, (open) => {
  if (open) { query.value = props.initialQuery; results.value = []; error.value = ''; search() }
})
const scoreColor = (s) => (s >= 100 ? 'success' : s >= 40 ? 'primary' : s >= 0 ? 'warning' : 'error')
function close() { emit('update:modelValue', false) }
</script>

<template>
  <v-dialog :model-value="modelValue" max-width="760" scrollable @update:model-value="$emit('update:modelValue', $event)">
    <v-card>
      <v-card-title class="d-flex align-center">
        {{ mode === 'youtube' ? 'Find on YouTube' : 'Find local file' }}
        <v-spacer /><v-btn icon="mdi-close" variant="text" size="small" aria-label="Close" @click="close" />
      </v-card-title>
      <div class="text-caption text-medium-emphasis px-4">For: {{ title }}</div>
      <v-divider class="mt-2" />
      <v-card-text>
        <div class="d-flex align-center ga-2 mb-3">
          <v-text-field v-model="query" density="compact" variant="solo-filled" flat hide-details
            :placeholder="mode === 'youtube' ? 'Search YouTube…' : 'Search local files…'"
            prepend-inner-icon="mdi-magnify" @keyup.enter="search" />
          <v-btn color="primary" :loading="loading" icon="mdi-magnify" size="small" aria-label="Search" @click="search" />
        </div>
        <v-alert v-if="error" type="error" variant="tonal" density="compact" class="mb-3">{{ error }}</v-alert>
        <div v-if="loading" class="pa-8 text-center"><v-progress-circular indeterminate color="primary" /></div>
        <v-list v-else-if="results.length" density="compact" lines="two">
          <v-list-item v-for="r in results" :key="mode === 'youtube' ? r.id : `${r.folder_identity}-${r.relative_path}`"
            @click="$emit('pick', r)">
            <template #prepend>
              <v-chip size="small" :color="scoreColor(r.score)" variant="tonal" class="mr-2 font-weight-medium" style="min-width:52px;justify-content:center">{{ r.score }}</v-chip>
            </template>
            <template v-if="mode === 'youtube'">
              <v-list-item-title>{{ r.title || r.id }}</v-list-item-title>
              <v-list-item-subtitle>{{ r.channel || '—' }}<span v-if="r.view_count"> · {{ formatViews(r.view_count) }}</span> · <span class="text-medium-emphasis">{{ r.id }}</span></v-list-item-subtitle>
            </template>
            <template v-else>
              <v-list-item-title>{{ r.basename }}</v-list-item-title>
              <v-list-item-subtitle>{{ r.relative_path }}</v-list-item-subtitle>
            </template>
            <template #append>
              <v-btn v-if="mode === 'youtube'" icon="mdi-open-in-new" size="small" variant="text" :href="`https://www.youtube.com/watch?v=${r.id}`" target="_blank" rel="noopener noreferrer" aria-label="Open on YouTube" @click.stop />
              <v-btn icon="mdi-check" size="small" variant="tonal" aria-label="Choose" @click.stop="$emit('pick', r)" />
            </template>
          </v-list-item>
        </v-list>
        <div v-else class="pa-8 text-center text-medium-emphasis">No results. Try a different search.</div>
      </v-card-text>
    </v-card>
  </v-dialog>
</template>
