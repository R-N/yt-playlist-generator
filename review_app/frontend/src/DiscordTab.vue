<script setup>
import { ref } from 'vue'
import { api } from './api'

const channelId = ref('')
const author = ref('')
const writeFiles = ref(true)
const loading = ref(false)
const error = ref('')
const result = ref(null)

async function run() {
  loading.value = true
  error.value = ''
  result.value = null
  try {
    result.value = await api.discordFetch({
      channel_id: channelId.value || null,
      author: author.value || null,
      write_files: writeFiles.value,
    })
  } catch (e) {
    error.value = String(e)
  } finally {
    loading.value = false
  }
}

function copy(text) {
  navigator.clipboard?.writeText(text)
}
</script>

<template>
  <v-container style="max-width: 900px">
    <div class="text-h6 mb-1">Harvest YouTube links from a Discord channel</div>
    <div class="text-caption text-grey mb-4">
      Pulls the channel's messages via the bot API, extracts every YouTube video id,
      and (optionally) writes ids.txt / urls.txt / playlists.txt at the repo root so
      the downloader and playlist flow can pick them up. Set the bot token on the
      Settings tab first.
    </div>

    <v-text-field v-model="channelId" label="Channel ID"
      hint="Discord → Developer Mode → right-click channel → Copy ID. Blank uses the saved default."
      persistent-hint density="comfortable" class="mb-3" />
    <v-text-field v-model="author" label="Only this author (optional)"
      hint="Username or nickname; blank = everyone in the channel."
      persistent-hint density="comfortable" class="mb-3" />
    <v-switch v-model="writeFiles" color="primary" hide-details
      label="Write pipeline files (ids.txt / urls.txt / playlists.txt)" class="mb-2" />

    <v-btn color="primary" :loading="loading" @click="run">Fetch &amp; extract</v-btn>

    <v-alert v-if="error" type="error" class="mt-4" closable @click:close="error=''">
      {{ error }}
    </v-alert>

    <template v-if="result">
      <v-alert type="success" variant="tonal" class="mt-4">
        Scanned {{ result.messages }} messages · found
        <strong>{{ result.count }}</strong> unique video ids ·
        {{ result.playlists.length }} playlist url(s)
        <span v-if="result.written.length"> · wrote {{ result.written.join(', ') }}</span>
      </v-alert>

      <v-card v-if="result.playlists.length" class="mt-3 pa-3">
        <div class="d-flex align-center mb-2">
          <div class="text-subtitle-2">Playlist URLs</div>
          <v-spacer />
          <v-btn size="small" variant="text" @click="copy(result.playlists.join('\n'))">Copy all</v-btn>
        </div>
        <div v-for="(p, i) in result.playlists" :key="i" class="mb-1">
          <a :href="p" target="_blank" class="text-caption">{{ p }}</a>
        </div>
      </v-card>

      <v-card v-if="result.ids.length" class="mt-3 pa-3">
        <div class="d-flex align-center mb-2">
          <div class="text-subtitle-2">Video IDs ({{ result.ids.length }})</div>
          <v-spacer />
          <v-btn size="small" variant="text" @click="copy(result.ids.join('\n'))">Copy</v-btn>
        </div>
        <v-textarea :model-value="result.ids.join('\n')" readonly rows="8"
          density="compact" hide-details variant="outlined" />
      </v-card>
    </template>
  </v-container>
</template>
