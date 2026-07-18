<script setup>
import WorkspaceTab from './WorkspaceTab.vue'
import ImportTab from './ImportTab.vue'
import ReviewTab from './ReviewTab.vue'
import LibraryTab from './LibraryTab.vue'
import ActivityTab from './ActivityTab.vue'
import SettingsTab from './SettingsTab.vue'
import { activeTab as tab, PRIMARY_NAV } from './nav'
import { ref } from 'vue'
import { useDisplay } from 'vuetify'

const navigation = PRIMARY_NAV.filter((item) => item.value !== 'settings')
const { mobile } = useDisplay()
// Desktop drawer must start open; temporary mobile drawer starts closed.
const drawer = ref(!mobile.value)
const rail = ref(false)   // desktop: collapse sidebar to icons
function go(value) { tab.value = value; if (mobile.value) drawer.value = false }
</script>

<template>
  <v-app>
    <v-app-bar v-if="mobile" flat density="comfortable" color="surface">
      <v-app-bar-nav-icon aria-label="Open navigation" @click="drawer = true" />
      <v-toolbar-title><v-icon color="primary" class="mr-2">mdi-music-circle</v-icon>Music Curator</v-toolbar-title>
    </v-app-bar>
    <v-navigation-drawer v-model="drawer" :temporary="mobile" :permanent="!mobile" :rail="!mobile && rail" width="224" color="surface">
      <v-list nav>
        <v-list-item prepend-icon="mdi-music-circle" :title="rail ? '' : 'Music Curator'" style="cursor:pointer" :aria-label="rail ? 'Expand sidebar' : 'Collapse sidebar'" @click="rail = !rail">
          <template #append><v-icon v-if="!rail" size="small">mdi-backburger</v-icon></template>
        </v-list-item>
      </v-list>
      <v-list nav density="comfortable">
        <v-list-subheader v-if="!rail">WORKSPACE</v-list-subheader>
        <v-list-item v-for="item in navigation" :key="item.value" :active="tab === item.value" @click="go(item.value)"
          :prepend-icon="item.icon" :title="item.label" rounded="lg" />
      </v-list>
      <template #append><v-list nav density="comfortable"><v-list-item :active="tab === 'settings'" @click="go('settings')" prepend-icon="mdi-tune" title="Settings" rounded="lg" /></v-list></template>
    </v-navigation-drawer>
    <v-main>
      <v-window v-model="tab">
        <v-window-item value="import" :eager="true"><v-container class="tab-wrap" fluid><ImportTab /></v-container></v-window-item>
        <v-window-item value="workspace" :eager="true"><v-container class="tab-wrap" fluid><WorkspaceTab /></v-container></v-window-item>
        <v-window-item value="library" :eager="true"><v-container class="tab-wrap" fluid><LibraryTab /></v-container></v-window-item>
        <v-window-item value="review" :eager="true"><v-container class="tab-wrap" fluid><ReviewTab /></v-container></v-window-item>
        <v-window-item value="activity"><v-container class="tab-wrap" fluid><ActivityTab /></v-container></v-window-item>
        <v-window-item value="settings"><v-container class="tab-wrap" fluid><SettingsTab /></v-container></v-window-item>
      </v-window>
    </v-main>
  </v-app>
</template>
