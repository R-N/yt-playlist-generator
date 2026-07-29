import { mkdtemp, readFile, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import {
  hardenBuildGradle,
  hardenManifest,
  hardenManifestFile,
} from './harden-android.mjs'

describe('Android hardening', () => {
  it('patches generated manifest and remains idempotent', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'harden-android-'))
    const manifestPath = join(directory, 'AndroidManifest.xml')
    const fixture = '<manifest><application android:label="Curator"></application></manifest>'

    await writeFile(manifestPath, fixture)
    await hardenManifestFile(manifestPath)
    const hardened = await readFile(manifestPath, 'utf8')
    expect(hardened).toMatch(/android:allowBackup="false"/)
    expect(hardened).toMatch(/android:usesCleartextTraffic="true"/)
    expect(hardenManifest(hardened)).toBe(hardened)
  })

  it('fails clearly when application element is missing', () => {
    expect(() => hardenManifest('<manifest></manifest>')).toThrow(/Android manifest application element not found/)
  })

  it('patches generated build.gradle and remains idempotent', () => {
    const fixture = 'plugins { id \'com.android.application\' }\n\nandroid {\n    namespace \'example\'\n}\n'
    const hardened = hardenBuildGradle(fixture)
    expect(hardened).toContain('file("../../.android-signing/signing.properties")')
    expect(hardened).toMatch(/taskGraph\.allTasks[\s\S]*contains\('release'\)/)
    expect(hardened).not.toContain('gradle.startParameter.taskNames')
    expect(hardened).toContain('    value\n')
    expect(hardened).toMatch(/signingConfigs \{[\s\S]*storeFile file\(releaseSigning\.ANDROID_KEYSTORE_PATH/)
    expect(hardened).toMatch(/buildTypes \{[\s\S]*debuggable false[\s\S]*signingConfig signingConfigs\.release/)
    // Groovy reads '\s' as a space escape, so the pin must be stripped with a slashy regex.
    expect(hardened).toContain("replaceAll(/[^0-9A-Fa-f]/, '')")
    expect(hardened).not.toContain("replaceAll('\\s', '')")
    expect(hardenBuildGradle(hardened)).toBe(hardened)
  })

  it('rejects an outdated signing patch instead of leaving it stale', () => {
    const fixture = 'android {\n    namespace \'example\'\n}\n'
    const stale = hardenBuildGradle(fixture).replace('debuggable false\n', '')
    expect(() => hardenBuildGradle(stale)).toThrow(/outdated signing patch/)
  })

  it('fails clearly when build.gradle android block is missing', () => {
    expect(() => hardenBuildGradle('plugins { id \'com.android.application\' }')).toThrow(/Android build\.gradle android block not found/)
  })
})
