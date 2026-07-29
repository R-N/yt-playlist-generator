import { readFile, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const defaultManifestPath = resolve('android', 'app', 'src', 'main', 'AndroidManifest.xml')

export function hardenManifest(manifest) {
  const applicationPattern = /<application\b[^>]*>/
  const application = manifest.match(applicationPattern)?.[0]
  if (!application) {
    throw new Error('Android manifest application element not found.')
  }

  let hardenedApplication = /\bandroid:allowBackup\s*=\s*(?:"[^"]*"|'[^']*')/.test(application)
    ? application.replace(/\bandroid:allowBackup\s*=\s*(?:"[^"]*"|'[^']*')/, 'android:allowBackup="false"')
    : application.replace('<application', '<application android:allowBackup="false"')
  hardenedApplication = /\bandroid:usesCleartextTraffic\s*=\s*(?:"[^"]*"|'[^']*')/.test(hardenedApplication)
    ? hardenedApplication.replace(/\bandroid:usesCleartextTraffic\s*=\s*(?:"[^"]*"|'[^']*')/, 'android:usesCleartextTraffic="true"')
    : hardenedApplication.replace('<application', '<application android:usesCleartextTraffic="true"')
  return manifest.replace(application, hardenedApplication)
}

export async function hardenManifestFile(manifestPath = defaultManifestPath) {
  let manifest
  try {
    manifest = await readFile(manifestPath, 'utf8')
  } catch (error) {
    if (error.code === 'ENOENT') throw new Error(`Android manifest not found: ${manifestPath}`)
    throw error
  }
  await writeFile(manifestPath, hardenManifest(manifest), 'utf8')
  return manifestPath
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  await hardenManifestFile()
  console.log(`Hardened ${defaultManifestPath}`)
}
