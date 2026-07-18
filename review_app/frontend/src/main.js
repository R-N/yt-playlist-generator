import { createApp } from 'vue'
import { createVuetify } from 'vuetify'
import 'vuetify/styles'
import '@mdi/font/css/materialdesignicons.css'
import App from './App.vue'
import './app.css'

// Tuned dark theme so the app reads as a product, not raw Vuetify defaults.
const dark = {
  dark: true,
  colors: {
    background: '#131017',
    surface: '#1C1A23',
    'surface-bright': '#26232F',
    primary: '#B69CFF',
    secondary: '#80CBC4',
    success: '#5FBF72',
    error: '#F0616A',
    warning: '#F5B14A',
    info: '#6FB6F2',
  },
}

const vuetify = createVuetify({
  theme: { defaultTheme: 'dark', themes: { dark } },
  defaults: {
    VCard: { rounded: 'lg' },
    VBtn: { rounded: 'lg' },
    // Denser defaults across the app — the comfortable defaults read as mobile-sized.
    VList: { density: 'compact' },
    VTextField: { density: 'compact' },
    VSelect: { density: 'compact' },
    VDataTable: { density: 'compact' },
    VToolbar: { density: 'compact' },
  },
})

createApp(App).use(vuetify).mount('#app')
