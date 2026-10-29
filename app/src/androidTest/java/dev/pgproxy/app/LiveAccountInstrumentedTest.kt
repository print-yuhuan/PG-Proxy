package dev.pgproxy.app

import android.content.Intent
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Assume.assumeTrue
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File
import java.util.UUID

/** Explicitly opt-in: a parent must stage a real session in target filesDir. */
@RunWith(AndroidJUnit4::class)
class LiveAccountInstrumentedTest {
    @Test fun realAccountImportAndGenerationInsideStandaloneApk() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val args = InstrumentationRegistry.getArguments()
        val context = instrumentation.targetContext
        val sourceName = args.getString("liveSession", "")
        assumeTrue("No live Google session supplied; real generation is not verified", sourceName.isNotEmpty())
        require(sourceName.matches(Regex("[A-Za-z0-9_.-]+"))) { "Use a private filesDir basename" }
        val source = File(context.filesDir, sourceName)
        check(source.isFile && source.length() in 1..1_048_576) { "Private live-session file is missing or too large" }
        val reportDir = File(context.filesDir, "validation").apply { mkdirs() }
        val report = JSONObject().put("ok", false).put("standaloneApk", true).put("termuxUsed", false)
            .put("normalApkUid", android.os.Process.myUid() == context.applicationInfo.uid)
            .put("browserLoginVerifiedByThisTest", false).put("multiAccountVerifiedByThisTest", false)
        val accountId = UUID.randomUUID().toString().replace("-", "")
        val keepAccount = args.getString("keepLiveAccount", "false") == "true"
        val keepServer = args.getString("keepServerRunning", "false") == "true"
        var step = "bind"
        context.startActivity(Intent(context, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        try {
            PrivateServiceClient(context).use { client ->
                val original = client.result("snapshot")
                var imported = false
                try {
                    step = "native_diagnostics"
                    report.put("diagnostics", client.result("diagnostics"))
                    step = "private_session_import"
                    val payload = JSONObject().put("id", accountId).put("label", "Live APK validation")
                        .put("session", JSONObject(source.readText()))
                    client.result("import_account", payload)
                    imported = true
                    // Remove the plaintext staging file immediately after the encrypted import.
                    check(source.delete()) { "Could not remove private staging file" }
                    step = "real_waa_and_model_generation"
                    val check = client.result("check_account", JSONObject().put("id", accountId).apply {
                        args.getString("liveModel")?.let { put("model", it) }
                    }, 360).getJSONObject("check")
                    assertTrue("Real generation did not verify the account", check.getBoolean("verified"))
                    report.put("check", check).put("realGeneration", true).put("waaServerAcceptance", true)
                    step = "real_waa_refresh_and_generation"
                    val refreshed = client.result("check_account", JSONObject().put("id", accountId)
                        .put("model", check.getString("model")).put("forceRefresh", true), 360).getJSONObject("check")
                    assertTrue("Generation following WAA refresh did not verify", refreshed.getBoolean("verified"))
                    report.put("refreshCheck", refreshed).put("forcedRefreshGeneration", true)
                    step = "http_start"
                    val started = client.result("start")
                    assertTrue(started.getBoolean("running"))
                    val exportName = args.getString("exportConnection", "")
                    if (exportName.isNotEmpty()) {
                        require(exportName.matches(Regex("[A-Za-z0-9_.-]+"))) { "Use a private connection-file basename" }
                        // Private handoff only. Never print this object or copy it into a public report.
                        File(context.filesDir, exportName).writeText(JSONObject()
                            .put("port", started.getInt("port")).put("apiKey", started.getString("apiKey"))
                            .put("model", check.getString("model")).put("accountId", accountId).toString())
                    }
                    report.put("ok", true).put("accountRetainedForFurtherTests", keepAccount)
                        .put("serverRetainedForFurtherTests", keepServer)
                } finally {
                    if (!keepAccount && imported) client.result("delete_account", JSONObject().put("id", accountId))
                    if (!keepServer && !original.optBoolean("running")) client.result("stop")
                }
            }
        } catch (error: Throwable) {
            report.put("ok", false).put("failedStep", step).put("errorClass", error.javaClass.simpleName)
            throw error
        } finally {
            source.delete()
            reportDir.resolve("live-account.json").writeText(report.toString(2))
        }
    }
}
