package dev.pgproxy.app

import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.os.Process
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File
import java.io.RandomAccessFile
import java.net.ServerSocket
import java.net.Socket
import java.util.UUID

@RunWith(AndroidJUnit4::class)
class RuntimeInstrumentedTest {
    private val context: Context get() = InstrumentationRegistry.getInstrumentation().targetContext

    @Test fun packagedWorkerRunsUnderApplicationUidAndIsReaped() {
        val bridge = NativeBridge(context)
        assertEquals("Test must run with target application UID", context.applicationInfo.uid, Process.myUid())
        assertTrue("Application must not run as root or shell", Process.myUid() > 9999)
        try {
            repeat(2) {
                val id = UUID.randomUUID().toString()
                bridge.reserveWorker(id)
                assertEquals(id, bridge.startWorker(id))
                // Observe the actual OS child as well as the bridge's bookkeeping.
                val workersField = bridge.javaClass.getDeclaredField("workers").apply { isAccessible = true }
                val worker = checkNotNull((workersField.get(bridge) as Map<*, *>)[id])
                val processField = worker.javaClass.getDeclaredField("process").apply { isAccessible = true }
                val nativeProcess = processField.get(worker) as java.lang.Process
                assertTrue(nativeProcess.isAlive)
                bridge.workerWrite(id, "{\"id\":1,\"op\":\"ping\"}")
                val result = JSONObject(bridge.workerRead(id, 15_000)).getJSONObject("result")
                assertEquals("android", result.getString("goos"))
                assertEquals("goja-firefox", result.getString("runtime"))
                assertTrue(result.getString("goarch") in listOf("amd64", "arm64"))
                // A worker must not fabricate a proof before initialization.
                bridge.workerWrite(id, "{\"id\":2,\"op\":\"proof\",\"digest\":\"test\"}")
                val uninitialized = JSONObject(bridge.workerRead(id, 15_000))
                assertTrue(uninitialized.has("error"))
                assertFalse(uninitialized.has("result"))
                bridge.workerStop(id)
                assertEquals(0, bridge.workerCount())
                assertFalse("Native child must actually exit", nativeProcess.isAlive)
                nativeProcess.exitValue() // Throws if the OS process has not terminated.
                var rejected = false
                try { bridge.workerWrite(id, "{\"id\":3,\"op\":\"ping\"}") }
                catch (_: IllegalStateException) { rejected = true }
                assertTrue("Stopped worker must reject further commands", rejected)
            }
        } finally { bridge.stopAll() }
        assertEquals(0, bridge.workerCount())
        report("native-runtime.json", JSONObject().put("ok", true).put("applicationUid", Process.myUid())
            .put("normalApkUid", true).put("workerRestart", true).put("workerCleanup", true)
            .put("uninitializedProofRejected", true).put("realWaaAcceptance", false))
    }

    @Test fun cancelledAndDuplicateReservationsCannotCreateAnOrphan() {
        val bridge = NativeBridge(context)
        val cancelled = UUID.randomUUID().toString()
        val active = UUID.randomUUID().toString()
        try {
            bridge.reserveWorker(cancelled)
            assertTrue(runCatching { bridge.reserveWorker(cancelled) }.isFailure)
            bridge.workerStop(cancelled)
            assertTrue("A late dispatch must not resurrect a cancelled reservation", runCatching { bridge.startWorker(cancelled) }.isFailure)
            assertEquals(0, bridge.workerCount())
            bridge.reserveWorker(active)
            bridge.startWorker(active)
            assertTrue("An ID can launch only one process", runCatching { bridge.startWorker(active) }.isFailure)
            assertEquals(1, bridge.workerCount())
            bridge.workerWrite(active, "{\"id\":1,\"op\":\"ping\"}")
            assertTrue(JSONObject(bridge.workerRead(active, 15_000)).has("result"))
            val pending = UUID.randomUUID().toString()
            bridge.reserveWorker(pending)
            bridge.stopAll()
            assertTrue(runCatching { bridge.startWorker(pending) }.isFailure)
            assertEquals(0, bridge.workerCount())
        } finally { bridge.stopAll() }
    }

    @Test fun committedCredentialBackupRecoversAndOversizedRecordsAreRejected() {
        val bridge = NativeBridge(context)
        val id = UUID.randomUUID().toString().replace("-", "")
        val file = File(context.noBackupFilesDir, "vault/account-$id.enc")
        val backup = File(file.path + ".bak")
        val record = "{\"synthetic\":\"atomic-recovery\"}"
        try {
            bridge.writeAccount(id, record)
            assertTrue(file.renameTo(backup))
            assertFalse(file.exists())
            assertTrue("AtomicFile backup must not be mistaken for an absent credential", bridge.readAccount(id) == record)
            assertTrue(file.exists())
            assertFalse(backup.exists())
            RandomAccessFile(file, "rw").use { it.setLength(8L * 1024 * 1024) }
            var rejected = false
            try { bridge.readAccount(id) } catch (_: IllegalArgumentException) { rejected = true }
            assertTrue("Oversized ciphertext must be rejected before decryption", rejected)
        } finally { bridge.deleteAccount(id); bridge.stopAll() }
    }

    @Test fun interruptedStopStillReapsTheNativeProcess() {
        val bridge = NativeBridge(context)
        val id = UUID.randomUUID().toString()
        try {
            bridge.reserveWorker(id)
            bridge.startWorker(id)
            val workersField = bridge.javaClass.getDeclaredField("workers").apply { isAccessible = true }
            val worker = checkNotNull((workersField.get(bridge) as Map<*, *>)[id])
            val processField = worker.javaClass.getDeclaredField("process").apply { isAccessible = true }
            val process = processField.get(worker) as java.lang.Process
            val restoredInterrupt = java.util.concurrent.atomic.AtomicBoolean(false)
            val stopped = Thread {
                Thread.currentThread().interrupt()
                bridge.workerStop(id)
                restoredInterrupt.set(Thread.currentThread().isInterrupted)
            }
            stopped.start()
            stopped.join(10_000)
            assertFalse("Cleanup must finish despite an interrupted waiter", stopped.isAlive)
            assertTrue("Cleanup must preserve the caller's interruption", restoredInterrupt.get())
            assertFalse(process.isAlive)
            assertEquals(0, bridge.workerCount())
        } finally { bridge.stopAll() }
    }

    @Test fun credentialsAreEncryptedAuthenticatedAndDeleted() {
        val bridge = NativeBridge(context)
        val id = UUID.randomUUID().toString().replace("-", "")
        val marker = "pg-test-secret-" + UUID.randomUUID()
        val record = JSONObject().put("cookies", JSONArray().put(JSONObject().put("name", "TEST_ONLY").put("value", marker))).toString()
        val file = File(context.noBackupFilesDir, "vault/account-$id.enc")
        try {
            bridge.writeAccount(id, record)
            assertTrue("Encrypted record must round trip", record == bridge.readAccount(id))
            val first = file.readBytes()
            assertFalse("Encrypted file must not contain plaintext", first.toString(Charsets.ISO_8859_1).contains(marker))
            bridge.writeAccount(id, record)
            val second = file.readBytes()
            assertFalse("Each save must use a fresh GCM nonce", first.contentEquals(second))
            second[second.lastIndex] = (second.last().toInt() xor 1).toByte()
            file.writeBytes(second)
            var tamperRejected = false
            try { bridge.readAccount(id) } catch (_: Exception) { tamperRejected = true }
            assertTrue("Corrupted ciphertext must be rejected", tamperRejected)
        } finally { bridge.deleteAccount(id) }
        assertFalse(file.exists())
        assertEquals("{}", bridge.readAccount(id))
        report("encrypted-storage.json", JSONObject().put("ok", true).put("plaintextExcluded", true)
            .put("nonceChanges", true).put("tamperRejected", true).put("deleted", true))
    }

    @Test fun privateControlSupportsCrudAndAuthenticatedHttpLifecycle() {
        context.startActivity(Intent(context, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        val info = context.packageManager.getServiceInfo(ComponentName(context, ProxyService::class.java), 0)
        assertFalse("Control service must not be exported", info.exported)
        PrivateServiceClient(context).use { client ->
            val original = client.result("snapshot")
            val originalConfig = configOf(original)
            val originalRunning = original.optBoolean("running")
            val id = UUID.randomUUID().toString().replace("-", "")
            val port = ServerSocket(0).use { it.localPort }
            val key = "instrumentation-" + UUID.randomUUID()
            try {
                val diagnostics = client.result("diagnostics")
                assertEquals("android", diagnostics.getJSONObject("runtime").getString("goos"))
                assertTrue(diagnostics.getJSONObject("dependencies").has("fastapi"))
                assertTrue(diagnostics.getJSONObject("dependencies").has("pydantic"))
                client.result("stop")
                client.result("configure", JSONObject().put("port", port).put("lan", false).put("apiKey", key))
                val invalid = client.call("configure", JSONObject().put("port", 1))
                assertFalse(invalid.optBoolean("ok"))
                assertEquals(port, client.result("snapshot").getInt("port"))
                client.result("import_account", JSONObject().put("id", id).put("label", "Instrumentation test only")
                    .put("session", syntheticSession()))
                client.result("set_enabled", JSONObject().put("id", id).put("enabled", false))
                val stored = client.result("snapshot").getJSONArray("accounts")
                assertTrue((0 until stored.length()).map { stored.getJSONObject(it) }.any { it.getString("id") == id && !it.getBoolean("enabled") })
                client.result("delete_account", JSONObject().put("id", id))
                val afterDelete = client.result("snapshot").getJSONArray("accounts")
                assertFalse((0 until afterDelete.length()).any { afterDelete.getJSONObject(it).getString("id") == id })
                assertTrue(client.result("start").getBoolean("running"))
                assertEquals(200, request(port, "GET", "/health").first)
                assertEquals(401, request(port, "POST", "/v1/chat/completions", body = "{}").first)
                val variants = listOf(
                    Pair("/v1/chat/completions", mapOf("Authorization" to "Bearer $key")),
                    Pair("/v1/chat/completions", mapOf("Authorization" to "Basic " + android.util.Base64.encodeToString("test:$key".toByteArray(), android.util.Base64.NO_WRAP))),
                    Pair("/v1/chat/completions", mapOf("x-goog-api-key" to key)),
                    Pair("/v1/chat/completions?key=$key", emptyMap())
                )
                for ((path, headers) in variants) {
                    // A valid local credential reaches payload validation, with no Google request.
                    assertEquals(400, request(port, "POST", path, headers, "{}").first)
                }
                assertEquals(404, request(port, "POST", "/accounts", mapOf("Authorization" to "Bearer $key"), "{}").first)
                assertFalse(client.result("stop").getBoolean("running"))
                var portClosed = false
                repeat(30) {
                    if (!portClosed) {
                        portClosed = try { Socket("127.0.0.1", port).use { }; false } catch (_: Exception) { true }
                        if (!portClosed) Thread.sleep(100)
                    }
                }
                assertTrue("Stopping service must close its listening socket", portClosed)
                report("private-service.json", JSONObject().put("ok", true).put("nativeDiagnostics", diagnostics)
                    .put("privateBinder", true).put("accountCrud", true).put("fourAuthMethods", true)
                    .put("httpLifecycle", true).put("invalidPortRejected", true).put("managementHttpAbsent", true)
                    .put("realGeneration", false))
            } finally {
                client.call("delete_account", JSONObject().put("id", id))
                client.call("stop")
                client.result("configure", originalConfig)
                if (originalRunning) client.result("start")
            }
        }
    }

    private fun syntheticSession(): JSONObject {
        val cookies = JSONArray()
        for (name in listOf("SAPISID", "__Secure-1PAPISID", "__Secure-3PAPISID", "__Secure-1PSID")) {
            cookies.put(JSONObject().put("name", name).put("value", "test-only-no-upstream")
                .put("domain", ".google.com").put("path", "/").put("expires", -1).put("secure", true).put("httpOnly", true))
        }
        return JSONObject().put("cookies", cookies).put("origins", JSONArray())
    }

    private fun configOf(snapshot: JSONObject): JSONObject = JSONObject().apply {
        for (name in listOf("port", "lan", "apiKey", "timeout", "queueTimeout")) put(name, snapshot.get(name))
    }

    private fun request(port: Int, method: String, path: String, headers: Map<String, String> = emptyMap(), body: String = ""): Pair<Int, String> {
        // Raw loopback HTTP verifies the server independently of browser rendering.
        Socket("127.0.0.1", port).use { socket ->
            socket.soTimeout = 15_000
            val bytes = body.toByteArray(Charsets.UTF_8)
            val head = buildString {
                append("$method $path HTTP/1.1\r\nHost: 127.0.0.1:$port\r\nConnection: close\r\n")
                append("Content-Type: application/json\r\nContent-Length: ${bytes.size}\r\n")
                headers.forEach { (name, value) -> append("$name: $value\r\n") }
                append("\r\n")
            }
            socket.getOutputStream().apply { write(head.toByteArray(Charsets.US_ASCII)); write(bytes); flush() }
            val response = socket.getInputStream().bufferedReader().readText()
            return Pair(response.lineSequence().first().split(" ")[1].toInt(), response.substringAfter("\r\n\r\n"))
        }
    }

    private fun report(name: String, body: JSONObject) {
        File(context.filesDir, "validation").apply { mkdirs() }.resolve(name).writeText(body.toString(2))
    }
}
