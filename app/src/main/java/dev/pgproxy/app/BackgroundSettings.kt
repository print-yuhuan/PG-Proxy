package dev.pgproxy.app

import android.app.Activity
import android.app.ActivityManager
import android.app.NotificationManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.PowerManager
import android.provider.Settings
import dev.pgproxy.app.ui.BackgroundUiState

enum class BackgroundDestination { NOTIFICATIONS, BATTERY, APP_DETAILS }

/** Public Android settings only; OEM-specific power switches cannot be read reliably. */
object BackgroundSettings {
    fun read(context: Context) = BackgroundUiState(
        notificationsAllowed = runCatching {
            context.getSystemService(NotificationManager::class.java)?.let { manager ->
                val channel = manager.getNotificationChannel("proxy")
                val groupBlocked = channel?.group?.let { manager.getNotificationChannelGroup(it)?.isBlocked } == true
                manager.areNotificationsEnabled() && channel?.importance != NotificationManager.IMPORTANCE_NONE && !groupBlocked
            }
        }.getOrNull(),
        batteryExempt = runCatching {
            context.getSystemService(PowerManager::class.java)?.isIgnoringBatteryOptimizations(context.packageName)
        }.getOrNull(),
        backgroundRestricted = runCatching {
            context.getSystemService(ActivityManager::class.java)?.isBackgroundRestricted
        }.getOrNull(),
    )

    fun open(activity: Activity, destination: BackgroundDestination): Boolean {
        val packageUri = Uri.fromParts("package", activity.packageName, null)
        val appDetails = Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, packageUri)
        val candidates = when (destination) {
            BackgroundDestination.NOTIFICATIONS -> listOf(
                Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS).putExtra(Settings.EXTRA_APP_PACKAGE, activity.packageName),
                appDetails,
            )
            BackgroundDestination.BATTERY -> buildList {
                // Only a user tap requests this exemption. Returning from Settings re-reads the actual result.
                if (read(activity).batteryExempt != true) add(Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, packageUri))
                add(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS))
                add(appDetails)
            }
            BackgroundDestination.APP_DETAILS -> listOf(appDetails)
        }
        return launchFirstAvailable(candidates + Intent(Settings.ACTION_SETTINGS)) { activity.startActivity(it) }
    }

    internal fun launchFirstAvailable(candidates: List<Intent>, launch: (Intent) -> Unit): Boolean {
        for (intent in candidates) {
            try {
                launch(intent)
                return true
            } catch (_: RuntimeException) {
                // Missing, disabled or vendor-restricted settings screens fall back without a crash.
            }
        }
        return false
    }
}
