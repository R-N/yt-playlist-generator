<script setup>
import { ref } from 'vue'
import ReviewTab from './ReviewTab.vue'
import DiscordTab from './DiscordTab.vue'
import PlaylistTab from './PlaylistTab.vue'
import PipelineTab from './PipelineTab.vue'
import SettingsTab from './SettingsTab.vue'

const tab = ref('review')
const tools = [
  { value: 'discord', icon: 'mdi-discord', label: 'Discord harvest' },
  { value: 'playlist', icon: 'mdi-playlist-play', label: 'Playlist links' },
  { value: 'pipeline', icon: 'mdi-cog-play-outline', label: 'Pipeline jobs' },
]
</script>

<template>
  <v-app>
    <v-navigation-drawer permanent width="224" color="surface">
      <div class="brand">
        <v-icon color="primary" size="30">mdi-music-circle</v-icon>
        <div>
          <div class="brand-title">Music Curator</div>
          <div class="brand-sub">match review toolkit</div>
        </div>
      </div>

      <v-list nav density="comfortable">
        <v-list-subheader>CURATE</v-list-subheader>
        <v-list-item :active="tab==='review'" @click="tab='review'"
          prepend-icon="mdi-check-decagram" title="Review" rounded="lg"
          subtitle="approve / reject by ear" />

        <v-divider class="my-2" />
        <v-list-subheader>TOOLS</v-list-subheader>
        <v-list-item v-for="t in tools" :key="t.value"
          :active="tab===t.value" @click="tab=t.value"
          :prepend-icon="t.icon" :title="t.label" rounded="lg" />
      </v-list>

      <template #append>
        <v-list nav density="comfortable">
          <v-list-item :active="tab==='settings'" @click="tab='settings'"
            prepend-icon="mdi-tune" title="Settings" rounded="lg" />
        </v-list>
      </template>
    </v-navigation-drawer>

    <v-main>
      <v-window v-model="tab">
        <v-window-item value="review" :eager="true">
          <div class="tab-wrap"><ReviewTab /></div>
        </v-window-item>
        <v-window-item value="discord">
          <div class="tab-wrap"><DiscordTab /></div>
        </v-window-item>
        <v-window-item value="playlist">
          <div class="tab-wrap"><PlaylistTab /></div>
        </v-window-item>
        <v-window-item value="pipeline">
          <div class="tab-wrap"><PipelineTab /></div>
        </v-window-item>
        <v-window-item value="settings">
          <div class="tab-wrap"><SettingsTab /></div>
        </v-window-item>
      </v-window>
    </v-main>
  </v-app>
</template>
