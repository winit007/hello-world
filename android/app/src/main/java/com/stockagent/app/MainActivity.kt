package com.stockagent.app

import android.Manifest
import android.annotation.SuppressLint
import android.app.Activity
import android.app.AlertDialog
import android.content.Context
import android.content.Intent
import android.content.res.Configuration
import android.graphics.Color
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.PowerManager
import android.provider.Settings
import android.view.Gravity
import android.view.ViewGroup
import android.webkit.CookieManager
import android.webkit.WebChromeClient
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import kotlin.concurrent.thread

/** The window: a full-screen view of the app's own server. Everything else runs in AgentRuntime / the Python app. */
class MainActivity : Activity() {
    private lateinit var web: WebView
    private lateinit var splash: LinearLayout
    private lateinit var splashText: TextView
    private lateinit var retry: Button
    private var waiting = false

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val night = (resources.configuration.uiMode and Configuration.UI_MODE_NIGHT_MASK) == Configuration.UI_MODE_NIGHT_YES
        val bg = if (night) Color.parseColor("#141414") else Color.parseColor("#f5f5f0")
        val fg = if (night) Color.parseColor("#e8e8e8") else Color.parseColor("#222222")

        val root = FrameLayout(this).apply { setBackgroundColor(bg) }
        web = WebView(this).apply {
            setBackgroundColor(bg)
            visibility = android.view.View.INVISIBLE
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            settings.mediaPlaybackRequiresUserGesture = true
            settings.cacheMode = WebSettings.LOAD_DEFAULT
            settings.setSupportZoom(false)
            settings.setSupportMultipleWindows(false)          // window.open (broker log-in pages) opens in this view
            settings.javaScriptCanOpenWindowsAutomatically = true
            webViewClient = object : WebViewClient() {
                override fun shouldOverrideUrlLoading(view: WebView, request: android.webkit.WebResourceRequest): Boolean {
                    val u = request.url
                    return if (u.scheme == "http" || u.scheme == "https") false else {      // tel:, mailto:, intent: ...
                        try { startActivity(Intent(Intent.ACTION_VIEW, u)) } catch (_: Exception) {}
                        true
                    }
                }
            }
            webChromeClient = WebChromeClient()
        }
        CookieManager.getInstance().setAcceptCookie(true)
        CookieManager.getInstance().setAcceptThirdPartyCookies(web, false)
        splashText = TextView(this).apply { setTextColor(fg); textSize = 16f; gravity = Gravity.CENTER; setPadding(48, 24, 48, 24) }
        retry = Button(this).apply { text = "Try again"; visibility = android.view.View.GONE; setOnClickListener { waitForServer() } }
        splash = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
            addView(TextView(this@MainActivity).apply { text = "Stock Agent"; setTextColor(fg); textSize = 26f; gravity = Gravity.CENTER })
            addView(splashText)
            addView(retry)
        }
        root.addView(web, ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
        root.addView(splash, ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
        setContentView(root)

        Notifier.init(this)
        AgentRuntime.start(this)
        try {
            startForegroundService(Intent(this, AgentService::class.java))
        } catch (_: Exception) {        // the server still runs while the window is open
        }
        askOnce()
        waitForServer()
    }

    /** Waits for the Python server, then opens it with the private key (which sets a cookie the page keeps). */
    private fun waitForServer() {
        if (waiting) return
        waiting = true
        retry.visibility = android.view.View.GONE
        thread(isDaemon = true) {
            val deadline = System.currentTimeMillis() + 240_000
            while (AgentRuntime.port == 0 && AgentRuntime.error == null && System.currentTimeMillis() < deadline) {
                runOnUiThread { splashText.text = AgentRuntime.status }
                Thread.sleep(400)
            }
            runOnUiThread {
                waiting = false
                val err = AgentRuntime.error
                if (AgentRuntime.port == 0) {
                    splashText.text = err ?: "The app did not start in time."
                    retry.visibility = android.view.View.VISIBLE
                } else {
                    web.loadUrl("http://127.0.0.1:${AgentRuntime.port}/?key=${AgentRuntime.key}")
                    web.visibility = android.view.View.VISIBLE
                    splash.visibility = android.view.View.GONE
                }
            }
        }
    }

    /** Asks once for notifications (so alerts can reach you) and to stay running in the background. */
    private fun askOnce() {
        val prefs = getSharedPreferences("agent", Context.MODE_PRIVATE)
        if (prefs.getBoolean("asked", false)) return
        prefs.edit().putBoolean("asked", true).apply()
        if (Build.VERSION.SDK_INT >= 33) requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
        val pm = getSystemService(PowerManager::class.java)
        if (!pm.isIgnoringBatteryOptimizations(packageName)) {
            AlertDialog.Builder(this)
                .setTitle("Keep Stock Agent running?")
                .setMessage("So that stop-loss alerts and the evening scans keep working while the screen is off, allow Stock Agent to run without battery limits.")
                .setPositiveButton("Allow") { _, _ ->
                    try {
                        startActivity(Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:$packageName")))
                    } catch (_: Exception) {
                    }
                }
                .setNegativeButton("Not now", null)
                .show()
        }
    }

    @Deprecated("Deprecated in Java")
    override fun onBackPressed() {
        if (web.visibility == android.view.View.VISIBLE && web.canGoBack()) web.goBack()
        else moveTaskToBack(true)          // leave the server running
    }

    override fun onResume() {
        super.onResume()
        web.onResume()
        web.evaluateJavascript("if (window.loadAlerts) loadAlerts();", null)
    }

    override fun onPause() {
        web.onPause()
        super.onPause()
    }
}
