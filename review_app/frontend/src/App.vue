<script setup>
import { ref } from 'vue'
import ReviewTab from './ReviewTab.vue'
import DiscordTab from './DiscordTab.vue'
import PipelineTab from './PipelineTab.vue'
import SettingsTab from './SettingsTab.vue'

const tab = ref('review')
</script>

<template>
  <v-app>
    <v-app-bar density="comfortable" title="Music Toolkit">
      <template #append>
        <v-tabs v-model="tab" density="compact">
          <v-tab value="review">Review</v-tab>
          <v-tab value="discord">Discord</v-tab>
          <v-tab value="pipeline">Pipeline</v-tab>
          <v-tab value="settings">Settings</v-tab>
        </v-tabs>
      </template>
    </v-app-bar>

    <v-main>
      <v-window v-model="tab">
        <v-window-item value="review" :eager="true"><ReviewTab /></v-window-item>
        <v-window-item value="discord"><DiscordTab /></v-window-item>
        <v-window-item value="pipeline"><PipelineTab /></v-window-item>
        <v-window-item value="settings"><SettingsTab /></v-window-item>
      </v-window>
    </v-main>
  </v-app>
</template>
