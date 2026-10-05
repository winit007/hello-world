package com.stockagent.app

import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat

/** Keeps the app's server alive when the window is closed, so evening scans and alerts still run. */
class AgentService : Service() {
    override fun onCreate() {
        super.onCreate()
        Notifier.init(this)
        val n = NotificationCompat.Builder(this, Notifier.CHANNEL_SERVICE)
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setContentTitle("Stock Agent is running")
            .setContentText("Watching your stops and running the evening scans. Tap to open.")
            .setContentIntent(Notifier.openApp(this))
            .setOngoing(true)
            .build()
        if (Build.VERSION.SDK_INT >= 34) startForeground(1, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        else startForeground(1, n)
        AgentRuntime.start(this)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int = START_STICKY

    override fun onBind(intent: Intent?): IBinder? = null
}
