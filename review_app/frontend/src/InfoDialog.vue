<script setup>
// Shared "File info" dialog. modelValue is { title, lines: [[label, value], …] }
// or null when closed. Used by Workspace and Library.
defineProps({ modelValue: { type: Object, default: null } })
defineEmits(['update:modelValue'])
</script>

<template>
  <v-dialog :model-value="!!modelValue" max-width="480" @update:model-value="$event || $emit('update:modelValue', null)">
    <v-card v-if="modelValue">
      <v-card-title>{{ modelValue.title }}</v-card-title>
      <v-card-text><div v-for="line in modelValue.lines" :key="line[0]" class="d-flex justify-space-between py-1 ga-4"><span class="text-medium-emphasis">{{ line[0] }}</span><span class="text-truncate text-right">{{ line[1] }}</span></div></v-card-text>
      <v-card-actions><v-spacer /><v-btn variant="text" @click="$emit('update:modelValue', null)">Close</v-btn></v-card-actions>
    </v-card>
  </v-dialog>
</template>
