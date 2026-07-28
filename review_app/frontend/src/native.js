// Capacitor exposes this global without requiring Capacitor as a frontend dependency.
export function isNativeRuntime() {
  return typeof window !== 'undefined' && window.Capacitor?.isNativePlatform?.() === true
}

export const NATIVE_SERVER_URL_KEY = 'music-curator.native-server-url'

export function normalizeNativeServerUrl(value) {
  const url = new URL(String(value).trim())
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password ||
      url.pathname !== '/' || url.search || url.hash) {
    throw new Error('Enter an http:// or https:// address without a path, query, or hash.')
  }
  return url.toString().replace(/\/$/, '')
}

export function getNativeServerUrl() {
  try {
    const value = localStorage.getItem(NATIVE_SERVER_URL_KEY)
    return value ? normalizeNativeServerUrl(value) : ''
  } catch {
    return ''
  }
}

export function setNativeServerUrl(value) {
  const normalized = normalizeNativeServerUrl(value)
  localStorage.setItem(NATIVE_SERVER_URL_KEY, normalized)
  return normalized
}

export function clearNativeServerUrl() {
  localStorage.removeItem(NATIVE_SERVER_URL_KEY)
}
