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
  server: {
    cleartext: true,
  },
}

export default config
