<script setup>
import { ref } from 'vue'
import { normalizeNativeServerUrl, setNativeServerUrl } from './native'

const emit = defineEmits(['connected'])
const address = ref('')
const error = ref('')

function connect() {
  error.value = ''
  try {
    emit('connected', setNativeServerUrl(address.value))
  } catch (cause) {
    error.value = cause.message || 'Enter a valid server address.'
  }
}

function onInput(value) {
  address.value = value
  if (error.value) {
    try { normalizeNativeServerUrl(value); error.value = '' } catch { /* retain message */ }
  }
}
</script>

<template>
  <v-container class="fill-height d-flex align-center justify-center py-8" fluid>
    <v-card max-width="560" width="100%" class="pa-5 pa-sm-8">
      <v-icon color="primary" size="44" class="mb-4">mdi-server-network</v-icon>
      <div class="text-overline text-primary">Native connection</div>
      <h1 class="text-h4 mb-3">Connect to Music Curator</h1>
      <p class="text-body-1 text-medium-emphasis mb-6">
        Enter FastAPI server address. Address stays on this device and is used for future connections.
      </p>
      <v-text-field
        :model-value="address"
        label="FastAPI base URL"
        placeholder="http://192.168.1.20:8000"
        hint="Use your computer's local-network IP, not localhost."
        persistent-hint
        prepend-inner-icon="mdi-link-variant"
        :error-messages="error"
        autocomplete="url"
        @update:model-value="onInput"
        @keyup.enter="connect"
      />
      <p class="text-caption text-medium-emphasis mt-4 mb-6">
        Phone and computer must share Wi-Fi. Use HTTPS when server is configured for HTTPS or traffic leaves your trusted local network.
        HTTP suits local development only when Android allows cleartext traffic.
      </p>
      <v-btn color="primary" size="large" block prepend-icon="mdi-connection" @click="connect">Connect</v-btn>
    </v-card>
  </v-container>
</template>
