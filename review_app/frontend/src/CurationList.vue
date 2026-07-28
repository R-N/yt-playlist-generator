<script setup>
// The one list look shared by Import (untracked), Workspace, and Library.
// Rows are normalized to a common shape; each screen supplies only its own
// actions/badge via slots and its own preview via :preview-for. Everything
// visual — card, row, checkbox, labels, inline media, pagination — lives here
// so the three screens can never drift apart again.
//
// Row shape: { key, raw, title, subtitle, subtitleHref?, labels?, selectable?, dead? }
//   key         selection value + :key (selection value == key on every screen)
//   raw         original item, handed back to slots/handlers untouched
//   selectable  false -> spacer instead of checkbox (saved links, dead items)
//   dead        strike the title + dim the row
import LabelRow from './LabelRow.vue'
import MediaPreview from './MediaPreview.vue'

defineProps({
  rows: { type: Array, required: true },              // the page currently shown
  selected: { type: Array, default: () => [] },
  previewFor: { type: Function, default: () => null }, // (row) -> preview object | null
  page: { type: Number, default: 1 },
  perPage: { type: Number, default: 50 },
  pageCount: { type: Number, default: 1 },
  hasItems: { type: Boolean, default: true },          // any items at all, pre-filter
})
const emit = defineEmits(['toggle', 'label', 'update:page', 'update:perPage'])
</script>

<template>
  <v-card variant="outlined" class="curation-list">
    <v-list v-if="rows.length" lines="two" density="compact">
      <template v-for="row in rows" :key="row.key">
        <v-list-item :class="{ 'is-dead': row.dead }">
          <template #prepend>
            <v-checkbox-btn v-if="row.selectable !== false" :model-value="selected.includes(row.key)" :disabled="row.dead" density="compact" :aria-label="`Select ${row.title}`" @update:model-value="emit('toggle', row)" />
            <span v-else class="select-spacer" />
          </template>
          <v-list-item-title class="font-weight-medium">
            <span :class="{ 'dead-text': row.dead }">{{ row.title }}</span>
            <slot name="badge" :row="row" />
          </v-list-item-title>
          <v-list-item-subtitle>
            <a v-if="row.subtitleHref" :href="row.subtitleHref" target="_blank" rel="noopener noreferrer">{{ row.subtitle }}</a>
            <span v-else :class="{ 'text-medium-emphasis': !row.subtitle }">{{ row.subtitle || '—' }}</span>
          </v-list-item-subtitle>
          <template #append>
            <LabelRow v-if="row.labels" :labels="row.labels" class="mx-1" @label-click="(label, ev) => emit('label', row, label, ev)" />
            <slot name="actions" :row="row" />
          </template>
        </v-list-item>
        <MediaPreview :preview="previewFor(row)" />
      </template>
    </v-list>
    <slot v-else-if="!hasItems" name="empty"><div class="pa-8 text-center text-medium-emphasis">Nothing here yet.</div></slot>
    <div v-else class="pa-8 text-center text-medium-emphasis">No items match this filter.</div>
    <div v-if="rows.length" class="d-flex align-center justify-space-between px-3 py-2 curation-foot">
      <v-select :model-value="perPage" :items="[25,50,100,200]" density="compact" variant="plain" hide-details style="max-width:88px" @update:model-value="emit('update:perPage', $event)" />
      <v-pagination :model-value="page" :length="pageCount" density="comfortable" :total-visible="6" @update:model-value="emit('update:page', $event)" />
    </div>
  </v-card>
</template>

<style scoped>
.curation-foot { border-top: 1px solid rgba(255, 255, 255, 0.08); }
.select-spacer { display: inline-block; width: 40px; }
.dead-text { text-decoration: line-through; opacity: 0.6; }
.is-dead { opacity: 0.75; }
@media (max-width: 599px) {
  .curation-list :deep(.v-list-item) { display: grid; grid-template-columns: auto minmax(0, 1fr); align-items: start; min-height: 72px; padding: 10px 8px; }
  .curation-list :deep(.v-list-item__prepend) { grid-column: 1; grid-row: 1; }
  .curation-list :deep(.v-list-item__content) { grid-column: 2; min-width: 0; }
  .curation-list :deep(.v-list-item__append) { grid-column: 2; width: 100%; min-width: 0; justify-content: flex-start; flex-wrap: wrap; margin-inline-start: 0; margin-top: 7px; }
  .curation-list :deep(.v-list-item-title), .curation-list :deep(.v-list-item-subtitle) { overflow-wrap: anywhere; }
  .curation-list :deep(.v-list-item-subtitle) { white-space: normal; }
  .curation-list :deep(.v-pagination__list) { flex-wrap: wrap; height: auto; gap: 2px; }
  .curation-foot { flex-wrap: wrap; gap: 4px 8px; padding-inline: 8px !important; }
  .curation-foot .v-pagination { margin-inline-start: auto; max-width: 100%; }
}
</style>
