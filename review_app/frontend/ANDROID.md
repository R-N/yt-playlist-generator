# Android wrapper

## Setup and build

Prerequisites: Node.js 22 or newer, Java 21 (Temurin recommended), and the
Android SDK. Gradle needs the SDK location: set `ANDROID_HOME` (for example
`C:/Android/Sdk`) or write `sdk.dir` into `android/local.properties`.
`ANDROID_HOME` survives the regenerated `android/`, so prefer it.

From `review_app/frontend`:

```text
npm ci
npm run build
npx cap add android
npm run android:sync
```

`android/` is generated and ignored. Do not commit generated Android files,
JKS files, passwords, or other credentials. `android:prepare` patches generated
`AndroidManifest.xml` (`allowBackup="false"`, trusted-LAN
`usesCleartextTraffic="true"`) and `android/app/build.gradle` (release signing,
`debuggable false`) after every Capacitor sync.

The patch does not rewrite itself in place. After changing
`scripts/harden-android.mjs`, delete `android/` and re-run `npx cap add android`
— an out-of-date patch fails the build loudly instead of building something
half-hardened. CI always starts from a fresh platform, so it never hits this.

`npm run test` covers the hardening and the address rules: the manifest patch
against both an attribute-free `<application>` and the one Capacitor really
generates (`android:allowBackup="true"`, so the override branch is exercised,
not just the insert branch), the emitted Gradle staying backslash-free,
idempotency, the stale-patch and missing-version failures, and
`normalizeNativeServerUrl` accepting/rejecting addresses plus failing closed on
a tampered stored value. The Gradle certificate and version checks and the
workflow's tag gate are not unit-testable; they are verified by an actual
release build.

Debug keeps Android's default debug signing, whose keystore is generated per
machine — a local debug APK and a CI debug APK never share a certificate. Use
the release build whenever the signature has to match:

```text
npm run android:debug
```

## Release signing

Local and CI builds sign with **one** keystore, so both APKs carry the same
certificate and can replace each other on a device. The keystore lives in the
ignored `review_app/frontend/.android-signing/` directory; CI receives the same
file as a base64 secret. Key at
`review_app/frontend/.android-signing/yt-playlist-generator-release.jks` and
create `review_app/frontend/.android-signing/signing.properties` with exactly:

```properties
ANDROID_KEYSTORE_PATH=../../.android-signing/yt-playlist-generator-release.jks
ANDROID_KEYSTORE_PASSWORD=your-keystore-password
ANDROID_KEY_ALIAS=your-key-alias
ANDROID_KEY_PASSWORD=your-key-password
ANDROID_VERSION_NAME=1.0.0
ANDROID_VERSION_CODE=1000000
```

Environment variables with those exact names override properties-file values.
Release tasks fail if any value, JKS, alias, certificate pin, certificate match,
or version is missing.

`ANDROID_VERSION_CODE` must equal `major * 1000000 + minor * 1000 + patch` of
`ANDROID_VERSION_NAME`, which must be `MAJOR.MINOR.PATCH` within
`2099.999.999`. Android refuses to install an APK over one with a higher
versionCode, so the derived code keeps local and CI builds of the same version
interchangeable and later versions always installable. Debug builds ignore both
values and stay at `1` / `1.0`.

Passwords must be plain alphanumeric. `.properties` parsing strips line
terminators and honours backslash escapes, so a password containing `\`, a
trailing `\r`, or leading whitespace desyncs the file from the keystore and
Gradle reports only "password was incorrect".

The tracked `review_app/frontend/release-cert-sha256.txt` holds the signing
certificate's real public SHA-256 fingerprint,
`27909AD551B7AD9A72ECC8724FF28B343D995A6276A5DD27285C3E9E783ED7EE`, as 64
uppercase hexadecimal characters without colons or whitespace. Gradle loads the
configured alias and refuses to build a release whose certificate differs; CI
additionally re-reads the fingerprint out of the finished APK with
`apksigner verify --print-certs`. It is a public value — do not replace it with
a placeholder or a secret. Verify a keystore's fingerprint with:

```text
keytool -list -v -keystore .android-signing/yt-playlist-generator-release.jks -alias yt-playlist-generator
```

Only `.android-signing/` is ignored; never commit JKS files, passwords, or other
private credentials.

**Back the keystore up off-device.** Losing it means no signed update can ever
replace an installed APK — the app has to be uninstalled first.

Build signed release locally:

```text
npm run android:build
```

The release APK is
`android/app/build/outputs/apk/release/app-release.apk`.

`.github/workflows/android-apk.yml` is manual-dispatch only, runs only on the
default branch, and takes a required `MAJOR.MINOR.PATCH` version input. It
derives the versionCode, rejects a version that is not greater than every
existing `vMAJOR.MINOR.PATCH` tag, decodes the JKS only into runner temporary
storage at mode 600, and fails the run if the built APK's certificate does not
match the tracked pin.

Its four secrets — `ANDROID_KEYSTORE_BASE64` (base64 of the exact same JKS),
`ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_ALIAS`, `ANDROID_KEY_PASSWORD` — live
in the **`android-release` Environment**, not at repository level, so no other
workflow in the repo can read the signing key. The Environment is restricted to
the `main` branch. Never print secret values. Base64 for the secret:

```powershell
[Convert]::ToBase64String([IO.File]::ReadAllBytes("review_app/frontend/.android-signing/yt-playlist-generator-release.jks")) | Set-Clipboard
```

## Development on a phone

**No backend address is baked into the APK.** The user types it on first launch
and it is kept in `localStorage`; `normalizeNativeServerUrl` in `src/native.js`
validates it by *range* (RFC1918) rather than against any specific host, so a
DHCP-assigned server address needs no rebuild and no re-signing. Literal
addresses appear only in this file's examples and in `src/native.test.js`
fixtures — never in shipped configuration. Keep it that way: pinning one host
would mean re-signing the APK whenever the LAN address changed, and Android's
Network Security Config cannot express a CIDR range to do it at the OS layer
either.

`server.cleartext` is enabled for trusted-LAN use only. Native `http://` backend
addresses must be literal RFC1918 IPv4 addresses (`10/8`, `172.16/12`, or
`192.168/16`), with optional port. HTTP is unencrypted, and backend has no
authentication. Do not expose it to internet or untrusted networks. Use
validated HTTPS hostname/IP beyond trusted LAN; HTTPS does not make this
unauthenticated backend safe against every threat.

`capacitor.config.ts` sets `server.androidScheme: 'http'` so WebView origin is
`http`. `CapacitorHttp` routes fetch/XHR through native stack, but audio/image
subresources are not intercepted; HTTPS origin would block plaintext LAN media.

Launch backend on server machine:

```text
cd review_app
python run.py --host 0.0.0.0
```

Phone and server must use same Wi-Fi. On first app launch, enter server address,
for example `http://192.168.1.20:8000`. Use server machine LAN IP, not
`localhost`.
