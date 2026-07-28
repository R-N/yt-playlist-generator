import type { CapacitorConfig } from '@capacitor/cli'

const config: CapacitorConfig = {
  appId: 'com.linearch.ytplaylistgenerator',
  appName: 'YT Playlist Generator',
  webDir: 'dist',
  plugins: {
    CapacitorHttp: {
      enabled: true,
    },
  },
  // Development/LAN only. Cleartext sends traffic without transport encryption.
  // androidScheme:'http' makes the WebView origin http so <audio>/<img> can load
  // plaintext LAN URLs (CapacitorHttp only covers fetch/XHR, not media subresources).
  server: {
    androidScheme: 'http',
    cleartext: true,
  },
}

export default config
