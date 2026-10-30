package dev.pgproxy.app

import android.content.Context
import android.content.Intent
import android.os.SystemClock
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File
import java.net.InetSocketAddress
import java.net.Socket
import java.util.UUID

/** APK integration of the request guard. Never requests a Google model or changes saved settings. */
@RunWith(AndroidJUnit4::class)
class ReviewRegressionInstrumentedTest {
    @Test fun installedApkRejectsBodiesBeforeReadingAndPreservesExistingAccounts() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val context = instrumentation.targetContext
        val args = InstrumentationRegistry.getArguments()
        val report = JSONObject().put("ok", false).put("realGoogleGeneration", false)
            .put("configurationModifiedByTest", false).put("accountImportedOrDeletedByTest", false)
        val reportDir = File(context.filesDir, "validation").apply { check(isDirectory || mkdirs()) }
        val exportName = args.getString("exportConnection", "").orEmpty()
        require(exportName.isEmpty() || (exportName.matches(Regex("[A-Za-z0-9][A-Za-z0-9_.-]{0,79}")) &&
            exportName !in setOf(".", ".."))) { "Use a private connection-file basename" }
        val exportFile = exportName.takeIf { it.isNotEmpty() }?.let { name ->
            File(context.filesDir, name).also { file ->
                check(file.canonicalFile.parentFile == context.filesDir.canonicalFile && !file.exists()) {
                    "Private connection export already exists or has an invalid path"
                }
            }
        }
        var exportCreated = false
        var step = "installed_version"
        try {
            @Suppress("DEPRECATION")
            val installed = context.packageManager.getPackageInfo(context.packageName, 0)
            val expectedVersion = args.getString("expectedVersion", BuildConfig.VERSION_NAME)
            val expectedCode = args.getString("expectedVersionCode", BuildConfig.VERSION_CODE.toString())!!.toLong()
            assertEquals("Installed application version mismatch", expectedVersion, installed.versionName)
            assertEquals("Installed application version code mismatch", expectedCode, installed.longVersionCode)
            assertEquals(BuildConfig.VERSION_NAME, installed.versionName)
            assertEquals(BuildConfig.VERSION_CODE.toLong(), installed.longVersionCode)
            report.put("version", installed.versionName).put("versionCode", installed.longVersionCode)

            // A visible application permits the user-facing foreground-service start. The
            // normal overview may query public IP metadata, but this test never calls Google.
            context.startActivity(Intent(context, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            instrumentation.waitForIdleSync()
            step = "private_snapshot"
            PrivateServiceClient(context).use { client ->
                val original = client.result("snapshot")
                val savedConfig = configOf(original)
                val savedAccounts = accountFlags(original)
                val wasRunning = original.getBoolean("running")
                val port = original.getInt("port")
                val key = original.getString("apiKey")
                var primaryError: Throwable? = null
                try {
                    step = "start_current_listener"
                    assertTrue("Listener did not start", client.result("start").getBoolean("running"))
                    val oversized = 64L * 1024 * 1024 + 1
                    var wrongKey = "review-invalid-${UUID.randomUUID()}"
                    if (wrongKey == key) wrongKey += "-different"

                    step = "header_only_unauthorized"
                    val unauthorized = request(port, "/v1/chat/completions", "POST", wrongKey,
                        declaredLength = oversized)
                    assertEquals("Wrong credentials must be rejected without waiting for a body", 401, unauthorized.first)
                    report.put("headerOnlyUnauthorized", true).put("headerOnlyUnauthorizedMillis", unauthorized.second)

                    step = "header_only_oversized"
                    val rejectedSize = request(port, "/v1/chat/completions", "POST", key,
                        declaredLength = oversized)
                    assertEquals("Oversized Content-Length must be rejected without waiting for a body", 413, rejectedSize.first)
                    report.put("headerOnlyOversized", true).put("headerOnlyOversizedMillis", rejectedSize.second)

                    step = "invalid_json"
                    assertEquals("Malformed JSON must remain a local 400", 400,
                        request(port, "/v1/chat/completions", "POST", key, body = "{".toByteArray()).first)
                    report.put("invalidJsonRejected", true)
                    step = "health_after_errors"
                    assertEquals(200, request(port, "/health", "GET").first)
                    report.put("healthAfterErrors", true)

                    step = "preserved_state"
                    val after = client.result("snapshot")
                    // Never use assertEquals with configs: a failure must not print the API key.
                    assertTrue("Saved configuration changed unexpectedly", configOf(after) == savedConfig)
                    assertTrue("Existing account IDs or enable flags changed unexpectedly", accountFlags(after) == savedAccounts)
                    report.put("configurationPreserved", true).put("accountsPreserved", true)
                        .put("accountCount", savedAccounts.size)
                } catch (error: Throwable) {
                    primaryError = error
                    throw error
                } finally {
                    try {
                        val current = client.result("snapshot")
                        if (current.getBoolean("running") != wasRunning) client.result(if (wasRunning) "start" else "stop")
                        val restored = client.result("snapshot")
                        assertTrue("Original running state was not restored", restored.getBoolean("running") == wasRunning)
                        assertTrue("Saved configuration changed during cleanup", configOf(restored) == savedConfig)
                        assertTrue("Existing accounts changed during cleanup", accountFlags(restored) == savedAccounts)
                        report.put("originalRunningRestored", true).put("originalRunning", wasRunning)
                    } catch (cleanup: Throwable) {
                        report.put("originalRunningRestored", false)
                        if (primaryError != null) primaryError!!.addSuppressed(cleanup)
                        else { step = "restore_original_state"; throw cleanup }
                    }
                }
                // Export only after all checks and restoration pass. The service still has
                // its original running state; exporting a connection does not start it.
                if (exportFile != null) {
                    step = "private_connection_export"
                    check(exportFile.createNewFile()) { "Could not reserve private connection export" }
                    exportCreated = true
                    context.openFileOutput(exportName, Context.MODE_PRIVATE).use { stream ->
                        stream.write(JSONObject().put("port", port).put("apiKey", key).toString().toByteArray(Charsets.UTF_8))
                    }
                    report.put("privateConnectionExported", true)
                }
                report.put("ok", true)
            }
        } catch (error: Throwable) {
            if (exportCreated) exportFile?.delete()
            report.put("ok", false).put("failedStep", step).put("errorClass", error.javaClass.simpleName)
            throw error
        } finally {
            reportDir.resolve("review-regression.json").writeText(report.toString(2))
        }
    }

    private fun configOf(snapshot: JSONObject): Map<String, Any> =
        listOf("port", "lan", "apiKey", "timeout", "queueTimeout", "keepAwake").associateWith { snapshot.get(it) }

    private fun accountFlags(snapshot: JSONObject): Map<String, Boolean> {
        val accounts = snapshot.getJSONArray("accounts")
        return (0 until accounts.length()).associate { index ->
            val account = accounts.getJSONObject(index)
            account.getString("id") to account.getBoolean("enabled")
        }
    }

    /** Header-only probes keep the write side open: a reader cannot rely on EOF to reject. */
    private fun request(port: Int, path: String, method: String, key: String? = null,
                        body: ByteArray = byteArrayOf(), declaredLength: Long = body.size.toLong()): Pair<Int, Long> {
        val started = SystemClock.elapsedRealtime()
        Socket().use { socket ->
            socket.connect(InetSocketAddress("127.0.0.1", port), 3000)
            socket.soTimeout = 5000
            val headers = buildString {
                append("$method $path HTTP/1.1\r\nHost: 127.0.0.1:$port\r\nConnection: close\r\n")
                append("Content-Type: application/json\r\nContent-Length: $declaredLength\r\n")
                if (key != null) append("Authorization: Bearer $key\r\n")
                append("\r\n")
            }
            socket.getOutputStream().apply { write(headers.toByteArray(Charsets.US_ASCII)); write(body); flush() }
            val statusLine = socket.getInputStream().bufferedReader(Charsets.US_ASCII).readLine().orEmpty()
            val code = Regex("HTTP/1\\.[01] ([0-9]{3})(?: .*)?").matchEntire(statusLine)?.groupValues?.get(1)?.toInt()
                ?: throw IllegalStateException("Invalid local HTTP status line")
            return code to (SystemClock.elapsedRealtime() - started)
        }
    }
}
