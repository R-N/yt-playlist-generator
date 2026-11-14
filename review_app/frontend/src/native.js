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

// Expands a bracket-stripped IPv6 literal (handles `::` shorthand) into 8 16-bit
// groups, or null if it isn't a valid IPv6 literal. No zone-id support (URL hostnames
// don't carry one in practice here).
function expandIpv6(raw) {
  const parts = raw.split('::')
  if (parts.length > 2) return null
  const head = parts[0] ? parts[0].split(':') : []
  const tail = parts.length === 2 && parts[1] ? parts[1].split(':') : []
  if (parts.length === 1 && head.length !== 8) return null
  const missing = 8 - head.length - tail.length
  if (parts.length === 2 && missing < 0) return null
  const groups = parts.length === 2 ? [...head, ...Array(missing).fill('0'), ...tail] : head
  if (groups.length !== 8) return null
  const nums = groups.map((g) => parseInt(g || '0', 16))
  return nums.every((n) => Number.isInteger(n) && n >= 0 && n <= 0xffff) ? nums : null
}

// Loopback (::1), unique local (fc00::/7), and link-local (fe80::/10) — the IPv6
// equivalents of loopback/RFC1918's "this is a LAN device, not the public internet".
function isPrivateIpv6(hostname) {
  if (!hostname.startsWith('[') || !hostname.endsWith(']')) return false
  const nums = expandIpv6(hostname.slice(1, -1))
  if (!nums) return false
  const isLoopback = nums.slice(0, 7).every((n) => n === 0) && nums[7] === 1
  const isUla = (nums[0] & 0xfe00) === 0xfc00
  const isLinkLocal = (nums[0] & 0xffc0) === 0xfe80
  return isLoopback || isUla || isLinkLocal
}

export function normalizeNativeServerUrl(value) {
  const url = new URL(String(value).trim())
  const hostname = url.hostname.toLowerCase()
  if (!['http:', 'https:'].includes(url.protocol) || !hostname || url.username || url.password ||
      url.pathname !== '/' || url.search || url.hash || hostname === 'localhost' ||
      (url.protocol === 'http:' && !isPrivateRfc1918(hostname) && !isPrivateIpv6(hostname))) {
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
