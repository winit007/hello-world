plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("com.chaquo.python")
}

// The CI passes these; locally the defaults build an arm64 phone version.
val abis = ((findProperty("ABIS") as String?) ?: "arm64-v8a").split(",")
val appVersionName = (findProperty("VERSION_NAME") as String?) ?: "0.0.0"
val appVersionCode = ((findProperty("VERSION_CODE") as String?) ?: "1").toInt()

android {
    namespace = "com.stockagent.app"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.stockagent.app"
        minSdk = 24
        targetSdk = 34
        versionCode = appVersionCode
        versionName = appVersionName
        ndk { abiFilters += abis }
    }

    signingConfigs {
        // A fixed key, so a newer version installs over an older one without losing your data. It is in the
        // repository on purpose (this is a personal, side-loaded app, not a Play Store release).
        create("release") {
            storeFile = file("../stockagent.keystore")
            storeType = "pkcs12"
            storePassword = "stockagent"
            keyAlias = "stockagent"
            keyPassword = "stockagent"
        }
    }

    buildTypes {
        release {
            signingConfig = signingConfigs.getByName("release")
            isMinifyEnabled = false
        }
        debug {
            signingConfig = signingConfigs.getByName("release")
        }
    }

    buildFeatures { buildConfig = true }
    lint { checkReleaseBuilds = false; abortOnError = false }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
    packaging { jniLibs { useLegacyPackaging = true } }
}

chaquopy {
    defaultConfig {
        version = "3.11"
        pip {
            // The versions Chaquopy has built for Android (pandas 2.1.3 / numpy 1.26.2). The app's code runs the
            // same numbers on these and on current pandas (tests/ run on both).
            install("numpy==1.26.2")
            install("pandas==2.1.3")
            install("requests")
            install("feedparser")
            install("vaderSentiment")
            install("qrcode")
            install("tzdata")
        }
    }
}

// The Python app is shipped as plain files (assets) and unpacked on first start: it reads its own folder for
// the page, the NSE lists and the default settings, which needs real files rather than files inside the APK.
val copyPython = tasks.register<Copy>("copyPythonSources") {
    from("../../stock_agent") { exclude("**/__pycache__/**") }
    into(layout.buildDirectory.dir("generated/pyassets/stock_agent"))
}
android.sourceSets.getByName("main").assets.srcDir(layout.buildDirectory.dir("generated/pyassets"))
tasks.configureEach {
    if (name.startsWith("merge") && name.endsWith("Assets")) dependsOn(copyPython)
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
}
