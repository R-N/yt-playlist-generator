import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from './api'
import {
  NATIVE_SERVER_URL_KEY,
  clearNativeServerUrl,
  getNativeServerUrl,
  normalizeNativeServerUrl,
  setNativeServerUrl,
} from './native'

let storage

beforeEach(() => {
  storage = new Map()
  vi.stubGlobal('localStorage', {
    getItem: (key) => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, String(value)),
    removeItem: (key) => storage.delete(key),
    clear: () => storage.clear(),
  })
})

afterEach(() => {
  localStorage.clear()
  vi.unstubAllGlobals()
})

// Every literal address below is a test fixture. No backend address is
// hardcoded in shipped code — the user enters it and it is validated by range.
describe('native server URLs', () => {
  it('accepts private IPv4 HTTP addresses and valid HTTPS hosts', () => {
    expect(normalizeNativeServerUrl('http://10.0.0.8:8000/')).toBe('http://10.0.0.8:8000')
    expect(normalizeNativeServerUrl('http://172.16.4.20')).toBe('http://172.16.4.20')
    expect(normalizeNativeServerUrl('http://192.168.1.20:8000')).toBe('http://192.168.1.20:8000')
    expect(normalizeNativeServerUrl('https://server.test:8000/')).toBe('https://server.test:8000')
    expect(normalizeNativeServerUrl('https://203.0.113.20:8443')).toBe('https://203.0.113.20:8443')
  })

  it('rejects public, loopback, and link-local HTTP IPv4 addresses', () => {
    for (const address of ['8.8.8.8', '127.0.0.1', '169.254.10.20']) {
      expect(() => normalizeNativeServerUrl(`http://${address}:8000`)).toThrow()
    }
    expect(() => normalizeNativeServerUrl('http://server.test:8000')).toThrow()
  })

  it('rejects paths, credentials, localhost, and non-HTTP URLs', () => {
    expect(() => normalizeNativeServerUrl('https://server.test:8000/api')).toThrow()
    expect(() => normalizeNativeServerUrl('https://user:pass@server.test:8000')).toThrow()
    expect(() => normalizeNativeServerUrl('https://localhost:8000')).toThrow()
    expect(() => normalizeNativeServerUrl('ftp://server.test:8000')).toThrow()
  })

  it('re-validates the persisted address instead of trusting storage', () => {
    // localStorage is not a trust boundary: a value written before a rule
    // tightened, or by anything else on the device, must not become a base URL.
    for (const tampered of ['http://8.8.8.8:8000', 'https://server.test/api', 'not a url', '']) {
      localStorage.setItem(NATIVE_SERVER_URL_KEY, tampered)
      expect(getNativeServerUrl()).toBe('')
    }
  })

  it('fails closed when native runtime has no usable server address', () => {
    vi.stubGlobal('window', { Capacitor: { isNativePlatform: () => true } })
    localStorage.setItem(NATIVE_SERVER_URL_KEY, 'http://8.8.8.8:8000')

    // A relative fallback would resolve against the WebView origin, so throw.
    expect(() => api.audioUrl('abcdefghijk')).toThrow(/Connect to FastAPI server/)
  })

  it('stores only normalized addresses and clears on demand', () => {
    expect(setNativeServerUrl(' http://192.168.1.20:8000/ ')).toBe('http://192.168.1.20:8000')
    expect(localStorage.getItem(NATIVE_SERVER_URL_KEY)).toBe('http://192.168.1.20:8000')

    expect(() => setNativeServerUrl('http://8.8.8.8:8000')).toThrow()
    expect(localStorage.getItem(NATIVE_SERVER_URL_KEY)).toBe('http://192.168.1.20:8000')

    clearNativeServerUrl()
    expect(getNativeServerUrl()).toBe('')
  })

  it('uses persisted native server base for audio URLs', () => {
    vi.stubGlobal('window', { Capacitor: { isNativePlatform: () => true } })
    localStorage.setItem(NATIVE_SERVER_URL_KEY, 'http://192.168.1.20:8000/')

    expect(api.audioUrl('abcdefghijk')).toBe('http://192.168.1.20:8000/api/audio/abcdefghijk')
    expect(api.localAudioUrl('music folder', 'song & name.mp3')).toBe(
      'http://192.168.1.20:8000/api/local-audio?folder_identity=music%20folder&relative_path=song%20%26%20name.mp3',
    )
  })

  it('keeps browser audio URLs relative', () => {
    vi.stubGlobal('window', { Capacitor: { isNativePlatform: () => false } })
    localStorage.setItem(NATIVE_SERVER_URL_KEY, 'http://server.test:8000')

    expect(api.audioUrl('abcdefghijk')).toBe('/api/audio/abcdefghijk')
  })
})
