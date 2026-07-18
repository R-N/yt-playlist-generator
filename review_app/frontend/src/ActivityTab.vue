<script setup>
// Activity log: background tasks (verify sweeps — running with progress + cancel,
// or finished with a result) and the append-only approve/reject decision history.
import { ref, onMounted, onUnmounted } from 'vue'
import { api } from './api'
import { activeTab } from './nav'

const sub = ref('tasks')
const tasks = ref([])
const history = ref([])
const cancelling = ref(null)
const error = ref('')

const KIND = {
  'library-verify': { icon: 'mdi-music-box-multiple', label: 'Library link verify' },
  'workspace-verify': { icon: 'mdi-youtube', label: 'Workspace link verify' },
}
function kind(t) { return KIND[t.kind] || { icon: 'mdi-cog', label: t.kind } }
const STATUS = { running: 'primary', done: 'success', error: 'error', cancelled: 'grey', interrupted: 'warning' }
function statusColor(s) { return STATUS[s] || 'grey' }
function pct(t) { return t.total ? Math.round((t.done / t.total) * 100) : 0 }
function glance(t) {
  const base = t.total ? `${t.done} / ${t.total}` : `${t.done} done`
  return t.status === 'running' ? `${base}${t.found ? ` · ${t.found} flagged` : ''}` : (t.message || base)
}
function fmt(ts) { return ts ? new Date(ts.replace(' ', 'T') + 'Z').toLocaleString() : '' }

async function loadTasks() {
  try { tasks.value = (await api.tasks()).tasks } catch (e) { error.value = String(e) }
}
async function loadHistory() {
  try { history.value = (await api.history()).decisions } catch (e) { error.value = String(e) }
}
async function cancel(t) {
  cancelling.value = t.id
  try { await api.taskCancel(t.id) } catch (e) { error.value = String(e) }
  finally { cancelling.value = null; loadTasks() }
}

let poll = null
onMounted(() => {
  loadTasks(); loadHistory()
  poll = setInterval(() => { if (activeTab.value === 'activity') loadTasks() }, 2000)
})
onUnmounted(() => clearInterval(poll))
</script>

<template>
  <div class="text-h4 mb-4">Activity</div>
  <v-alert v-if="error" type="error" closable class="mb-3" @click:close="error = ''">{{ error }}</v-alert>
  <v-card>
    <v-tabs v-model="sub" color="primary" grow>
      <v-tab value="tasks" prepend-icon="mdi-cog-sync-outline">Background tasks</v-tab>
      <v-tab value="history" prepend-icon="mdi-history">Decision history</v-tab>
    </v-tabs>
    <v-divider />
    <v-window v-model="sub">
      <v-window-item value="tasks">
        <div v-if="!tasks.length" class="text-medium-emphasis text-center pa-8">No background tasks yet.</div>
        <v-list v-else lines="two">
          <template v-for="t in tasks" :key="t.id">
            <v-list-item :title="t.title">
              <template #prepend><v-icon :color="t.status === 'running' ? 'primary' : undefined">{{ kind(t).icon }}</v-icon></template>
              <template #subtitle>
                <span>{{ kind(t).label }}</span>
                <span class="text-caption text-medium-emphasis ml-2">{{ glance(t) }}</span>
                <span v-if="t.started_at" class="text-caption text-medium-emphasis ml-2">· {{ fmt(t.started_at) }}</span>
              </template>
              <template #append>
                <v-chip size="x-small" :color="statusColor(t.status)" variant="tonal" class="mr-2">{{ t.status }}</v-chip>
                <v-btn v-if="t.status === 'running'" icon="mdi-close-circle-outline" size="small" variant="text" color="error"
                  aria-label="Cancel task" :loading="cancelling === t.id" @click="cancel(t)" />
              </template>
            </v-list-item>
            <v-progress-linear v-if="t.status === 'running'" :model-value="pct(t)" color="primary" height="2" />
          </template>
        </v-list>
      </v-window-item>

      <v-window-item value="history">
        <div class="d-flex align-center px-4 pt-3">
          <div class="text-body-2 text-medium-emphasis">Append-only approve / reject log (newest first).</div>
          <v-spacer />
          <v-btn size="small" variant="text" icon="mdi-refresh" aria-label="Refresh" @click="loadHistory" />
        </div>
        <div v-if="!history.length" class="text-medium-emphasis text-center pa-8">No decisions recorded yet.</div>
        <v-list v-else lines="two">
          <v-list-item v-for="d in history" :key="d.id" :title="d.title || d.yt_title || d.filename || `Track ${d.track_id}`">
            <template #prepend>
              <v-icon :color="d.decision === 1 ? 'success' : 'grey'">{{ d.decision === 1 ? 'mdi-check-decagram' : 'mdi-close-circle-outline' }}</v-icon>
            </template>
            <template #subtitle>
              <span>{{ d.decision === 1 ? 'Approved' : 'Rejected' }}</span>
              <span v-if="d.artist" class="text-caption text-medium-emphasis ml-2">· {{ d.artist }}</span>
              <span class="text-caption text-medium-emphasis ml-2">· {{ fmt(d.ts) }}</span>
            </template>
            <template #append>
              <a v-if="d.yt_id" :href="`https://www.youtube.com/watch?v=${d.yt_id}`" target="_blank" rel="noopener noreferrer" class="text-caption">#{{ d.yt_id }}</a>
            </template>
          </v-list-item>
        </v-list>
      </v-window-item>
    </v-window>
  </v-card>
</template>
