# Android wrapper

## Setup and build

From `review_app/frontend`:

```text
npm install
npm run build
npx cap add android
npm run android:sync
android\gradlew.bat assembleDebug
```

Repeatable build after `android/` exists:

```text
npm run android:build
```

Debug artifact: `android/app/build/outputs/apk/debug/app-debug.apk`.

`android/` is generated and ignored; config, scripts, and docs are tracked. Do
not commit generated Android files. `android:sync` runs `android:prepare`, which patches
generated `AndroidManifest.xml` to set `android:allowBackup="false"`; it fails
if expected manifest/application structure is missing. If `android/` is absent,
run `npx cap add android` first.

Preparation sets `android:allowBackup="false"` and enables cleartext traffic for
private trusted-LAN use. HTTP remains deliberately unencrypted;
HTTP is unencrypted and backend has no authentication. Keep server access off
internet and untrusted networks. Release signing is external and no signing
keys or credentials belong in this repository.

For Android Studio debugging, choose `app` configuration, select device, then
run the `debug` variant. Use Logcat for device logs.

## Development on a phone

`server.cleartext` is enabled for trusted-LAN use only. Native `http://` backend
addresses must be literal RFC1918 IPv4 addresses (`10/8`, `172.16/12`, or
`192.168/16`), with optional port. HTTP traffic is unencrypted, and backend
has no authentication. Do not expose it to internet or untrusted networks.
Use validated HTTPS hostname/IP beyond trusted LAN; HTTPS does not make this
unauthenticated backend safe against every threat.

`capacitor.config.ts` also sets `server.androidScheme: 'http'` so the WebView
origin is `http`. `CapacitorHttp` routes `fetch`/XHR through the native stack,
but `<audio>`/`<img>` are subresource loads it does not intercept — an `https`
origin would block those plaintext LAN media URLs as mixed content, so audio
playback needs the `http` scheme.

Launch backend on server machine:

```text
cd review_app
python run.py --host 0.0.0.0
```

Phone and server must use same Wi-Fi. On first app launch, enter server
address in connection screen, for example `http://192.168.1.20:8000`.
Use server machine's LAN IP, not `localhost` (which means phone itself).
