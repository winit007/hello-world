package com.stockagent.app

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat

/** Shows the app's alerts (stop near, target hit, price alert, new penny picks) in the phone's notifications. */
object Notifier {
    const val CHANNEL_ALERTS = "alerts"
    const val CHANNEL_SERVICE = "service"
    private var ctx: Context? = null
    private var next = 100

    fun init(context: Context) {
        ctx = context.applicationContext
        if (Build.VERSION.SDK_INT >= 26) {
            val nm = ctx!!.getSystemService(NotificationManager::class.java)
            nm.createNotificationChannel(NotificationChannel(CHANNEL_ALERTS, "Alerts", NotificationManager.IMPORTANCE_HIGH).apply {
                description = "Stops, targets, price alerts and changes to your penny lists"
            })
            nm.createNotificationChannel(NotificationChannel(CHANNEL_SERVICE, "Running in the background", NotificationManager.IMPORTANCE_LOW).apply {
                description = "Keeps the scans and alerts going while the app is closed"
            })
        }
    }

    fun openApp(context: Context): PendingIntent = PendingIntent.getActivity(
        context, 0, Intent(context, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
    )

    /** Called from Python (alerts.py) through Chaquopy. */
    @JvmStatic
    fun post(title: String, text: String) {
        val c = ctx ?: return
        if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(c, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) return
        val n = NotificationCompat.Builder(c, CHANNEL_ALERTS)
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setContentTitle(title)
            .setContentText(text)
            .setStyle(NotificationCompat.BigTextStyle().bigText(text))
            .setContentIntent(openApp(c))
            .setAutoCancel(true)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .build()
        try {
            NotificationManagerCompat.from(c).notify(next++, n)
        } catch (_: SecurityException) {
        }
    }
}
