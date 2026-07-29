// Capacitor exposes this global without requiring Capacitor as a frontend dependency.
export function isNativeRuntime() {
  return typeof window !== 'undefined' && window.Capacitor?.isNativePlatform?.() === true
}

export const NATIVE_SERVER_URL_KEY = 'music-curator.native-server-url'

function isIpv4(hostname) {
  const octets = hostname.split('.')
  return octets.length === 4 && octets.every((octet) => /^(0|[1-9]\d{0,2})$/.test(octet) && Number(octet) <= 255)
}

function isPrivateRfc1918(hostname) {
  if (!isIpv4(hostname)) return false
  const [first, second] = hostname.split('.').map(Number)
  return first === 10 || (first === 172 && second >= 16 && second <= 31) || (first === 192 && second === 168)
}

export function normalizeNativeServerUrl(value) {
  const url = new URL(String(value).trim())
  const hostname = url.hostname.toLowerCase()
  if (!['http:', 'https:'].includes(url.protocol) || !hostname || url.username || url.password ||
      url.pathname !== '/' || url.search || url.hash || hostname === 'localhost' ||
      (url.protocol === 'http:' && !isPrivateRfc1918(hostname))) {
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
