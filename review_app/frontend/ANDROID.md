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

Debug artifact: `android/app/build/outputs/apk/debug/app-debug.apk`.

`android/` is generated and ignored; config and docs are tracked. Do not commit
generated Android files. Run `npm run build` and `npm run android:sync` after
frontend changes. If `android/` is absent, run `npx cap add android` first.

For Android Studio debugging, choose `app` configuration, select device, then
run the `debug` variant. Use Logcat for device logs.

## Development on a phone

`server.cleartext` is enabled for development/LAN use only. Use trusted LAN
networks: HTTP traffic is unencrypted, and this backend has no authentication.
Do not expose it to the internet or an untrusted network. Prefer HTTPS beyond
trusted LAN.

Launch backend on server machine:

```text
cd review_app
python run.py --host 0.0.0.0
```

Phone and server must use same Wi-Fi. On first app launch, enter server
address in connection screen, for example `http://192.168.1.20:8000`.
Use server machine's LAN IP, not `localhost` (which means phone itself).
