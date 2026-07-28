import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from './api'
import { NATIVE_SERVER_URL_KEY, normalizeNativeServerUrl } from './native'

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

describe('native server URLs', () => {
  it('rejects paths, credentials, and non-HTTP URLs while normalizing base URLs', () => {
    expect(normalizeNativeServerUrl('https://server.test:8000/')).toBe('https://server.test:8000')
    expect(() => normalizeNativeServerUrl('https://server.test:8000/api')).toThrow()
    expect(() => normalizeNativeServerUrl('https://user:pass@server.test:8000')).toThrow()
    expect(() => normalizeNativeServerUrl('ftp://server.test:8000')).toThrow()
  })

  it('uses persisted native server base for audio URLs', () => {
    vi.stubGlobal('window', { Capacitor: { isNativePlatform: () => true } })
    localStorage.setItem(NATIVE_SERVER_URL_KEY, 'http://server.test:8000/')

    expect(api.audioUrl('abcdefghijk')).toBe('http://server.test:8000/api/audio/abcdefghijk')
    expect(api.localAudioUrl('music folder', 'song & name.mp3')).toBe(
      'http://server.test:8000/api/local-audio?folder_identity=music%20folder&relative_path=song%20%26%20name.mp3',
    )
  })

  it('keeps browser audio URLs relative', () => {
    vi.stubGlobal('window', { Capacitor: { isNativePlatform: () => false } })
    localStorage.setItem(NATIVE_SERVER_URL_KEY, 'http://server.test:8000')

    expect(api.audioUrl('abcdefghijk')).toBe('/api/audio/abcdefghijk')
  })
})
