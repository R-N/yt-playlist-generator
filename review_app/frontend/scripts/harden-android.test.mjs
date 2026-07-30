import { mkdtemp, readFile, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import {
  hardenBuildGradle,
  hardenManifest,
  hardenManifestFile,
} from './harden-android.mjs'

// Mirrors the shape Capacitor generates: the patch has to find defaultConfig's
// versionCode/versionName to pin a release version.
const buildGradleFixture = [
  "plugins { id 'com.android.application' }",
  '',
  'android {',
  "    namespace 'example'",
  '    defaultConfig {',
  '        versionCode 1',
  '        versionName "1.0"',
  '    }',
  '}',
  '',
].join('\n')

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

  it('overrides the attributes Capacitor already emits', () => {
    // Capacitor's template ships android:allowBackup="true", so the replace
    // branch — not the insert branch — is the one that runs in a real project.
    const generated = [
      '<manifest xmlns:android="http://schemas.android.com/apk/res/android">',
      '    <application',
      '        android:allowBackup="true"',
      '        android:usesCleartextTraffic="false"',
      '        android:label="@string/app_name">',
      '    </application>',
      '</manifest>',
    ].join('\n')
    const hardened = hardenManifest(generated)

    expect(hardened).toContain('android:allowBackup="false"')
    expect(hardened).not.toContain('android:allowBackup="true"')
    expect(hardened).toContain('android:usesCleartextTraffic="true"')
    expect(hardened).not.toContain('android:usesCleartextTraffic="false"')
    expect(hardened.match(/android:allowBackup=/g)).toHaveLength(1)
    expect(hardened.match(/android:usesCleartextTraffic=/g)).toHaveLength(1)
    expect(hardened).toContain('android:label="@string/app_name"')
    expect(hardenManifest(hardened)).toBe(hardened)
  })

  it('fails clearly when application element is missing', () => {
    expect(() => hardenManifest('<manifest></manifest>')).toThrow(/Android manifest application element not found/)
  })

  it('patches generated build.gradle and remains idempotent', () => {
    const hardened = hardenBuildGradle(buildGradleFixture)
    expect(hardened).toContain('file("../../.android-signing/signing.properties")')
    expect(hardened).toMatch(/taskGraph\.allTasks[\s\S]*contains\('release'\)/)
    expect(hardened).not.toContain('gradle.startParameter.taskNames')
    expect(hardened).toContain('    value\n')
    expect(hardened).toMatch(/signingConfigs \{[\s\S]*storeFile file\(releaseSigning\.ANDROID_KEYSTORE_PATH/)
    expect(hardened).toMatch(/buildTypes \{[\s\S]*debuggable false[\s\S]*signingConfig signingConfigs\.release/)
    // Groovy reads '\s' as a space escape, so the pin must be stripped with a slashy regex.
    expect(hardened).toContain("replaceAll(/[^0-9A-Fa-f]/, '')")
    expect(hardened).not.toContain("replaceAll('\\s', '')")
    // This file is a JS template: JS strips unknown escapes before Groovy sees
    // them, so the emitted Gradle must contain no backslash at all.
    expect(hardened).not.toContain('\\')
    expect(hardened).toContain('versionCode releaseVersionCode ? releaseVersionCode.toInteger() : 1')
    expect(hardened).toContain('versionName releaseVersionName ?: "1.0"')
    expect(hardened).not.toMatch(/versionCode\s+1$/m)
    expect(hardened).toMatch(/validateReleaseVersion\(\)/)
    expect(hardenBuildGradle(hardened)).toBe(hardened)
  })

  it('refuses to patch when defaultConfig has no version fields to pin', () => {
    const fixture = 'android {\n    namespace \'example\'\n}\n'
    expect(() => hardenBuildGradle(fixture)).toThrow(/versionCode\/versionName not found/)
  })

  it('rejects an outdated signing patch instead of leaving it stale', () => {
    const stale = hardenBuildGradle(buildGradleFixture).replace('debuggable false\n', '')
    expect(() => hardenBuildGradle(stale)).toThrow(/outdated signing patch/)
  })

  it('fails clearly when build.gradle android block is missing', () => {
    expect(() => hardenBuildGradle('plugins { id \'com.android.application\' }')).toThrow(/Android build\.gradle android block not found/)
  })
})
