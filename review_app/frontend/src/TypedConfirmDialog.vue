<script setup>
// Shared destructive-action gate: a dialog whose confirm button unlocks only
// after the user types the confirm word (default DELETE). Body content is a
// slot, so each caller supplies its own explanation / target list. Used by the
// mp3-folder delete, download cleanup, and maintenance flows so the safety gate
// is defined once. The parent still owns the actual delete on @confirm.
import { ref, watch } from 'vue'
const props = defineProps({
  modelValue: { type: Boolean, default: false },
  title: { type: String, default: '' },
  confirmWord: { type: String, default: 'DELETE' },
  confirmLabel: { type: String, default: 'Delete files' },
  loading: { type: Boolean, default: false },
  busy: { type: Boolean, default: false },   // extra caller-side disable
})
const emit = defineEmits(['update:modelValue', 'confirm'])
const typed = ref('')
watch(() => props.modelValue, (open) => { if (open) typed.value = '' })
</script>

<template>
  <v-dialog :model-value="modelValue" max-width="520" persistent @update:model-value="$emit('update:modelValue', $event)">
    <v-card>
      <v-card-title class="text-error">{{ title }}</v-card-title>
      <v-card-text>
        <slot />
        <v-text-field v-model="typed" :label="`Type ${confirmWord} to confirm`" autocomplete="off" autofocus />
      </v-card-text>
      <v-card-actions>
        <v-spacer />
        <v-btn variant="text" :disabled="loading" @click="$emit('update:modelValue', false)">Cancel</v-btn>
        <v-btn color="error" :loading="loading" :disabled="loading || busy || typed !== confirmWord" @click="$emit('confirm')">{{ confirmLabel }}</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>
