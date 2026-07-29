import { readFile, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const defaultManifestPath = resolve('android', 'app', 'src', 'main', 'AndroidManifest.xml')
const defaultBuildGradlePath = resolve('android', 'app', 'build.gradle')
const signingMarker = '// yt-playlist-generator release signing'

export function hardenManifest(manifest) {
  const applicationPattern = /<application\b[^>]*>/
  const application = manifest.match(applicationPattern)?.[0]
  if (!application) throw new Error('Android manifest application element not found.')

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

export function hardenBuildGradle(buildGradle) {
  const androidBlock = /\bandroid\s*\{/m
  if (!androidBlock.test(buildGradle)) throw new Error('Android build.gradle android block not found.')

  const signingCode = `
${signingMarker}
def signingProperties = new Properties()
def signingPropertiesFile = file("../../.android-signing/signing.properties")
if (signingPropertiesFile.isFile()) {
    signingPropertiesFile.withInputStream { signingProperties.load(it) }
}
def signingValue = { String name ->
    def value = System.getenv(name)
    if (value == null || value.trim().isEmpty()) value = signingProperties.getProperty(name)
    if (value == null || value.trim().isEmpty()) return ''
    value
}
def releaseSigning = [
    ANDROID_KEYSTORE_PATH: signingValue('ANDROID_KEYSTORE_PATH'),
    ANDROID_KEYSTORE_PASSWORD: signingValue('ANDROID_KEYSTORE_PASSWORD'),
    ANDROID_KEY_ALIAS: signingValue('ANDROID_KEY_ALIAS'),
    ANDROID_KEY_PASSWORD: signingValue('ANDROID_KEY_PASSWORD'),
]
def releaseVersionCode = signingValue('ANDROID_VERSION_CODE')
def releaseVersionName = signingValue('ANDROID_VERSION_NAME')
// Regexes here stay slashy and backslash-free: this file is a JS template, and
// JS eats unknown escapes before Groovy ever sees them.
def validateReleaseVersion = {
    def parts = releaseVersionName.tokenize('.')
    if (parts.size() != 3 || parts.any { !(it ==~ /[0-9]+/) }) {
        throw new GradleException("Release ANDROID_VERSION_NAME must be MAJOR.MINOR.PATCH: '\${releaseVersionName}'")
    }
    def major = parts[0].toLong(), minor = parts[1].toLong(), patch = parts[2].toLong()
    if (major > 2099L || minor > 999L || patch > 999L) {
        throw new GradleException("Release version out of range (max 2099.999.999): '\${releaseVersionName}'")
    }
    def expected = major * 1000000L + minor * 1000L + patch
    if (!(releaseVersionCode ==~ /[1-9][0-9]*/) || releaseVersionCode.toLong() != expected) {
        throw new GradleException("Release ANDROID_VERSION_CODE must be \${expected} for version \${releaseVersionName}")
    }
    if (expected < 1L || expected > 2100000000L) {
        throw new GradleException("Release version code out of range: \${expected}")
    }
}
def validateReleaseSigning = {
    releaseSigning.each { name, value ->
        if (value == null || value.trim().isEmpty()) throw new GradleException("Missing release signing value: \${name}")
    }
    validateReleaseVersion()
    def keystoreFile = file(releaseSigning.ANDROID_KEYSTORE_PATH)
    if (!keystoreFile.isFile()) throw new GradleException("Release keystore not found: \${keystoreFile}")
    def pinFile = file("../../release-cert-sha256.txt")
    if (!pinFile.isFile()) throw new GradleException("Release certificate pin not found: \${pinFile}")
    def pin = pinFile.getText('UTF-8').replaceAll(/[^0-9A-Fa-f]/, '').toUpperCase(Locale.ROOT)
    if (!(pin ==~ /[0-9A-F]{64}/)) throw new GradleException('Release certificate pin must be 64 hexadecimal characters.')
    def keyStore = java.security.KeyStore.getInstance('JKS')
    keystoreFile.withInputStream { keyStore.load(it, releaseSigning.ANDROID_KEYSTORE_PASSWORD.toCharArray()) }
    def certificate = keyStore.getCertificate(releaseSigning.ANDROID_KEY_ALIAS)
    if (certificate == null) throw new GradleException("Release keystore alias not found: \${releaseSigning.ANDROID_KEY_ALIAS}")
    def digest = java.security.MessageDigest.getInstance('SHA-256').digest(certificate.encoded)
        .collect { String.format('%02X', it & 0xff) }.join()
    if (digest != pin) throw new GradleException("Release certificate fingerprint differs from pin: \${digest}")
}
gradle.taskGraph.whenReady { taskGraph ->
    if (taskGraph.allTasks.any { task -> task.path.toLowerCase(Locale.ROOT).contains('release') }) validateReleaseSigning()
}
`
  const signingConfig = `
    signingConfigs {
        release {
            storeFile file(releaseSigning.ANDROID_KEYSTORE_PATH ?: '__missing_release_keystore__')
            storePassword releaseSigning.ANDROID_KEYSTORE_PASSWORD
            keyAlias releaseSigning.ANDROID_KEY_ALIAS
            keyPassword releaseSigning.ANDROID_KEY_PASSWORD
        }
    }
    buildTypes {
        release {
            debuggable false
            signingConfig signingConfigs.release
        }
    }
`
  if (buildGradle.includes(signingCode) && buildGradle.includes(signingConfig)) return buildGradle
  // ponytail: no in-place re-patching, a changed patch means regenerating the
  // ignored android/ project. CI always starts fresh; add surgery only if local
  // regeneration ever gets expensive.
  if (buildGradle.includes(signingMarker)) {
    throw new Error(
      'Android build.gradle carries an outdated signing patch. Delete review_app/frontend/android/ and re-run `npx cap add android`.',
    )
  }
  const versioned = buildGradle
    .replace(/\bversionCode\s+\d+/, 'versionCode releaseVersionCode ? releaseVersionCode.toInteger() : 1')
    .replace(/\bversionName\s+"[^"]*"/, 'versionName releaseVersionName ?: "1.0"')
  if (!versioned.includes('versionCode releaseVersionCode') || !versioned.includes('versionName releaseVersionName')) {
    throw new Error('Android build.gradle versionCode/versionName not found; release version cannot be pinned.')
  }
  return versioned.replace(androidBlock, (match) => `${signingCode}\n${match}${signingConfig}`)
}

export async function hardenBuildGradleFile(buildGradlePath = defaultBuildGradlePath) {
  let buildGradle
  try {
    buildGradle = await readFile(buildGradlePath, 'utf8')
  } catch (error) {
    if (error.code === 'ENOENT') throw new Error(`Android build.gradle not found: ${buildGradlePath}`)
    throw error
  }
  await writeFile(buildGradlePath, hardenBuildGradle(buildGradle), 'utf8')
  return buildGradlePath
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  await hardenManifestFile()
  await hardenBuildGradleFile()
  console.log(`Hardened ${defaultManifestPath} and ${defaultBuildGradlePath}`)
}
