import { strict as assert } from 'node:assert'
import { mkdtemp, readFile, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'
import { hardenManifest, hardenManifestFile } from './harden-android.mjs'

test('patches generated manifest and remains idempotent', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'harden-android-'))
  const manifestPath = join(directory, 'AndroidManifest.xml')
  const fixture = '<manifest><application android:label="Curator"></application></manifest>'

  await writeFile(manifestPath, fixture)
  await hardenManifestFile(manifestPath)
  const hardened = await readFile(manifestPath, 'utf8')
  assert.match(hardened, /android:allowBackup="false"/)
  assert.match(hardened, /android:usesCleartextTraffic="true"/)
  assert.equal(hardenManifest(hardened), hardened)
})

test('fails clearly when application element is missing', () => {
  assert.throws(
    () => hardenManifest('<manifest></manifest>'),
    /Android manifest application element not found/,
  )
})
