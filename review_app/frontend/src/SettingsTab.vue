<script setup>
import { ref, onMounted } from 'vue'
import { api } from './api'

const state = ref({})           // key -> {set, preview}
const form = ref({ DISCORD_BOT_TOKEN: '', DISCORD_CHANNEL_ID: '', ACOUSTID_API_KEY: '' })
const saving = ref(false)
const saved = ref(false)
const error = ref('')

async function load() {
  try {
    state.value = await api.getSettings()
    // prefill the non-secret channel id so it's editable in place
    form.value.DISCORD_CHANNEL_ID = state.value.DISCORD_CHANNEL_ID?.preview || ''
  } catch (e) {
    error.value = String(e)
  }
}

async function save() {
  saving.value = true
  saved.value = false
  error.value = ''
  // only send fields the user actually typed (blank secret = leave unchanged)
  const body = {}
  for (const [k, v] of Object.entries(form.value)) {
    if (v !== '') body[k] = v
  }
  try {
    state.value = await api.saveSettings(body)
    form.value.DISCORD_BOT_TOKEN = ''
    form.value.ACOUSTID_API_KEY = ''
    saved.value = true
  } catch (e) {
    error.value = String(e)
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<template>
  <v-container style="max-width: 720px">
    <div class="text-h6 mb-1">Settings</div>
    <div class="text-caption text-grey mb-4">
      Secrets are written to a <code>.env</code> file at the repo root (gitignored) and
      applied to the environment, so the scripts the app runs (downloader, AcoustID
      enrich, Discord fetch) inherit them. A real shell env var still overrides the file.
      Leave a secret blank to keep the current value.
    </div>

    <v-text-field v-model="form.DISCORD_BOT_TOKEN" label="Discord bot token"
      type="password" autocomplete="off"
      :placeholder="state.DISCORD_BOT_TOKEN?.set ? 'set ('+state.DISCORD_BOT_TOKEN.preview+') — leave blank to keep' : 'not set'"
      density="comfortable" class="mb-3" />
    <v-text-field v-model="form.DISCORD_CHANNEL_ID" label="Default Discord channel ID"
      density="comfortable" class="mb-3" />
    <v-text-field v-model="form.ACOUSTID_API_KEY" label="AcoustID API key"
      type="password" autocomplete="off"
      :placeholder="state.ACOUSTID_API_KEY?.set ? 'set ('+state.ACOUSTID_API_KEY.preview+') — leave blank to keep' : 'not set'"
      density="comfortable" class="mb-3" />

    <v-btn color="primary" :loading="saving" @click="save">Save</v-btn>
    <v-alert v-if="saved" type="success" variant="tonal" class="mt-4">Saved to .env</v-alert>
    <v-alert v-if="error" type="error" class="mt-4" closable @click:close="error=''">
      {{ error }}
    </v-alert>
  </v-container>
</template>
