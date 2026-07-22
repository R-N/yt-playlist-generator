<script setup>
// Lyrics display + inline edit. When `currentTime` is set AND the lyrics carry LRC
// timestamps, the active line highlights and scrolls into view as audio plays. When
// `editable`, a pencil toggles a raw-text editor; Save emits `save(text)` for the
// parent to persist. Shared by the Review Lyrics tab and LyricsDialog (edit once here).
import { ref, computed, watch, nextTick } from 'vue'
import { parseLyricLines, isSynced, activeLineIndex } from './lyrics'
import { api } from './api'

const props = defineProps({
  text: { type: String, default: '' },
  currentTime: { type: Number, default: null },  // seconds; null = not playing / not tracked
  editable: { type: Boolean, default: false },
})
const emit = defineEmits(['save', 'error'])

const lines = computed(() => parseLyricLines(props.text))
const synced = computed(() => isSynced(lines.value))
const playing = computed(() => synced.value && props.currentTime != null && !editing.value)
const active = computed(() => (playing.value ? activeLineIndex(lines.value, props.currentTime) : -1))

const container = ref(null)
watch(active, async (i) => {
  if (i < 0 || !container.value) return
  await nextTick()
  const el = container.value.querySelector(`[data-line="${i}"]`)
  if (el) el.scrollIntoView({ block: 'center', behavior: 'smooth' })
})

const editing = ref(false)
const draft = ref('')
function startEdit() { draft.value = props.text || ''; editing.value = true }
function cancel() { editing.value = false }
function save() { emit('save', draft.value); editing.value = false }

const romanizing = ref(false)
async function romanize() {
  romanizing.value = true
  try { draft.value = (await api.romanize([draft.value])).texts[0] }
  catch (e) { emit('error', String(e)) }
  finally { romanizing.value = false }
}
</script>

<template>
  <div>
    <div v-if="editable" class="d-flex align-center mb-1" style="min-height:28px">
      <v-spacer />
      <template v-if="!editing">
        <v-btn icon="mdi-pencil" size="x-small" variant="text" aria-label="Edit lyrics" @click="startEdit" />
      </template>
      <template v-else>
        <v-btn size="small" variant="text" prepend-icon="mdi-syllabary-hiragana" :loading="romanizing" @click="romanize">Romanize</v-btn>
        <v-btn size="small" variant="text" @click="cancel">Cancel</v-btn>
        <v-btn size="small" color="primary" variant="tonal" prepend-icon="mdi-content-save" @click="save">Save</v-btn>
      </template>
    </div>
    <v-textarea v-if="editing" v-model="draft" auto-grow rows="12" variant="outlined" density="compact" hide-details
      placeholder="[00:12.34] lyric line — LRC timestamps keep it synced" class="lyric-edit" />
    <div v-else ref="container" class="lyrics-view" :class="{ playing }">
      <p v-for="(line, i) in lines" :key="i" :data-line="i"
        class="lyric-line" :class="{ active: i === active, empty: !line.text.trim() }">{{ line.text || ' ' }}</p>
    </div>
  </div>
</template>

<style scoped>
.lyrics-view { max-height: min(70vh, 520px); overflow-y: auto; line-height: 1.9; white-space: pre-wrap; }
.lyric-line { margin: 0; padding: 1px 8px; transition: color .2s, opacity .2s; }
.lyric-line.empty { min-height: .7em; }
/* Dim non-active lines only while a synced track is playing. */
.lyrics-view.playing .lyric-line { opacity: .45; }
.lyrics-view.playing .lyric-line.active { opacity: 1; font-weight: 700; color: rgb(var(--v-theme-primary)); }
.lyric-edit :deep(textarea) { font-family: monospace; font-size: .85rem; line-height: 1.6; }
</style>
