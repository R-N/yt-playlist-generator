<script setup>
// View lyrics for a workspace item or track (the In-Workspace label's "View lyrics").
// Fetches stored-or-online lyrics on open; refresh re-fetches online.
import { ref, watch } from 'vue'
import { api } from './api'
import LyricsView from './LyricsView.vue'

const props = defineProps({
  modelValue: Boolean,
  kind: { type: String, default: 'workspace' },  // 'workspace' | 'track'
  id: { type: Number, default: null },
  title: { type: String, default: '' },
})
const emit = defineEmits(['update:modelValue'])

const state = ref({ loading: false, found: false, synced: false, lyrics: '', error: '' })

async function load(refresh = false) {
  if (props.id == null) return
  state.value = { ...state.value, loading: true, error: '' }
  try {
    const r = await api.entityLyrics(props.kind, props.id, refresh)
    state.value = { loading: false, found: r.found, synced: r.synced, lyrics: r.lyrics, error: '' }
  } catch (e) {
    state.value = { ...state.value, loading: false, error: String(e) }
  }
}

async function onSave(text) {
  try {
    const r = await api.entitySaveLyrics(props.kind, props.id, text)
    state.value = { loading: false, found: r.found, synced: r.synced, lyrics: r.lyrics, error: '' }
  } catch (e) { state.value = { ...state.value, error: String(e) } }
}

watch(() => [props.modelValue, props.id], ([open]) => { if (open) load(false) })
</script>

<template>
  <v-dialog :model-value="modelValue" max-width="640" scrollable @update:model-value="emit('update:modelValue', $event)">
    <v-card>
      <v-card-title class="d-flex align-center ga-1">
        <span class="text-truncate">{{ title || 'Lyrics' }}</span>
        <v-chip v-if="state.synced" size="x-small" color="primary" variant="tonal">synced</v-chip>
        <v-spacer />
        <v-btn icon="mdi-refresh" variant="text" size="small" :loading="state.loading" aria-label="Re-fetch lyrics online" @click="load(true)" />
        <v-btn icon="mdi-close" variant="text" size="small" aria-label="Close" @click="emit('update:modelValue', false)" />
      </v-card-title>
      <v-divider />
      <v-card-text>
        <v-progress-linear v-if="state.loading" indeterminate color="primary" class="mb-2" />
        <v-alert v-if="state.error" type="error" density="compact" variant="tonal">{{ state.error }}</v-alert>
        <LyricsView v-else :text="state.lyrics" editable @save="onSave" @error="state.error = $event" />
        <div v-if="!state.loading && !state.found && !state.error" class="text-medium-emphasis text-body-2 mt-1">No lyrics found — use the pencil to add them.</div>
      </v-card-text>
    </v-card>
  </v-dialog>
</template>
