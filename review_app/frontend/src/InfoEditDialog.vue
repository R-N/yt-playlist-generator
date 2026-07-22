<script setup>
// Row metadata viewer + editor. Shows every field the row carries; in edit mode the
// whitelisted `editable` keys become inputs. Emits `save` with only the edited fields.
import { ref, computed, watch } from 'vue'
import { api } from './api'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  title: { type: String, default: 'Metadata' },
  data: { type: Object, default: () => ({}) },
  editable: { type: Array, default: () => [] },
})
const emit = defineEmits(['update:modelValue', 'save', 'error'])

const editing = ref(false)
const form = ref({})
const rows = computed(() => Object.entries(props.data)
  .filter(([, v]) => v !== null && v !== undefined && v !== '')
  .map(([k, v]) => [k, typeof v === 'object' ? JSON.stringify(v) : String(v)]))

watch(() => props.modelValue, (open) => {
  if (open) { editing.value = false; form.value = Object.fromEntries(props.editable.map((k) => [k, props.data[k] ?? ''])) }
})
function save() { emit('save', { ...form.value }); editing.value = false }

const romanizing = ref(false)
async function romanize() {
  const keys = props.editable.filter((k) => form.value[k])
  if (!keys.length) return
  romanizing.value = true
  try {
    const { texts } = await api.romanize(keys.map((k) => String(form.value[k])))
    keys.forEach((k, i) => { form.value[k] = texts[i] })
  } catch (e) { emit('error', String(e)) } finally { romanizing.value = false }
}
</script>

<template>
  <v-dialog :model-value="modelValue" max-width="640" scrollable @update:model-value="$emit('update:modelValue', $event)">
    <v-card>
      <v-card-title class="d-flex align-center ga-1">
        <span class="text-truncate" style="min-width:0">{{ title }}</span>
        <v-spacer />
        <v-btn v-if="!editing && editable.length" icon="mdi-pencil" size="small" variant="text" class="flex-shrink-0" aria-label="Edit" @click="editing = true" />
        <template v-else-if="editing">
          <v-btn size="small" variant="text" prepend-icon="mdi-syllabary-hiragana" :loading="romanizing" @click="romanize">Romanize</v-btn>
          <v-btn size="small" variant="text" @click="editing = false">Cancel</v-btn>
          <v-btn size="small" color="primary" @click="save">Save</v-btn>
        </template>
        <v-btn icon="mdi-close" variant="text" size="small" class="ml-1" aria-label="Close" @click="$emit('update:modelValue', false)" />
      </v-card-title>
      <v-divider />
      <v-card-text>
        <div v-if="editing" class="mb-4">
          <v-text-field v-for="k in editable" :key="k" v-model="form[k]" :label="k" density="compact" variant="outlined" hide-details class="mb-2" />
        </div>
        <v-table density="compact">
          <tbody>
            <tr v-for="[k, v] in rows" :key="k">
              <td class="text-medium-emphasis" style="width:38%;vertical-align:top">{{ k }}</td>
              <td style="word-break:break-word">{{ v }}</td>
            </tr>
          </tbody>
        </v-table>
      </v-card-text>
    </v-card>
  </v-dialog>
</template>
