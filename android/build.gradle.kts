// Stock Agent for Android: a thin app (a full-screen browser view and a background service) around the Python
// app, which runs inside it through Chaquopy. See android/README.md.
plugins {
    id("com.android.application") version "8.7.3" apply false
    id("org.jetbrains.kotlin.android") version "1.9.25" apply false
    id("com.chaquo.python") version "16.0.0" apply false
}
