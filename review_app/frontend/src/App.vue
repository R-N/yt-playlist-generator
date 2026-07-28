<script setup>
import WorkspaceTab from './WorkspaceTab.vue'
import ImportTab from './ImportTab.vue'
import ReviewTab from './ReviewTab.vue'
import LibraryTab from './LibraryTab.vue'
import ActivityTab from './ActivityTab.vue'
import SettingsTab from './SettingsTab.vue'
import { activeTab as tab, PRIMARY_NAV } from './nav'
import { computed, ref } from 'vue'
import { useDisplay } from 'vuetify'
import NativeConnection from './NativeConnection.vue'
import { clearNativeServerUrl, getNativeServerUrl, isNativeRuntime } from './native'

const navigation = PRIMARY_NAV.filter((item) => item.value !== 'settings')
const { mobile } = useDisplay()
const native = isNativeRuntime()
const serverUrl = ref(native ? getNativeServerUrl() : '')
const connected = computed(() => !native || Boolean(serverUrl.value))
// Desktop drawer must start open; temporary mobile drawer starts closed.
const drawer = ref(!mobile.value)
const rail = ref(false)   // desktop: collapse sidebar to icons
function go(value) { tab.value = value; if (mobile.value) drawer.value = false }
function connect(value) { serverUrl.value = value }
function forgetServer() { clearNativeServerUrl(); serverUrl.value = '' }
</script>

<template>
  <v-app>
    <v-app-bar v-if="mobile || native" flat density="comfortable" color="surface" class="mobile-app-bar">
      <v-app-bar-nav-icon v-if="connected" aria-label="Open navigation" @click="drawer = true" />
      <v-toolbar-title class="text-truncate"><v-icon color="primary" class="mr-2">mdi-music-circle</v-icon>Music Curator</v-toolbar-title>
      <v-btn v-if="native && connected" variant="text" size="small" prepend-icon="mdi-server-network" @click="forgetServer">Change server</v-btn>
    </v-app-bar>
    <v-navigation-drawer v-if="connected" v-model="drawer" :temporary="mobile" :permanent="!mobile" :rail="!mobile && rail" width="224" color="surface">
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
    <v-main class="app-main">
      <NativeConnection v-if="native && !connected" @connected="connect" />
      <v-window v-else v-model="tab">
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
