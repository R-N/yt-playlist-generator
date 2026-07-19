<script setup>
// Force-set with verification, shared by "Set YouTube link…" (paste id/URL, verify it's
// alive) and "Pick local file…" (native picker, file already chosen + scored). Both show
// the match score and require confirmation. State lives in useForceSet; this is the view.
import { computed } from 'vue'
import { formatViews } from './workspace'

const props = defineProps({ state: { type: Object, required: true } })
const emit = defineEmits(['update:open', 'update:value', 'apply'])

const s = computed(() => props.state)
const canApply = computed(() => props.state.mode === 'youtube'
  ? !!props.state.value?.trim()
  : !!props.state.resolved?.exists)
const applyLabel = computed(() => props.state.mode !== 'youtube' ? 'Set file'
  : (props.state.resolved?.alive ? 'Confirm & set' : 'Set link'))
const scoreColor = (n) => (n >= 100 ? 'success' : n >= 40 ? 'primary' : n >= 0 ? 'warning' : 'error')
const healthColor = { ok: 'success', dead: 'error', private: 'error', unknown: 'warning' }
</script>

<template>
  <v-dialog :model-value="s.open" max-width="560" @update:model-value="$emit('update:open', $event)">
    <v-card>
      <v-card-title class="d-flex align-center">
        {{ s.mode === 'youtube' ? 'Set YouTube link' : 'Pick local file' }}
        <v-spacer /><v-btn icon="mdi-close" variant="text" size="small" aria-label="Close" @click="$emit('update:open', false)" />
      </v-card-title>
      <v-divider />
      <v-card-text>
        <template v-if="s.mode === 'youtube'">
          <v-text-field :model-value="s.value" density="compact" variant="solo-filled" flat hide-details
            placeholder="Paste YouTube URL or 11-char id" prepend-inner-icon="mdi-youtube" class="mb-2"
            @update:model-value="$emit('update:value', $event)" @keyup.enter="canApply && $emit('apply')" />
          <div class="text-caption text-medium-emphasis mb-2">Verified on save — a dead or private link is refused.</div>
          <v-alert v-if="s.error" type="error" variant="tonal" density="compact" class="mb-2">{{ s.error }}</v-alert>
          <v-card v-if="s.resolved && s.resolved.alive" variant="tonal" class="pa-3">
            <div class="d-flex align-center ga-2 mb-1">
              <v-chip size="small" :color="scoreColor(s.resolved.score)" variant="flat">score {{ s.resolved.score }}</v-chip>
              <v-chip size="small" :color="healthColor[s.resolved.health] || 'grey'" variant="tonal">{{ s.resolved.health }}</v-chip>
              <span class="text-caption text-medium-emphasis">{{ s.resolved.id }}</span>
            </div>
            <div class="font-weight-medium">{{ s.resolved.title || '(no title)' }}</div>
            <div class="text-caption text-medium-emphasis">{{ s.resolved.channel || '—' }}<span v-if="s.resolved.view_count"> · {{ formatViews(s.resolved.view_count) }}</span></div>
            <div class="text-caption text-medium-emphasis mt-1">Confirm to set this link.</div>
          </v-card>
        </template>
        <template v-else>
          <div class="d-flex align-center ga-2 mb-1">
            <v-chip size="small" :color="scoreColor(s.score)" variant="flat">score {{ s.score }}</v-chip>
            <v-chip size="small" color="success" variant="tonal">file exists</v-chip>
          </div>
          <div class="font-weight-medium">{{ s.basename }}</div>
          <div class="text-caption text-medium-emphasis" style="word-break:break-all">{{ s.path }}</div>
        </template>
      </v-card-text>
      <v-card-actions>
        <v-spacer />
        <v-btn variant="text" @click="$emit('update:open', false)">Cancel</v-btn>
        <v-btn color="primary" :disabled="!canApply" :loading="s.resolving" @click="$emit('apply')">{{ applyLabel }}</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>
