package com.stockagent.app

import android.content.Context
import android.util.Log
import com.chaquo.python.Python
import java.io.File
import java.security.SecureRandom
import kotlin.concurrent.thread

/** Unpacks the Python app, starts its server in a background thread and tells the window where it listens. */
object AgentRuntime {
    private const val TAG = "StockAgent"

    @Volatile var status = "Starting…"
    @Volatile var error: String? = null
    @Volatile var port = 0
    @Volatile private var started = false
    lateinit var key: String
        private set

    @Synchronized
    fun start(context: Context) {
        if (started) return
        started = true
        val app = context.applicationContext
        key = loadKey(app)
        thread(name = "stock-agent-server", isDaemon = true) {
            try {
                status = "Unpacking the app…"
                val py = File(app.filesDir, "py")
                unpack(app, py)
                status = "Starting Python (the first start takes about a minute)…"
                val entry = Python.getInstance().getModule("entry")
                thread(name = "stock-agent-port", isDaemon = true) {
                    while (port == 0 && error == null) {
                        try {
                            port = entry.callAttr("port").toInt()
                        } catch (_: Throwable) {
                        }
                        if (port == 0) Thread.sleep(400)
                    }
                    status = "Ready"
                }
                entry.callAttr("start", py.absolutePath, File(app.filesDir, "data").absolutePath, key)   // blocks while serving
            } catch (e: Throwable) {
                Log.e(TAG, "server failed", e)
                error = (e.message ?: e.toString()).take(600)
            }
        }
    }

    /** A random key made once and kept private to this app: only the app's own window knows it. */
    private fun loadKey(app: Context): String {
        if (BuildConfig.DEBUG) return "debug-key-for-the-build-test-0123456789abcdef"
        val prefs = app.getSharedPreferences("agent", Context.MODE_PRIVATE)
        prefs.getString("key", null)?.let { return it }
        val bytes = ByteArray(24).also { SecureRandom().nextBytes(it) }
        val k = bytes.joinToString("") { "%02x".format(it) }
        prefs.edit().putString("key", k).apply()
        return k
    }

    /** Copies the Python files out of the APK when this version has not been unpacked yet. */
    private fun unpack(app: Context, dest: File) {
        val stamp = File(dest, ".version")
        val version = "${BuildConfig.VERSION_CODE}-${BuildConfig.VERSION_NAME}"
        if (stamp.exists() && stamp.readText() == version && File(dest, "stock_agent/__init__.py").exists()) return
        dest.deleteRecursively()
        copyAsset(app, "stock_agent", File(dest, "stock_agent"))
        stamp.writeText(version)
    }

    private fun copyAsset(app: Context, path: String, out: File) {
        val children = app.assets.list(path) ?: emptyArray()
        if (children.isEmpty()) {                       // a file
            out.parentFile?.mkdirs()
            app.assets.open(path).use { input -> out.outputStream().use { input.copyTo(it) } }
        } else {
            out.mkdirs()
            for (c in children) copyAsset(app, "$path/$c", File(out, c))
        }
    }
}
