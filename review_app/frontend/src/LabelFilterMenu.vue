<script setup>
// Shared tri-state label filter menu for Workspace and Library. attrs is the
// subset of FILTER_ATTRS the screen supports; filter is the reactive state map.
defineProps({
  attrs: { type: Array, required: true },
  filter: { type: Object, required: true },
  count: { type: Number, default: 0 },
})
defineEmits(['cycle', 'clear'])
</script>

<template>
  <v-menu :close-on-content-click="false">
    <template #activator="{ props }">
      <v-btn icon variant="text" v-bind="props" aria-label="Filter"><v-icon>mdi-filter-variant</v-icon><span v-if="count" class="ml-1">{{ count }}</span><v-tooltip activator="parent" location="bottom">Filter</v-tooltip></v-btn>
    </template>
    <v-list density="compact" min-width="220">
      <v-list-subheader>Tap to cycle: ignore → must have → exclude</v-list-subheader>
      <v-list-item v-for="attr in attrs" :key="attr.key" @click="$emit('cycle', attr.key)">
        <template #prepend>
          <v-icon :color="filter[attr.key] === 1 ? 'success' : filter[attr.key] === -1 ? 'error' : undefined">
            {{ filter[attr.key] === 1 ? 'mdi-checkbox-marked' : filter[attr.key] === -1 ? 'mdi-close-box' : 'mdi-checkbox-blank-outline' }}
          </v-icon>
        </template>
        <v-list-item-title><v-icon size="16" class="mr-2">{{ attr.icon }}</v-icon>{{ attr.label }}</v-list-item-title>
      </v-list-item>
      <v-divider v-if="count" />
      <v-list-item v-if="count" title="Clear filters" prepend-icon="mdi-filter-remove-outline" @click="$emit('clear')" />
    </v-list>
  </v-menu>
</template>
