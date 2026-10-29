package dev.pgproxy.app

import android.content.Intent
import android.os.SystemClock
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File
import java.util.UUID

/** Replays only a synthetic account's UI removal intent through the real private service. */
@RunWith(AndroidJUnit4::class)
class DeletionRecoveryInstrumentedTest {
    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val context = instrumentation.targetContext

    @Test
    fun mainActivityRecoversRemovalWhenTheOriginalDeleteIpcWasNeverSent() {
        val id = UUID.randomUUID().toString().replace("-", "")
        val bridge = NativeBridge(context)
        PrivateServiceClient(context).use { client ->
            try {
                client.result("import_account", JSONObject().put("id", id)
                    .put("label", "Synthetic deletion recovery test")
                    .put("session", syntheticSession()))
                client.result("set_enabled", JSONObject().put("id", id).put("enabled", false))
                assertTrue("Synthetic account must exist before the simulated interruption", contains(client.result("snapshot"), id))
                assertNotEquals("{}", bridge.readAccount(id))

                // This is the interruption point: durable UI journals are written, but this
                // test intentionally does not send delete_account to the service.
                var geckoMarked = false
                instrumentation.runOnMainSync {
                    geckoMarked = GeckoProfiles.markForDeletion(context, id)
                }
                assertTrue(geckoMarked)
                assertTrue(GeckoProfiles.deletedAccountIds(context).contains(id))

                context.startActivity(Intent(context, MainActivity::class.java).addFlags(
                    Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP))
                val deadline = SystemClock.elapsedRealtime() + 45_000
                var removed = false
                while (SystemClock.elapsedRealtime() < deadline) {
                    if (!contains(client.result("snapshot", timeoutSeconds = 30), id)) {
                        removed = true
                        break
                    }
                    SystemClock.sleep(300)
                }
                assertTrue("MainActivity must replay the queued removal through private IPC", removed)
                assertEquals("Recovered deletion must remove the encrypted credential record", "{}", bridge.readAccount(id))
                val report = JSONObject().put("ok", true).put("syntheticOnly", true)
                    .put("originalDeleteIpcOmitted", true).put("mainActivityReplay", true)
                    .put("encryptedRecordDeleted", true).put("realGoogleAccountUsed", false)
                File(context.filesDir, "validation").apply { mkdirs() }
                    .resolve("deletion-recovery.json").writeText(report.toString(2))
            } finally {
                // Cleanup addresses only this random synthetic ID; existing accounts and
                // service configuration/running state are never modified by this test.
                client.call("delete_account", JSONObject().put("id", id))
                bridge.deleteAccount(id)
                GeckoProfiles.delete(context, id)
            }
        }
    }

    private fun contains(snapshot: JSONObject, id: String): Boolean {
        val accounts = snapshot.getJSONArray("accounts")
        return (0 until accounts.length()).any { accounts.getJSONObject(it).getString("id") == id }
    }

    private fun syntheticSession(): JSONObject {
        val cookies = JSONArray()
        for (name in listOf("SAPISID", "__Secure-1PAPISID", "__Secure-3PAPISID", "__Secure-1PSID")) {
            cookies.put(JSONObject().put("name", name).put("value", "synthetic-no-google-request")
                .put("domain", ".google.com").put("path", "/").put("expires", -1)
                .put("secure", true).put("httpOnly", true))
        }
        return JSONObject().put("cookies", cookies).put("origins", JSONArray())
    }
}
