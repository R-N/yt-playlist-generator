<script setup>
import { ref } from 'vue'
import { api } from './api'

const text = ref('')
const loading = ref(false)
const error = ref('')
const result = ref(null)

async function run() {
  loading.value = true
  error.value = ''
  result.value = null
  try {
    result.value = await api.playlists(text.value)
  } catch (e) {
    error.value = String(e)
  } finally {
    loading.value = false
  }
}

function copy(t) {
  navigator.clipboard?.writeText(t)
}
</script>

<template>
  <v-container style="max-width: 900px">
    <div class="text-h6 mb-1">Generate YouTube playlist links</div>
    <div class="text-caption text-grey mb-4">
      Paste YouTube URLs or 11-char video ids (one per line). Each group of 50 becomes a
      <code>watch_videos</code> playlist URL — open it in a browser and YouTube turns it into a
      real playlist. This is the original <code>playlist_generator.py</code>, in the browser.
    </div>

    <v-textarea v-model="text" rows="10" variant="outlined" density="compact"
      label="URLs or video ids, one per line"
      placeholder="https://www.youtube.com/watch?v=dQw4w9WgXcQ&#10;https://youtu.be/abcdefghijk&#10;..." />

    <v-btn color="primary" :loading="loading" :disabled="!text.trim()" @click="run">
      Generate
    </v-btn>

    <v-alert v-if="error" type="error" class="mt-4" closable @click:close="error=''">
      {{ error }}
    </v-alert>

    <template v-if="result">
      <v-alert :type="result.id_count ? 'success' : 'warning'" variant="tonal" class="mt-4">
        <span v-if="result.id_count">
          Found <strong>{{ result.id_count }}</strong> video ids ·
          {{ result.playlists.length }} playlist url(s)
        </span>
        <span v-else>No video ids found in the input.</span>
      </v-alert>

      <v-card v-if="result.playlists.length" class="mt-3 pa-3">
        <div class="d-flex align-center mb-2">
          <div class="text-subtitle-2">Playlist URLs</div>
          <v-spacer />
          <v-btn size="small" variant="text" @click="copy(result.playlists.join('\n'))">Copy all</v-btn>
        </div>
        <div v-for="(p, i) in result.playlists" :key="i" class="mb-2 d-flex align-center" style="gap:8px">
          <v-chip size="x-small" label>{{ i + 1 }}</v-chip>
          <a :href="p" target="_blank" class="text-caption text-truncate" style="max-width:100%">{{ p }}</a>
          <v-btn size="x-small" variant="text" icon="mdi-content-copy" @click="copy(p)" />
        </div>
      </v-card>
    </template>
  </v-container>
</template>
