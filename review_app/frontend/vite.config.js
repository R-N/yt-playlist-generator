import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import vuetify from 'vite-plugin-vuetify'

export default defineConfig({
  plugins: [vue(), vuetify({ autoImport: true })],
  server: {
    port: 5173,
    // Forward API + audio calls to the FastAPI backend so the <audio> tag and
    // fetch() can use relative URLs. Range requests pass through unchanged.
    proxy: {
      '/api': 'http://localhost:8000',
    },
  },
})
