<script setup>
// Progress/result alert for a single-item YouTube-label download run (Library/Review/
// Workspace). Reuses the same workspace_run shape as the bulk download. Dismissable once done.
import { ACTIVE_RUN_STATUSES } from './workspace'
defineProps({ run: { type: Object, default: null } })
const emit = defineEmits(['dismiss'])
</script>

<template>
  <v-alert v-if="run" :type="run.status === 'done' ? 'success' : run.status === 'failed' ? 'error' : 'info'"
    variant="tonal" class="mb-3" role="status"
    :closable="!ACTIVE_RUN_STATUSES.includes(run.status)" @click:close="emit('dismiss')">
    Download: <strong>{{ run.status }}</strong>. {{ run.error_text || `${run.items?.length || 0} item(s)` }}
  </v-alert>
</template>
