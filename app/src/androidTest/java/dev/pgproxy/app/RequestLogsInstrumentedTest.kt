package dev.pgproxy.app

import android.content.Intent
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.util.UUID

/** Actual APK listener + private Binder, followed by isolated packaged-Python faults.
 * No Google requests, existing credential changes, or production test hooks.
 */
@RunWith(AndroidJUnit4::class)
class RequestLogsInstrumentedTest {
    @Test fun installedListenerRecordsFailuresAndDetailsRemainPrivate() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val context = instrumentation.targetContext
        context.startActivity(Intent(context, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        instrumentation.waitForIdleSync()
        PrivateServiceClient(context).use { client ->
            val original = client.result("snapshot")
            val saved = configOf(original)
            val accounts = original.getJSONArray("accounts").toString()
            val wasRunning = original.getBoolean("running")
            val port = original.getInt("port")
            val key = original.getString("apiKey")
            val rejectedKey = "invalid-logs-${UUID.randomUUID()}"
            val requestText = "private-message-${UUID.randomUUID()}"
            var primary: Throwable? = null
            try {
                client.result("start")
                assertEquals(401, request(port, "/v1/models?key=$rejectedKey", "GET").first)
                assertEquals(404, request(port, "/v1/logging-no-such-route", "GET", key).first)
                assertEquals(405, request(port, "/v1/models", "DELETE", key).first)
                assertEquals(400, request(port, "/v1/chat/completions", "POST", key,
                    "[\"$requestText\"]").first)
                assertEquals(200, request(port, "/health", "GET").first)
                assertEquals("Log details must never become a public route", 404,
                    request(port, "/log_detail", "GET", key).first)

                val snapshot = client.result("snapshot")
                val entries = snapshot.getJSONArray("logs")
                val calls = (0 until entries.length()).map { entries.getJSONObject(it) }
                    .filter { it.optString("event") == "api_request" }
                fun detail(path: String, method: String): JSONObject {
                    val entry = calls.last { it.optString("path") == path && it.optString("method") == method }
                    assertTrue(entry.getDouble("durationMs") >= 0)
                    return client.result("log_detail", JSONObject().put("id", entry.getString("id")))
                }
                val auth = detail("/v1/models", "GET")
                assertEquals(401, auth.getInt("status"))
                assertEquals("failure", auth.getString("outcome"))
                assertEquals("authentication", auth.getJSONObject("error").getString("type"))
                assertTrue(auth.getBoolean("credentialIssue"))
                assertFalse(auth.toString().contains(rejectedKey))
                assertFalse(auth.toString().contains("?key=$rejectedKey"))
                val badPath = detail("/v1/logging-no-such-route", "GET")
                assertEquals(404, badPath.getInt("status"))
                assertEquals("not_found", badPath.getJSONObject("error").getString("type"))
                assertFalse(badPath.getBoolean("credentialIssue"))
                val parameters = detail("/v1/chat/completions", "POST")
                assertEquals("parameters", parameters.getJSONObject("error").getString("type"))
                assertFalse(parameters.toString().contains(requestText))
                assertFalse(parameters.toString().contains("Bearer $key"))
                assertEquals("success", detail("/health", "GET").getString("outcome"))
                assertFalse(entries.toString().contains(requestText))
                assertFalse(entries.toString().contains(rejectedKey))
                assertEquals("log_not_found", client.call("log_detail", JSONObject().put("id", "not-present"))
                    .getJSONObject("error").getString("code"))
                assertTrue("Saved settings changed", configOf(snapshot) == saved)
                assertTrue("Credentials changed", accounts == snapshot.getJSONArray("accounts").toString())
            } catch (error: Throwable) {
                primary = error
                throw error
            } finally {
                try {
                    if (client.result("snapshot").getBoolean("running") != wasRunning) {
                        client.result(if (wasRunning) "start" else "stop")
                    }
                    assertTrue("Configuration not preserved", configOf(client.result("snapshot")) == saved)
                } catch (cleanup: Throwable) {
                    if (primary != null) primary.addSuppressed(cleanup) else throw cleanup
                }
            }
        }
    }

    @Test fun packagedPythonHandlesCredentialNetworkAndStreamFailures() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val context = instrumentation.targetContext
        if (!Python.isStarted()) Python.start(AndroidPlatform(context))
        val python = Python.getInstance()
        val source = instrumentation.context.assets.open("request_logging_scenarios.py")
            .bufferedReader().use { it.readText() }
        val globals = python.getModule("builtins").callAttr("dict")
        python.getModule("builtins").callAttr("exec", source, globals)
        val result = JSONObject(checkNotNull(globals.callAttr("get", "run_scenarios")).call().toString())
        assertTrue(result.getBoolean("ok"))
        assertEquals(7, result.getInt("scenarios"))
        assertFalse(result.getBoolean("realGoogleGeneration"))
        File(context.filesDir, "validation").apply { mkdirs() }
            .resolve("request-logging.json").writeText(result.toString(2))
    }

    private fun configOf(value: JSONObject) = listOf("port", "lan", "apiKey", "timeout", "queueTimeout", "keepAwake")
        .associateWith { value.get(it) }

    private fun request(port: Int, path: String, method: String, key: String? = null,
                        body: String? = null): Pair<Int, String> {
        val connection = URL("http://127.0.0.1:$port$path").openConnection() as HttpURLConnection
        try {
            connection.connectTimeout = 5000
            connection.readTimeout = 5000
            connection.requestMethod = method
            if (key != null) connection.setRequestProperty("Authorization", "Bearer $key")
            if (body != null) {
                connection.setRequestProperty("Content-Type", "application/json")
                connection.doOutput = true
                connection.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }
            }
            val status = connection.responseCode
            val content = (if (status >= 400) connection.errorStream else connection.inputStream)
                ?.bufferedReader()?.use { it.readText() }.orEmpty()
            return status to content
        } finally { connection.disconnect() }
    }
}
