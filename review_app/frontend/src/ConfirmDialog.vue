<script setup>
// Plain yes/no confirm dialog (no typed gate — see TypedConfirmDialog for the
// destructive DELETE flow). Body is a slot; parent handles @confirm and owns the
// open state. Shared by the Library remove/download-delete and Settings
// empty-folders confirms.
defineProps({
  modelValue: { type: Boolean, default: false },
  title: { type: String, default: '' },
  confirmLabel: { type: String, default: 'Confirm' },
  confirmColor: { type: String, default: 'error' },
  loading: { type: Boolean, default: false },
  maxWidth: { type: [String, Number], default: 480 },
})
defineEmits(['update:modelValue', 'confirm'])
</script>

<template>
  <v-dialog :model-value="modelValue" :max-width="maxWidth" @update:model-value="$emit('update:modelValue', $event)">
    <v-card>
      <v-card-title :class="confirmColor === 'error' ? 'text-error' : ''">{{ title }}</v-card-title>
      <v-card-text><slot /></v-card-text>
      <v-card-actions>
        <v-spacer />
        <v-btn variant="text" :disabled="loading" @click="$emit('update:modelValue', false)">Cancel</v-btn>
        <v-btn :color="confirmColor" :loading="loading" @click="$emit('confirm')">{{ confirmLabel }}</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>
