<script setup>
// Renders the shared status labels (see labels.js) as icons. Used by Workspace
// and Library. Full label text (incl. duplicate counts) lives in the tooltip.
// Emits label-click so a parent can act (open link, toggle preview).
defineProps({ labels: { type: Array, default: () => [] } })
const emit = defineEmits(['label-click'])
</script>

<template>
  <span class="d-inline-flex align-center ga-1">
    <v-btn v-for="label in labels" :key="label.key" :color="label.color"
      size="small" variant="text" density="comfortable" :aria-label="label.label" :title="label.tooltip || label.label"
      @click.stop="emit('label-click', label, $event)">
      <v-icon size="22">{{ label.icon }}</v-icon>
    </v-btn>
  </span>
</template>
