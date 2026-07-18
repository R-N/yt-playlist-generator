<script setup>
// Shared by Library + Workspace Verify-links buttons. Verifying every link is a
// paced background task (rate-limit safe), so it asks scope up front: re-check
// ALL links, or only the ones not yet verified (fewer = faster).
defineProps({ modelValue: Boolean, busy: Boolean })
const emit = defineEmits(['update:modelValue', 'pick'])
function pick(scope) { emit('pick', scope) }
</script>

<template>
  <v-dialog :model-value="modelValue" max-width="440" @update:model-value="emit('update:modelValue', $event)">
    <v-card>
      <v-card-title>Verify links</v-card-title>
      <v-card-text>
        Runs as a background task with a paced, randomized delay so YouTube doesn't
        rate-limit. Track it under <strong>Activity</strong>.
      </v-card-text>
      <v-card-actions class="flex-wrap">
        <v-btn variant="text" @click="emit('update:modelValue', false)">Cancel</v-btn>
        <v-spacer />
        <v-btn variant="tonal" :loading="busy" @click="pick('unverified')">Only unverified</v-btn>
        <v-btn color="primary" variant="tonal" :loading="busy" @click="pick('all')">Verify all</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>
