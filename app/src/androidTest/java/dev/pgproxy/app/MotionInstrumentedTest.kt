package dev.pgproxy.app

import android.content.res.Configuration
import android.graphics.Bitmap
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.width
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.MotionDurationScale
import androidx.compose.ui.graphics.asAndroidBitmap
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsSelected
import androidx.compose.ui.test.assertTextEquals
import androidx.compose.ui.test.captureToImage
import androidx.compose.ui.test.ExperimentalTestApi
import androidx.compose.ui.test.getUnclippedBoundsInRoot
import androidx.compose.ui.test.hasTestTag
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.performScrollToNode
import androidx.compose.ui.test.performTextReplacement
import androidx.compose.ui.test.performTouchInput
import androidx.compose.ui.unit.Density
import androidx.compose.ui.unit.dp
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import dev.pgproxy.app.ui.AccountUi
import dev.pgproxy.app.ui.PgProxyScreen
import dev.pgproxy.app.ui.ProxyUiState
import dev.pgproxy.app.ui.UiActions
import dev.pgproxy.app.ui.UiLogEvent
import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Animation tests of the real presentation, driven by Compose's frame clock.
 * This empty test host never starts MainActivity, a browser, or a service. All
 * account/settings/log state is synthetic, and every callback stays in memory.
 */
@RunWith(AndroidJUnit4::class)
@OptIn(ExperimentalTestApi::class)
class MotionInstrumentedTest {
    private val motionDuration = object : MotionDurationScale { override var scaleFactor = 1f }
    @get:Rule val compose = createComposeRule(effectContext = motionDuration)

    private var state by mutableStateOf(fixture())
    private var toggleCount = 0
    private val copies = mutableListOf<String>()
    private val actions = UiActions(
        onToggleService = { toggleCount++; state = state.copy(running = !state.running) },
        onCopy = { copies += it },
        onThemeChange = { state = state.copy(themeMode = it) },
    )

    private fun show(fontScale: Float = 1f, widthDp: Int? = null) {
        compose.setContent {
            val density = LocalDensity.current.density
            val configuration = Configuration(LocalConfiguration.current).apply {
                this.fontScale = fontScale
                widthDp?.let { screenWidthDp = it }
            }
            CompositionLocalProvider(LocalDensity provides Density(density, fontScale), LocalConfiguration provides configuration) {
                Box(Modifier.fillMaxSize(), contentAlignment = Alignment.TopCenter) {
                    val viewport = widthDp?.let { Modifier.width(it.dp).fillMaxHeight() } ?: Modifier.fillMaxSize()
                    Box(viewport.testTag("motion_viewport")) { PgProxyScreen(state, actions) }
                }
            }
        }
        compose.waitForIdle()
        compose.mainClock.autoAdvance = false
    }

    private fun advance(milliseconds: Long) {
        compose.mainClock.advanceTimeBy(milliseconds)
        compose.waitForIdle()
    }

    private fun select(page: String, settle: Boolean = true) {
        compose.onNodeWithTag("nav_$page").performClick()
        compose.mainClock.advanceTimeByFrame()
        if (settle) advance(600)
    }

    /** Scrolling is a separate operation; only animation observations freeze the clock. */
    private fun visible(tag: String, page: String) = run {
        compose.mainClock.autoAdvance = true
        try {
            compose.onNodeWithTag("page_$page").performScrollToNode(hasTestTag(tag))
            compose.onNodeWithTag(tag).performScrollTo().assertIsDisplayed()
        } finally {
            compose.mainClock.autoAdvance = false
        }
    }

    /** Does not advance time: intermediate frames must remain genuinely intermediate. */
    private fun frame(name: String): Bitmap {
        compose.waitForIdle()
        val bitmap = compose.onNodeWithTag("motion_viewport").captureToImage().asAndroidBitmap()
        val directory = File(InstrumentationRegistry.getInstrumentation().targetContext.filesDir, "motion-validation")
        check(directory.isDirectory || directory.mkdirs())
        val destination = File(directory, "$name.png")
        destination.outputStream().use { check(bitmap.compress(Bitmap.CompressFormat.PNG, 100, it)) }
        assertTrue("A motion frame must contain image data", destination.length() > 0)
        return bitmap
    }

    @Test fun pageTransitionHasAnIntermediatePositionAndFinishesInBothDirections() {
        show()
        val indicatorStart = compose.onNodeWithTag("nav_indicator").getUnclippedBoundsInRoot()
        frame("01-overview-before")
        select("accounts", settle = false)
        frame("nav-frame-00")
        advance(48)
        frame("nav-frame-01")
        advance(48)
        val forwardMiddle = compose.onNodeWithTag("page_accounts").getUnclippedBoundsInRoot()
        val indicatorMiddle = compose.onNodeWithTag("nav_indicator").getUnclippedBoundsInRoot()
        frame("nav-frame-02")
        frame("02-accounts-enter-middle")
        advance(48)
        frame("nav-frame-03")
        advance(48)
        frame("nav-frame-04")
        advance(96)
        frame("nav-frame-05")
        advance(600)
        val accountsEnd = compose.onNodeWithTag("page_accounts").getUnclippedBoundsInRoot()
        val indicatorEnd = compose.onNodeWithTag("nav_indicator").getUnclippedBoundsInRoot()
        val selectedTab = compose.onNodeWithTag("nav_accounts").getUnclippedBoundsInRoot()
        compose.onNodeWithTag("nav_accounts").assertIsSelected()
        compose.onNodeWithTag("page_overview").assertDoesNotExist()
        assertTrue("Forward navigation should visibly approach its resting position from the right",
            forwardMiddle.left > accountsEnd.left + 0.1.dp)
        assertTrue("The selection capsule should have a real intermediate position",
            indicatorMiddle.left > indicatorStart.left && indicatorMiddle.left < indicatorEnd.left)
        assertEquals("The capsule should settle under the selected tab", selectedTab.left.value, indicatorEnd.left.value, 1f)
        frame("03-accounts-enter-complete")

        select("overview", settle = false)
        advance(80)
        val reverseMiddle = compose.onNodeWithTag("page_overview").getUnclippedBoundsInRoot()
        frame("04-overview-return-middle")
        advance(600)
        val overviewEnd = compose.onNodeWithTag("page_overview").getUnclippedBoundsInRoot()
        assertTrue("Backward navigation should approach its resting position from the left",
            reverseMiddle.left < overviewEnd.left - 0.1.dp)
        compose.onNodeWithTag("page_accounts").assertDoesNotExist()
        frame("05-overview-return-complete")
    }

    @Test fun rapidTabReversalsKeepOneSettledPageAndDoNotDispatchBusinessActions() {
        show()
        // Re-enter a page while it is still leaving; this exercises the real
        // SaveableStateProvider lifetime, rather than a duplicate test animation.
        listOf("accounts", "overview", "logs", "accounts", "settings", "logs",
            "settings", "overview", "accounts", "overview", "logs", "accounts").forEach { page ->
            select(page, settle = false)
            advance(32)
        }
        advance(800)
        compose.onNodeWithTag("nav_accounts").assertIsSelected()
        compose.onNodeWithTag("page_accounts").assertIsDisplayed()
        listOf("overview", "logs", "settings").forEach { compose.onNodeWithTag("page_$it").assertDoesNotExist() }
        assertEquals(0, toggleCount)
        assertTrue(copies.isEmpty())
        frame("06-rapid-navigation-settled")
    }

    @Test fun pausedLogsAndSearchSurviveAnimatedNavigation() {
        show()
        select("logs")
        visible("logs_search", "logs").performTextReplacement("account")
        visible("logs_filter_info", "logs").performClick()
        visible("logs_pause", "logs").performClick()
        advance(500)
        select("overview", settle = false)
        advance(64)
        compose.runOnIdle { state = state.copy(logs = state.logs + UiLogEvent(1_780_000_004.0, "account_disabled")) }
        select("logs", settle = false)
        advance(600)
        visible("logs_search", "logs").assertTextEquals("account")
        visible("logs_filter_info", "logs").assertIsSelected()
        visible("logs_copy", "logs").performClick()
        advance(500)
        compose.runOnIdle {
            assertTrue(copies.last().contains("account_imported"))
            assertFalse("Paused data must not absorb events arriving on another page", copies.last().contains("account_disabled"))
        }
        frame("07-logs-paused-after-return")
        visible("logs_pause", "logs").performClick()
        advance(500)
        visible("logs_copy", "logs").performClick()
        advance(500)
        compose.runOnIdle { assertTrue(copies.last().contains("account_disabled")) }
        visible("log_code_account_disabled", "logs").assertIsDisplayed()
        frame("08-logs-resumed")
        assertEquals(0, toggleCount)
    }

    @Test fun servicePressReleaseAndCancelUseRealHitTestingWithoutDuplicateCallbacks() {
        show()
        val toggle = compose.onNodeWithTag("service_toggle")
        toggle.performTouchInput { down(center) }
        advance(120)
        assertEquals("Pressing without release must not activate the service", 0, toggleCount)
        frame("09-service-pressed")
        toggle.performTouchInput { up() }
        compose.mainClock.advanceTimeByFrame()
        advance(80)
        frame("10-service-status-middle")
        advance(600)
        compose.onNodeWithContentDescription("停止服务").assertIsDisplayed()
        compose.onNodeWithText("运行中").assertIsDisplayed()
        assertEquals(1, toggleCount)
        frame("11-service-running")

        toggle.performTouchInput { down(center) }
        advance(120)
        toggle.performTouchInput { cancel() }
        advance(500)
        assertEquals("A cancelled press must not call the action", 1, toggleCount)
        assertTrue(state.running)

        toggle.performTouchInput { down(center); up() }
        advance(600)
        compose.onNodeWithContentDescription("启动服务").assertIsDisplayed()
        compose.onNodeWithText("已停止").assertIsDisplayed()
        assertEquals("Each completed gesture must produce exactly one callback", 2, toggleCount)
        frame("12-service-stopped")
    }

    @Test fun themeBackgroundHasAMiddleFrameAndSettlesAfterReversal() {
        show()
        val light = frame("13-theme-light")
        compose.runOnIdle { state = state.copy(themeMode = "dark") }
        compose.mainClock.advanceTimeByFrame()
        advance(80)
        val middle = frame("14-theme-middle")
        advance(600)
        val dark = frame("15-theme-dark")
        // The outer margin is the actual Scaffold background, clear of text,
        // cards and navigation; compare rendered pixels rather than color constants.
        fun background(bitmap: Bitmap) = bitmap.getPixel(1, bitmap.height / 2)
        assertNotEquals("Theme must visibly change", background(light), background(dark))
        assertNotEquals("An intermediate frame must leave the initial color", background(light), background(middle))
        assertNotEquals("An intermediate frame must precede the final color", background(dark), background(middle))
        compose.runOnIdle { state = state.copy(themeMode = "light") }
        compose.mainClock.advanceTimeByFrame()
        advance(600)
        val restored = frame("16-theme-restored")
        assertEquals(background(light), background(restored))
        assertEquals(0, toggleCount)
    }

    @Test fun controlsRemainReachableAfterTransitionsAtDoubleFontSize() {
        show(fontScale = 2f, widthDp = 320)
        val controls = linkedMapOf(
            "accounts" to listOf("account_check_$ACCOUNT_ID", "account_delete_$ACCOUNT_ID"),
            "logs" to listOf("logs_pause", "logs_copy"),
            "settings" to listOf("settings_save"),
        )
        controls.forEach { (page, tags) ->
            select(page)
            tags.forEach { tag ->
                val bounds = visible(tag, page).getUnclippedBoundsInRoot()
                val pageBounds = compose.onNodeWithTag("page_$page").getUnclippedBoundsInRoot()
                val navBounds = compose.onNodeWithTag("nav_$page").getUnclippedBoundsInRoot()
                assertTrue("$tag must remain above navigation after animation", bounds.bottom <= navBounds.top + 1.dp)
                assertTrue("$tag must be fully within the page", bounds.top >= pageBounds.top - 1.dp && bounds.bottom <= pageBounds.bottom + 1.dp)
                assertTrue("$tag must fit the narrow width", bounds.left >= pageBounds.left - 1.dp && bounds.right <= pageBounds.right + 1.dp)
            }
            advance(500)
            frame("17-$page-double-font-settled")
        }
        assertEquals(0, toggleCount)
        assertTrue(copies.isEmpty())
    }

    @Test fun zeroDurationScaleKeepsNavigationStateAndControlsUsable() {
        // Inject the public Compose duration-scale context, without changing any
        // emulator-wide accessibility or developer setting.
        motionDuration.scaleFactor = 0f
        show()
        val restingLeft = compose.onNodeWithTag("page_overview").getUnclippedBoundsInRoot().left
        select("accounts", settle = false)
        advance(64)
        compose.onNodeWithTag("page_overview").assertDoesNotExist()
        val page = compose.onNodeWithTag("page_accounts").getUnclippedBoundsInRoot()
        val capsule = compose.onNodeWithTag("nav_indicator").getUnclippedBoundsInRoot()
        val tab = compose.onNodeWithTag("nav_accounts").getUnclippedBoundsInRoot()
        assertEquals(restingLeft.value, page.left.value, 0.1f)
        assertEquals(tab.left.value, capsule.left.value, 1f)
        select("overview", settle = false)
        advance(64)
        compose.onNodeWithTag("service_toggle").performClick()
        advance(64)
        compose.onNodeWithContentDescription("停止服务").assertIsDisplayed()
        compose.onNodeWithText("运行中").assertIsDisplayed()
        compose.onNodeWithText("已停止").assertDoesNotExist()
        assertEquals(1, toggleCount)
        frame("18-zero-duration-scale")
    }

    companion object {
        private const val ACCOUNT_ID = "33333333333333333333333333333333"
        private fun fixture() = ProxyUiState(
            ready = true, apiKey = "motion-only-synthetic-token", themeMode = "light", version = "MOTION TEST",
            accounts = listOf(AccountUi(ACCOUNT_ID, "动效合成账号", status = "verified", modelCount = 3)),
            logs = listOf(UiLogEvent(1_780_000_001.0, "service_started"),
                UiLogEvent(1_780_000_002.0, "account_imported"), UiLogEvent(1_780_000_003.0, "rate_limit")),
        )
    }
}
