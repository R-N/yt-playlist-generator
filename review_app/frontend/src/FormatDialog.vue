<script setup>
// Shared audio-format chooser for every download button (bulk + YouTube-label single).
// Picks the codec the downloader re-encodes to; opus is the app default.
defineProps({ modelValue: Boolean, busy: Boolean, title: { type: String, default: 'Download audio' } })
const emit = defineEmits(['update:modelValue', 'pick'])
const FORMATS = [
  { value: 'opus', label: 'Opus', hint: 'default · smallest' },
  { value: 'mp3', label: 'MP3', hint: 'most compatible' },
  { value: 'm4a', label: 'M4A / AAC', hint: 'Apple-friendly' },
]
</script>

<template>
  <v-dialog :model-value="modelValue" max-width="440" @update:model-value="emit('update:modelValue', $event)">
    <v-card>
      <v-card-title>{{ title }}</v-card-title>
      <v-card-text class="pb-0">Choose an audio format. Downloads run in the background — track them in <strong>Activity</strong> / the run alert.</v-card-text>
      <v-list density="comfortable">
        <v-list-item v-for="f in FORMATS" :key="f.value" :disabled="busy" @click="emit('pick', f.value)">
          <v-list-item-title>{{ f.label }}</v-list-item-title>
          <v-list-item-subtitle>{{ f.hint }}</v-list-item-subtitle>
          <template #append><v-icon>mdi-download</v-icon></template>
        </v-list-item>
      </v-list>
      <v-card-actions>
        <v-spacer />
        <v-btn variant="text" :disabled="busy" @click="emit('update:modelValue', false)">Cancel</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>
