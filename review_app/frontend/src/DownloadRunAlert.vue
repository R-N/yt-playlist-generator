<script setup>
// Progress/result alert for a download run: the Workspace bulk run and the single-item
// YouTube-label run (Library/Review/Workspace) share the workspace_run shape. Dismissable
// once finished; an interrupted/failed/stopped run offers Resume (the backend re-verifies
// which files already landed and downloads only the rest).
import { ACTIVE_RUN_STATUSES } from './workspace'
defineProps({ run: { type: Object, default: null }, resuming: { type: Boolean, default: false } })
const emit = defineEmits(['dismiss', 'resume'])
const TYPE = { done: 'success', failed: 'error', interrupted: 'warning', stopped: 'warning' }
</script>

<template>
  <v-alert v-if="run" :type="TYPE[run.status] || 'info'"
    variant="tonal" class="mb-3" role="status"
    :closable="!ACTIVE_RUN_STATUSES.includes(run.status)" @click:close="emit('dismiss')">
    Download: <strong>{{ run.status }}</strong>. {{ run.error_text || `${run.items?.length || 0} item(s)` }}
    <template v-if="run.resumable" #append>
      <v-btn size="small" variant="tonal" prepend-icon="mdi-play-circle-outline" :loading="resuming"
        @click="emit('resume')">Resume</v-btn>
    </template>
  </v-alert>
</template>
