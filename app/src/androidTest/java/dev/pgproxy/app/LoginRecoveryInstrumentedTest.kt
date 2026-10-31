package dev.pgproxy.app

import android.app.AlertDialog
import android.app.Application
import android.app.Instrumentation
import android.content.Intent
import android.graphics.Bitmap
import android.os.Handler
import android.os.SystemClock
import android.widget.TextView
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Assume.assumeTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.mozilla.geckoview.GeckoSession
import org.mozilla.geckoview.GeckoView
import org.mozilla.geckoview.WebExtension
import org.mozilla.geckoview.WebRequestError
import java.io.File
import java.util.UUID

/**
 * Synthetic errors through the real activity's delegates, Handler and Android dialogs.
 * A recording session isolates assertions from Google/network timing. This is not a
 * reproduction of Google's reset, a real sign-in, or a successful credential import.
 */
@RunWith(AndroidJUnit4::class)
class LoginRecoveryInstrumentedTest {
    @Test
    fun realActivityRecoversOnceAndRetainsFailureAndImportGuards() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        assumeTrue("Use the explicit :login test runner", instrumentation is LoginRecoveryTestRunner)
        val context = instrumentation.targetContext
        assertEquals("dev.pgproxy.app:login", Application.getProcessName())
        val profileId = UUID.randomUUID().toString().replace("-", "")
        val directory = File(context.filesDir, "validation").apply { mkdirs() }
        val passed = JSONArray()
        val report = JSONObject().put("ok", false).put("testProfileId", profileId)
            .put("process", Application.getProcessName()).put("passedScenarios", passed)
            .put("scope", "Real activity callbacks, deferred navigation and dialogs with synthetic Gecko errors")
            .put("googleFailureReproduced", false).put("realGoogleLogin", false)
            .put("credentialImportVerified", false).put("recordingSessionOpened", false)
        val reportFile = File(directory, "login-recovery.json")
        // Persist the synthetic ID before launch so a startup crash is still cleanable.
        reportFile.writeText(report.toString(2))
        var harness: Harness? = null
        var step = "launch_real_activity_and_gecko"
        try {
            val activity = instrumentation.startActivitySync(Intent(context, GeckoLoginActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                .putExtra(GeckoLoginActivity.EXTRA_ACCOUNT_ID, profileId)
                .putExtra(GeckoLoginActivity.EXTRA_LABEL, "Synthetic login recovery test")) as GeckoLoginActivity
            harness = Harness(instrumentation, activity)
            val h = harness
            h.await("Gecko extension initialization", 30_000) { h.value<Any?>("extension") != null }
            h.attachRecordingSession()
            passed.put(step)

            step = "reset_before_login_is_reported_without_automatic_navigation"
            h.resetFixture()
            h.main {
                h.error(HOME, RESET)
                h.assertDialog("连接被重置", "错误分类 3 · 错误代码 147")
                assertFalse(h.recovering())
            }
            h.assertNoNavigation()
            passed.put(step)

            step = "home_origin_duplicates_defer_exactly_one_home_then_failure_is_visible"
            h.resetFixture()
            h.main {
                h.observeLogin()
                h.location(HOME)
                h.error(HOME, RESET)
                assertTrue(h.recovering())
                assertNotNull(h.value<Runnable?>("recoveryNavigation"))
                assertTrue("Navigation must leave the error callback first", h.session.loads.isEmpty())
                h.error(HOME, RESET)
                h.progress.onPageStop(h.session, false)
                assertTrue("A duplicate HOME-origin reset must not cancel queued recovery", h.recovering())
                assertFalse(h.value("errorShown"))
            }
            h.await("one deferred HOME") { h.session.loads.size == 1 }
            h.main {
                assertEquals(listOf(HOME), h.session.loads)
                h.progress.onPageStart(h.session, HOME)
                h.error(HOME, RESET)
                h.assertDialog("已自动尝试打开 Playground", "连接被重置", "浏览器会话已保留")
                assertEquals("playground_recovery_failed", GeckoProfiles.diagnostics(activity).getString("stage"))
                assertFalse(h.recovering())
                assertNull("The progress toast must not outlive recovery failure", h.value<Any?>("recoveryToast"))
            }
            h.screenshot(File(directory, "recovery-failed.png"))
            passed.put(step)

            step = "manual_retry_does_not_replenish_automatic_budget"
            h.main { h.dialog().getButton(AlertDialog.BUTTON_POSITIVE).performClick() }
            h.await("actual Android dialog button handler") { h.session.loads.size == 2 }
            h.main {
                assertEquals(listOf(HOME, HOME), h.session.loads)
                assertFalse(h.value("errorShown"))
                h.error(HOME, RESET)
                assertFalse(h.recovering())
                h.assertDialog("连接被重置")
                assertFalse(h.dialogText().contains("已自动尝试打开 Playground"))
            }
            h.drain()
            h.main { assertEquals("Only the explicit manual retry may add a load", 2, h.session.loads.size) }
            passed.put(step)

            step = "old_account_callbacks_are_ignored_until_recovery_page_starts"
            h.resetFixture()
            h.main { h.observeLogin(); h.error(ACCOUNT, RESET) }
            h.await("deferred HOME after account reset") { h.session.loads.size == 1 }
            h.main {
                h.error(ACCOUNT, RESET)
                h.progress.onPageStop(h.session, false)
                assertTrue(h.recovering())
                assertFalse(h.value("errorShown"))
                assertEquals(listOf(HOME), h.session.loads)
                h.progress.onPageStart(h.session, HOME)
                h.location(HOME)
                h.progress.onPageStop(h.session, true)
                assertFalse(h.recovering())
                assertTrue(h.value("loaded"))
                assertFalse("A page-stop callback alone must never permit capture", h.eligible())
                val timeout = h.value<Runnable>("recoveryTimeout")
                assertFalse(h.handler.hasCallbacks(timeout))
            }
            passed.put(step)

            step = "real_30_second_timeout_surfaces_failure"
            h.resetFixture()
            val timeoutStarted = SystemClock.elapsedRealtime()
            h.main { h.observeLogin(); h.error(ACCOUNT, RESET) }
            h.await("actual recovery timeout", 36_000) { h.value<Boolean>("errorShown") }
            h.main {
                assertTrue("The watchdog must actually wait, not be invoked by the test",
                    SystemClock.elapsedRealtime() - timeoutStarted >= 29_000)
                assertEquals(listOf(HOME), h.session.loads)
                h.assertDialog("30 秒内仍未完成页面加载", "浏览器会话已保留")
                assertFalse(h.recovering())
                assertEquals("playground_recovery_failed", GeckoProfiles.diagnostics(activity).getString("stage"))
            }
            passed.put(step)

            step = "security_error_cancels_queued_recovery_and_is_never_swallowed"
            h.resetFixture()
            h.main {
                h.observeLogin()
                h.error(ACCOUNT, RESET)
                h.error(ACCOUNT, CERTIFICATE)
                h.assertDialog("网站安全连接验证未通过", "不会忽略安全错误")
                assertFalse(h.recovering())
                assertNull(h.value<Runnable?>("recoveryNavigation"))
            }
            h.assertNoNavigation()
            h.screenshot(File(directory, "security-failed.png"))
            h.resetFixture()
            h.main {
                h.observeLogin()
                h.error(ACCOUNT, CERTIFICATE)
                h.assertDialog("网站安全连接验证未通过")
                assertFalse(h.recovering())
            }
            h.assertNoNavigation()
            passed.put(step)

            step = "back_navigation_cancels_pending_recovery"
            h.resetFixture()
            h.main {
                h.observeLogin()
                h.error(ACCOUNT, RESET)
                h.set("canGoBack", true)
                @Suppress("DEPRECATION")
                activity.onBackPressed()
                assertEquals(1, h.session.backCount)
                assertFalse(activity.isFinishing)
                assertFalse(h.recovering())
                assertFalse(h.handler.hasCallbacks(h.value("recoveryTimeout")))
            }
            h.assertNoNavigation()
            passed.put(step)

            step = "closing_and_import_flags_block_queued_and_late_callbacks"
            for (flag in listOf("closing", "importing", "imported")) {
                h.resetFixture()
                h.main {
                    h.observeLogin()
                    h.error(ACCOUNT, RESET)
                    h.set(flag, true)
                }
                h.await("$flag cancels pending navigation") { !h.recovering() }
                h.main {
                    h.error(ACCOUNT, CERTIFICATE)
                    h.error(HOME, RESET)
                    assertFalse(h.value("errorShown"))
                    assertTrue(h.session.loads.isEmpty())
                }
            }
            passed.put(step)

            step = "capture_gate_requires_loaded_secure_active_and_no_import_in_progress"
            for (guard in listOf("loaded", "secure", "active", "importing", "imported")) {
                h.resetFixture()
                h.main {
                    h.set("page", HOME)
                    h.set("secureHost", "aistudio.google.com")
                    h.set("loaded", true); h.set("secure", true); h.set("active", true)
                    h.set(guard, guard == "importing" || guard == "imported")
                    assertFalse("Capture guard $guard must prevent eligibility", h.eligible())
                }
            }
            passed.put(step)
            report.put("ok", true)
        } catch (error: Throwable) {
            report.put("failedStep", step).put("errorClass", error.javaClass.simpleName)
            throw error
        } finally {
            try {
                harness?.detachWithoutFinishing()
                report.put("sessionsDetached", harness != null)
            } catch (error: Throwable) {
                report.put("ok", false).put("cleanupErrorClass", error.javaClass.simpleName)
                throw error
            } finally {
                reportFile.writeText(report.toString(2))
            }
        }
    }

    private class RecordingSession : GeckoSession() {
        val loads = mutableListOf<String>()
        var backCount = 0
        override fun loadUri(uri: String) { loads.add(uri) }
        override fun goBack() { backCount++ }
    }

    private class Harness(val instrumentation: Instrumentation, val activity: GeckoLoginActivity) {
        lateinit var session: RecordingSession
        lateinit var original: GeckoSession
        lateinit var navigation: GeckoSession.NavigationDelegate
        lateinit var progress: GeckoSession.ProgressDelegate
        val handler: Handler get() = value("main")

        fun <T> main(block: () -> T): T {
            var result: Result<T>? = null
            instrumentation.runOnMainSync { result = runCatching(block) }
            return result!!.getOrThrow()
        }

        fun attachRecordingSession() = main {
            assertFalse(activity.isFinishing)
            original = value<GeckoSession?>("session") ?: error("No real Gecko session")
            assertTrue("The real activity must have initialized Gecko", original.isOpen)
            navigation = original.navigationDelegate ?: error("No activity navigation delegate")
            progress = original.progressDelegate ?: error("No activity progress delegate")
            session = RecordingSession()
            original.navigationDelegate = null
            original.progressDelegate = null
            original.stop()
            set("session", session)
            assertFalse(session.isOpen)
        }

        // Each scenario starts from an independent fixture state on one Activity,
        // because creating another runtime in this process is unsupported. Resets
        // belong only to this test APK and are never part of the product's retry path.
        fun resetFixture() = main {
            handler.removeCallbacksAndMessages(null)
            value<AlertDialog?>("errorDialog")?.dismiss()
            set("errorDialog", null)
            for (name in listOf("loaded", "secure", "importing", "imported", "closing", "errorShown",
                "canGoBack", "recoveryNavigationIssued", "recoveryPageStarted")) set(name, false)
            set("active", true)
            set("page", ""); set("secureHost", "")
            set("recoveryNavigation", null); set("recoveryOriginUri", null)
            val policy = value<Any>("recovery")
            for (name in listOf("observedLogin", "attemptedRecovery", "disabled", "isRecovering")) {
                policy.javaClass.getDeclaredField(name).apply { isAccessible = true }.setBoolean(policy, false)
            }
            session.loads.clear()
            session.backCount = 0
        }

        fun observeLogin() = location(ACCOUNT)
        fun location(uri: String) = navigation.onLocationChange(session, uri, mutableListOf(), false)
        fun error(uri: String, error: WebRequestError) { navigation.onLoadError(session, uri, error) }
        fun recovering(): Boolean = value<LoginRecoveryPolicy>("recovery").isRecovering
        fun eligible(): Boolean = activity.javaClass.getDeclaredMethod("eligible")
            .apply { isAccessible = true }.invoke(activity) as Boolean
        fun dialog(): AlertDialog = value<AlertDialog?>("errorDialog") ?: error("Expected a real Android dialog")
        fun dialogText(): String = dialog().findViewById<TextView>(android.R.id.message)?.text?.toString().orEmpty()
        fun assertDialog(vararg excerpts: String) {
            assertTrue("The Android dialog must be visible", dialog().isShowing)
            assertTrue(value("errorShown"))
            for (excerpt in excerpts) assertTrue("Dialog must contain: $excerpt", dialogText().contains(excerpt))
        }

        fun await(description: String, timeout: Long = 5_000, condition: () -> Boolean) {
            val deadline = SystemClock.elapsedRealtime() + timeout
            do {
                if (main(condition)) return
                SystemClock.sleep(40)
            } while (SystemClock.elapsedRealtime() < deadline)
            fail("Timed out waiting for $description")
        }

        fun drain() { instrumentation.waitForIdleSync(); main { } }
        fun assertNoNavigation() { drain(); main { assertTrue(session.loads.isEmpty()) } }
        fun screenshot(destination: File) {
            drain()
            SystemClock.sleep(250)
            val bitmap = instrumentation.uiAutomation.takeScreenshot() ?: error("Screenshot unavailable")
            try {
                destination.outputStream().use { assertTrue(bitmap.compress(Bitmap.CompressFormat.PNG, 100, it)) }
            } finally { bitmap.recycle() }
        }

        fun detachWithoutFinishing() = main {
            handler.removeCallbacksAndMessages(null)
            set("closing", true)
            value<AlertDialog?>("errorDialog")?.dismiss()
            value<WebExtension?>("extension")?.setMessageDelegate(null, "pgproxy_bridge")
            value<GeckoView?>("view")?.releaseSession()
            if (::original.isInitialized) {
                original.navigationDelegate = null
                original.progressDelegate = null
                if (original.isOpen) original.close()
            }
            set("session", null)
            // Do not call finish()/shutdown(): the runner must deliver its result
            // before the host stops :login and deletes this synthetic profile.
        }

        @Suppress("UNCHECKED_CAST")
        fun <T> value(name: String): T = activity.javaClass.getDeclaredField(name)
            .apply { isAccessible = true }.get(activity) as T
        fun set(name: String, value: Any?) = activity.javaClass.getDeclaredField(name)
            .apply { isAccessible = true }.set(activity, value)
    }

    companion object {
        private const val HOME = GeckoLoginActivity.HOME
        private const val ACCOUNT = "https://accounts.google.com/v3/signin/challenge/dp"
        private val RESET = WebRequestError(WebRequestError.ERROR_NET_RESET, WebRequestError.ERROR_CATEGORY_NETWORK)
        private val CERTIFICATE = WebRequestError(WebRequestError.ERROR_SECURITY_BAD_CERT, WebRequestError.ERROR_CATEGORY_SECURITY)
    }
}
