package dev.pgproxy.app

import android.app.ActivityManager
import android.content.Context
import android.content.Intent
import android.os.PowerManager
import android.os.ParcelFileDescriptor
import android.os.SystemClock
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import java.io.File
import java.net.ServerSocket
import java.net.Socket
import java.util.UUID
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith

/** Short screen-off lifecycle check; never queries Google or changes saved account records. */
@RunWith(AndroidJUnit4::class)
class BackgroundRuntimeInstrumentedTest {
    private val instrumentation get() = InstrumentationRegistry.getInstrumentation()
    private val context: Context get() = instrumentation.targetContext

    @Test fun wakeLockFollowsTheRunningServiceAcrossAShortScreenOffInterval() {
        openApp()
        PrivateServiceClient(context).use { client ->
            val original = client.result("snapshot")
            val config = JSONObject().apply {
                for (key in listOf("port", "lan", "apiKey", "timeout", "queueTimeout", "keepAwake")) put(key, original.get(key))
            }
            val port = ServerSocket(0).use { it.localPort }
            try {
                client.result("stop")
                val configured = client.result("configure", JSONObject().put("port", port).put("lan", false)
                    .put("apiKey", "background-test-${UUID.randomUUID()}").put("keepAwake", true))
                assertFalse("An enabled preference must not hold a lock while stopped", configured.getBoolean("wakeLockHeld"))
                val started = client.result("start")
                assertTrue(started.getBoolean("running"))
                assertTrue("The system wake lock must actually be held", started.getBoolean("wakeLockHeld"))
                assertEquals("", started.getString("wakeLockError"))
                @Suppress("DEPRECATION")
                val foreground = context.getSystemService(ActivityManager::class.java).getRunningServices(50)
                    .any { it.service.className == ProxyService::class.java.name && it.foreground }
                assertTrue("The independent proxy process must be a foreground service", foreground)
                shell("input keyevent KEYCODE_HOME")
                shell("input keyevent KEYCODE_SLEEP")
                SystemClock.sleep(800)
                assertFalse(context.getSystemService(PowerManager::class.java).isInteractive)
                repeat(5) {
                    assertEquals(200, health(port))
                    assertTrue(client.result("snapshot").getBoolean("wakeLockHeld"))
                    SystemClock.sleep(2000)
                }
                val disabled = client.result("configure", JSONObject().put("keepAwake", false))
                assertTrue(disabled.getBoolean("running"))
                assertFalse("Disabling the option must release immediately", disabled.getBoolean("wakeLockHeld"))
                assertEquals(200, health(port))
                assertTrue(client.result("configure", JSONObject().put("keepAwake", true)).getBoolean("wakeLockHeld"))
                val stopped = client.result("stop")
                assertFalse(stopped.getBoolean("running"))
                assertFalse("Stop must release the lock", stopped.getBoolean("wakeLockHeld"))
                File(context.filesDir, "validation").apply { mkdirs() }.resolve("background-runtime.json")
                    .writeText(JSONObject().put("ok", true).put("foregroundService", true).put("screenOffSeconds", 10)
                        .put("healthChecks", 5).put("wakeLockReleasedOnDisableAndStop", true)
                        .put("realGoogleGeneration", false).put("forcedDoze", false).put("physicalDevice", false).toString(2))
            } finally {
                shell("input keyevent KEYCODE_WAKEUP")
                openApp()
                client.result("stop")
                client.result("configure", config)
                if (original.optBoolean("running")) client.result("start")
            }
        }
    }

    private fun openApp() = context.startActivity(Intent(context, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    private fun shell(command: String) {
        ParcelFileDescriptor.AutoCloseInputStream(instrumentation.uiAutomation.executeShellCommand(command)).use { it.readBytes() }
    }
    private fun health(port: Int): Int = Socket("127.0.0.1", port).use { socket ->
        socket.soTimeout = 5000
        socket.getOutputStream().write("GET /health HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n".toByteArray())
        socket.getInputStream().bufferedReader().use { it.readLine().split(" ")[1].toInt() }
    }
}
