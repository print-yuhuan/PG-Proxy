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

/** Opt-in verification of a session already captured by the app's Gecko login UI. */
@RunWith(AndroidJUnit4::class)
class BrowserImportedAccountInstrumentedTest {
    @Test fun browserImportedAccountGeneratesAndRefreshesWaa() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val args = InstrumentationRegistry.getArguments()
        val context = instrumentation.targetContext
        val label = args.getString("browserAccountLabel", "")
        assumeTrue("No explicitly selected browser-imported account; live test skipped", label.isNotEmpty())
        val exportName = args.getString("exportConnection", "")
        require(exportName.isEmpty() || exportName.matches(Regex("[A-Za-z0-9_.-]+"))) {
            "Use a private connection-file basename"
        }
        val exportFile = exportName.takeIf { it.isNotEmpty() }?.let { File(context.filesDir, it) }
        if (exportFile?.exists() == true) check(exportFile.delete()) { "Could not remove stale private connection file" }
        val keepServer = args.getString("keepServerRunning", "false") == "true"
        val reportFile = File(context.filesDir, "validation/browser-imported-account.json")
        reportFile.parentFile!!.mkdirs()
        val report = JSONObject().put("ok", false).put("standaloneApk", true).put("termuxUsed", false)
            .put("normalApkUid", android.os.Process.myUid() == context.applicationInfo.uid)
            .put("scope", "Real generation after UI credential import; login interaction is manually confirmed")
            .put("browserLoginInteractionVerifiedByThisTest", false)
            .put("sessionReimportedByThisTest", false).put("accountDeletedByThisTest", false)
        var step = "bind"
        context.startActivity(Intent(context, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        try {
            PrivateServiceClient(context).use { client ->
                val original = client.result("snapshot")
                val accounts = original.getJSONArray("accounts")
                val records = (0 until accounts.length()).map { accounts.getJSONObject(it) }
                step = "select_unique_browser_imported_account"
                val selected = records.filter { it.getString("label") == label }
                assertEquals("The explicit account label must match exactly one existing account", 1, selected.size)
                val accountId = selected.single().getString("id")
                assertTrue("The selected account lacks a completed Gecko UI import marker", GeckoProfiles.isImported(context, accountId))
                report.put("geckoImportMarkerPresent", true)
                val originalEnabled = records.associate { it.getString("id") to it.getBoolean("enabled") }
                var primaryError: Throwable? = null
                try {
                    step = "temporarily_isolate_selected_account"
                    originalEnabled.forEach { (id, enabled) ->
                        val wanted = id == accountId
                        if (enabled != wanted) client.result("set_enabled", JSONObject().put("id", id).put("enabled", wanted))
                    }
                    step = "real_generation_from_browser_import"
                    val checked = client.result("check_account", JSONObject().put("id", accountId).apply {
                        args.getString("liveModel")?.let { put("model", it) }
                    }, 360).getJSONObject("check")
                    assertTrue("Real generation did not verify the browser-imported account", checked.getBoolean("verified"))
                    val model = checked.getString("model")
                    report.put("check", safeCheck(checked)).put("realGeneration", true).put("waaServerAcceptance", true)
                    step = "forced_waa_refresh_and_real_generation"
                    val refreshed = client.result("check_account", JSONObject().put("id", accountId)
                        .put("model", model).put("forceRefresh", true), 360).getJSONObject("check")
                    assertTrue("Real generation after forced WAA refresh did not verify", refreshed.getBoolean("verified"))
                    assertTrue("The server did not supply a changed WAA program after forced refresh", refreshed.optBoolean("waaRefreshed"))
                    report.put("refreshCheck", safeCheck(refreshed)).put("forcedRefreshGeneration", true)
                    step = "http_start_and_private_handoff"
                    val started = client.result("start")
                    assertTrue("Local API server did not start", started.getBoolean("running"))
                    exportFile?.writeText(JSONObject().put("port", started.getInt("port"))
                        .put("apiKey", started.getString("apiKey")).put("model", model)
                        .put("accountId", accountId).toString())
                    report.put("serverRetainedForFurtherTests", keepServer || original.optBoolean("running"))
                } catch (error: Throwable) {
                    primaryError = error
                    throw error
                } finally {
                    // Restore only enable flags; preserve all accounts, sessions and verification results.
                    var cleanupError: Throwable? = null
                    originalEnabled.forEach { (id, enabled) ->
                        try {
                            client.result("set_enabled", JSONObject().put("id", id).put("enabled", enabled))
                        } catch (error: Throwable) {
                            if (cleanupError == null) cleanupError = error else cleanupError!!.addSuppressed(error)
                        }
                    }
                    try {
                        if (!keepServer && !original.optBoolean("running")) client.result("stop")
                    } catch (error: Throwable) {
                        if (cleanupError == null) cleanupError = error else cleanupError!!.addSuppressed(error)
                    }
                    report.put("originalAccountEnableFlagsRestored", cleanupError == null)
                    cleanupError?.let { error ->
                        primaryError?.let { it.addSuppressed(error) } ?: run {
                            step = "restore_original_account_state"
                            throw error
                        }
                    }
                }
                report.put("ok", true)
            }
        } catch (error: Throwable) {
            exportFile?.delete()
            report.put("ok", false).put("failedStep", step).put("errorClass", error.javaClass.simpleName)
            throw error
        } finally {
            reportFile.writeText(report.toString(2))
        }
    }

    private fun safeCheck(check: JSONObject) = JSONObject().put("verified", check.getBoolean("verified"))
        .put("model", check.getString("model")).put("checkedAt", check.opt("checkedAt"))
        .apply { if (check.has("waaRefreshed")) put("waaRefreshed", check.getBoolean("waaRefreshed")) }
}
