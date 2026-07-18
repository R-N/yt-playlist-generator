<script setup>
// Generic cursor-anchored context menu for label badges. items is a list of
// { action, icon, title, color? }; clicking one emits select(action). Shared by
// Workspace and Library so the YouTube/file/status menus never drift.
defineProps({
  modelValue: { type: Boolean, default: false },
  target: { type: [Array, Object], default: () => [0, 0] },
  items: { type: Array, default: () => [] },
})
defineEmits(['update:modelValue', 'select'])
</script>

<template>
  <v-menu :model-value="modelValue" :target="target" @update:model-value="$emit('update:modelValue', $event)">
    <v-list density="compact">
      <v-list-item v-for="it in items" :key="it.action" :prepend-icon="it.icon" :title="it.title" :base-color="it.color" @click="$emit('select', it.action)" />
    </v-list>
  </v-menu>
</template>
