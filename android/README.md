# Stock Agent for Android

The whole app on the phone, no PC and no Termux. Download `StockAgent.apk` from
<https://github.com/winit007/hello-world/releases/latest/download/StockAgent.apk>.

## How it works

- `MainActivity` is a WebView on `http://127.0.0.1:8765/?key=<random key>`; the page is the same single page as on the PC.
- `AgentService` is a foreground service (special-use type, with a small notification) that starts the Python server
  thread through [Chaquopy](https://chaquo.com/chaquopy/) (Python 3.11, numpy 1.26, pandas 2.1) and keeps it alive,
  so alerts and the evening scans keep running with the screen off.
- The server listens on 127.0.0.1 only and is locked with a key stored in the app: other apps on the phone get 403
  (`PHONE lock_local`). The page also needs the per-start `agent-token` for every API call.
- Python sources are copied from the APK's assets to `filesDir/py`; data lives in `filesDir/data`
  (`STOCK_AGENT_HOME`). `STOCK_AGENT_ANDROID=1` tells the code it runs in the app.
- Alerts are posted as Android notifications through `Notifier` (called from Python with `java.jclass`).

## Building

Done by `.github/workflows/android-app.yml`: unit tests on pandas 2.1.3, `assembleRelease` (arm64-v8a), an x86_64
debug build that is started in an emulator and checked by `android/ci/smoke_test.sh` (server up, locked, page served,
`/api/selftest` passes), then the release APK is attached to the release `app-v<version>`.

Locally: `gradle -p android assembleRelease -PVERSION_NAME=0.20.0 -PVERSION_CODE=2000` (JDK 17, Android SDK 34,
Gradle 8.9). Add `-PABIS=x86_64` for an emulator build. `versionCode = major*10000 + minor*100 + patch`.

## Signing

`stockagent.keystore` (password and alias `stockagent`) is committed on purpose: every build is signed with the same
key, so a new version installs over the old one and keeps its data. It is not a secret and not a Play Store key.
