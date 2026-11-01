package dev.pgproxy.app

import android.graphics.Bitmap
import android.graphics.Color
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.runtime.SideEffect
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.graphics.asAndroidBitmap
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.assertIsSelected
import androidx.compose.ui.test.assertTextContains
import androidx.compose.ui.test.assertTextEquals
import androidx.compose.ui.test.captureToImage
import androidx.compose.ui.test.getUnclippedBoundsInRoot
import androidx.compose.ui.test.hasTestTag
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.performScrollToNode
import androidx.compose.ui.test.performTextReplacement
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import dev.pgproxy.app.ui.LogDetailField
import dev.pgproxy.app.ui.LogDetailPage
import dev.pgproxy.app.ui.LogDetailResult
import dev.pgproxy.app.ui.LogDetailSection
import dev.pgproxy.app.ui.LogDetailUi
import dev.pgproxy.app.ui.PgProxyScreen
import dev.pgproxy.app.ui.PgTheme
import dev.pgproxy.app.ui.ProxyUiState
import dev.pgproxy.app.ui.UiActions
import dev.pgproxy.app.ui.UiLogEvent
import dev.pgproxy.app.ui.parseLogDetail
import org.json.JSONObject
import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/** Presentation-only regression: no service, credential store or Google request. */
@RunWith(AndroidJUnit4::class)
class LogDetailInstrumentedTest {
    @get:Rule val compose = createComposeRule()
    private val callbacks = mutableListOf<(LogDetailResult) -> Unit>()
    private val copies = mutableListOf<String>()
    private val actions = UiActions(
        onLoadLogDetail = { _, callback -> callbacks += callback },
        onCopy = { copies += it },
    )

    private fun showDetail() {
        compose.setContent { PgTheme("light") { LogDetailPage(ID, null, actions, {}) } }
        compose.waitForIdle()
    }

    private fun complete(index: Int, response: LogDetailResult = LogDetailResult(data = detail())) {
        compose.runOnIdle { callbacks[index](response) }
        compose.waitForIdle()
    }

    private fun refreshImage(): Bitmap = requireNotNull(compose.onNodeWithTag("log_detail_refresh")
        .captureToImage().asAndroidBitmap().copy(Bitmap.Config.ARGB_8888, false))

    private fun saveFrame(bitmap: Bitmap, name: String) {
        val directory = File(InstrumentationRegistry.getInstrumentation().targetContext.filesDir, "log-detail-validation")
        check(directory.isDirectory || directory.mkdirs())
        File(directory, "$name.png").outputStream().use { check(bitmap.compress(Bitmap.CompressFormat.PNG, 100, it)) }
    }

    /** Compare the icon's visible geometry, excluding button/ripple backgrounds.
     * The fixed light palette draws this icon in #626F84 on a white surface;
     * thresholded ink also avoids asserting exact GPU antialiasing pixels.
     */
    private fun ink(bitmap: Bitmap): BooleanArray {
        val pixels = IntArray(bitmap.width * bitmap.height)
        bitmap.getPixels(pixels, 0, bitmap.width, 0, 0, bitmap.width, bitmap.height)
        return BooleanArray(pixels.size) { index ->
            val color = pixels[index]
            Color.alpha(color) > 128 && Color.red(color) < 145 && Color.green(color) < 155 && Color.blue(color) < 180
        }
    }

    private fun inkDifference(first: Bitmap, second: Bitmap): Int {
        assertEquals("Refresh button width must remain fixed", first.width, second.width)
        assertEquals("Refresh button height must remain fixed", first.height, second.height)
        val left = ink(first)
        val right = ink(second)
        return left.indices.count { left[it] != right[it] }
    }

    @Test fun refreshKeepsSummaryStationaryAndFinishesItsIconAnimation() {
        showDetail()
        compose.onNodeWithTag("log_detail_refresh").assertIsNotEnabled()
        complete(0)
        compose.onNodeWithTag("log_detail_refresh").assertIsEnabled()
        val summaryBefore = compose.onNodeWithTag("log_detail_summary").getUnclippedBoundsInRoot()
        val iconBefore = refreshImage()
        saveFrame(iconBefore, "refresh-before")
        val tolerance = maxOf(2, ink(iconBefore).count { it } / 100)
        compose.mainClock.autoAdvance = false
        compose.onNodeWithTag("log_detail_refresh").performClick()
        compose.mainClock.advanceTimeByFrame()
        compose.mainClock.advanceTimeBy(144)
        compose.waitForIdle()
        compose.onNodeWithTag("log_detail_refresh").assertIsNotEnabled()
        assertEquals(summaryBefore, compose.onNodeWithTag("log_detail_summary").getUnclippedBoundsInRoot())
        compose.onNodeWithTag("log_detail_loading").assertDoesNotExist()
        val iconMiddle = refreshImage()
        saveFrame(iconMiddle, "refresh-middle")
        assertTrue("The refresh icon should visibly turn without shifting content",
            inkDifference(iconBefore, iconMiddle) > tolerance)
        compose.runOnIdle { callbacks[1](LogDetailResult(data = detail())) }
        compose.mainClock.advanceTimeBy(600)
        compose.waitForIdle()
        assertEquals(summaryBefore, compose.onNodeWithTag("log_detail_summary").getUnclippedBoundsInRoot())
        compose.onNodeWithTag("log_detail_refresh").assertIsEnabled()
        val iconAfter = refreshImage()
        saveFrame(iconAfter, "refresh-after")
        val endDifference = inkDifference(iconBefore, iconAfter)
        assertTrue("A full turn should restore the visible icon geometry: $endDifference changed ink pixels (limit $tolerance)",
            endDifference <= tolerance)
        compose.mainClock.advanceTimeBy(500)
        compose.waitForIdle()
        assertTrue("The refresh animation must finish instead of continuing to spin",
            inkDifference(iconAfter, refreshImage()) <= tolerance)
        compose.mainClock.autoAdvance = true
    }

    @Test fun failedRefreshRetainsDetailAndRetryDoesNotCollapseItsContent() {
        showDetail()
        complete(0)
        compose.onNodeWithTag("log_detail_refresh").performClick()
        compose.waitForIdle()
        complete(1, LogDetailResult(error = "详情读取暂时失败，请重试"))
        compose.onNodeWithTag("log_detail_summary").assertIsDisplayed()
        compose.onNodeWithTag("log_detail_copy").performClick()
        compose.runOnIdle { assertEquals(listOf(detail().exportText), copies) }
        val beforeRetry = compose.onNodeWithTag("log_detail_summary").getUnclippedBoundsInRoot()
        compose.onNodeWithTag("log_detail_retry").performClick()
        compose.waitForIdle()
        compose.onNodeWithTag("log_detail_retry").assertIsNotEnabled()
        assertEquals(beforeRetry, compose.onNodeWithTag("log_detail_summary").getUnclippedBoundsInRoot())
        complete(2)
        compose.onNodeWithText("详情读取暂时失败，请重试").assertDoesNotExist()
        compose.onNodeWithTag("log_detail_summary").assertIsDisplayed()
    }

    @Test fun staleCallbackCannotReplaceAnotherSelectedLog() {
        var selected by mutableStateOf(ID)
        compose.setContent { PgTheme("light") { LogDetailPage(selected, null, actions, {}) } }
        compose.waitForIdle()
        compose.runOnIdle { selected = OTHER_ID }
        compose.waitForIdle()
        compose.runOnIdle {
            callbacks[0](LogDetailResult(data = detail().copy(title = "不应出现的旧详情")))
            callbacks[1](LogDetailResult(data = detail().copy(title = "当前请求详情")))
        }
        compose.onNodeWithText("当前请求详情").assertIsDisplayed()
        compose.onNodeWithText("不应出现的旧详情").assertDoesNotExist()
    }

    @Test fun returningFromDetailRetainsFilterSearchPauseAndListPosition() {
        var state by mutableStateOf(ProxyUiState(ready = true, themeMode = "light", logs = listOf(
            UiLogEvent(1_793_513_000.0, "api_request", ID, "failure", "请求失败", "接口鉴权失败",
                "GET", "/v1/models", 401, 12.0, true, "failure", "authentication_error", "authentication"),
            UiLogEvent(1_793_513_001.0, "api_request", OTHER_ID, "success", "请求成功", "响应已完成",
                "GET", "/health", 200, 2.0, false, "success"),
        )))
        val listActions = actions.copy(onLoadLogDetail = { _, callback -> callback(LogDetailResult(data = detail())) })
        var dismissKeyboard: () -> Unit = {}
        compose.setContent {
            val focus = LocalFocusManager.current
            val keyboard = LocalSoftwareKeyboardController.current
            SideEffect { dismissKeyboard = { focus.clearFocus(force = true); keyboard?.hide() } }
            PgProxyScreen(state, listActions)
        }
        compose.onNodeWithTag("nav_logs").performClick()
        compose.waitForIdle()
        compose.onNodeWithTag("logs_filter_failure").performClick()
        compose.onNodeWithTag("logs_search").performTextReplacement("401")
        compose.runOnIdle { dismissKeyboard() }
        compose.waitForIdle()
        compose.onNodeWithTag("logs_pause").performClick()
        compose.onNodeWithText("请求失败").performScrollTo().assertIsDisplayed()
        val before = compose.onNodeWithText("请求失败").getUnclippedBoundsInRoot()
        compose.onNodeWithText("请求失败").performClick()
        compose.waitForIdle()
        compose.onNodeWithTag("page_log_detail").assertIsDisplayed()
        compose.runOnIdle {
            state = state.copy(logs = state.logs + UiLogEvent(1_793_513_002.0, "api_request", NEW_ID,
                "failure", "新请求失败", "接口鉴权失败", "GET", "/v1/models", 401))
        }
        compose.onNodeWithTag("log_detail_back").performClick()
        compose.waitForIdle()
        assertEquals(before, compose.onNodeWithText("请求失败").getUnclippedBoundsInRoot())
        compose.onNodeWithText("新请求失败").assertDoesNotExist()
        compose.onNodeWithTag("page_logs").performScrollToNode(hasTestTag("logs_search"))
        compose.onNodeWithTag("logs_search").assertTextEquals("401")
        compose.onNodeWithTag("logs_filter_failure").assertIsSelected()
        compose.onNodeWithTag("logs_pause").assertTextContains("继续显示")
    }

    @Test fun detailParserIgnoresUnrelatedSecretFieldsAndKeepsActualAndBusinessStatusesSeparate() {
        val data = JSONObject().put("id", ID).put("event", "api_request").put("title", "请求失败")
            .put("status", 200).put("method", "POST").put("path", "/v1/chat/completions")
            .put("apiKey", "top-level-key-canary").put("cookie", "top-level-cookie-canary")
            .put("headers", JSONObject().put("Authorization", "Bearer header-canary"))
            .put("error", JSONObject().put("type", "interrupted").put("code", "stream_interrupted")
                .put("httpStatus", 200).put("businessHttpStatus", 502).put("upstreamStatus", 503)
                .put("upstreamCode", "UNAVAILABLE").put("raw", "stream closed; token=[已脱敏]"))
        val parsed = parseLogDetail(data)
        assertFalse(parsed.exportText.contains("canary"))
        assertTrue(parsed.exportText.contains("200"))
        assertTrue(parsed.exportText.contains("502"))
        assertTrue(parsed.exportText.contains("503"))
        assertTrue(parsed.exportText.contains("UNAVAILABLE"))
        assertTrue(parsed.exportText.contains("token=[已脱敏]"))
    }

    private companion object {
        const val ID = "11111111111111111111111111111111"
        const val OTHER_ID = "22222222222222222222222222222222"
        const val NEW_ID = "33333333333333333333333333333333"
        fun detail() = LogDetailUi("鉴权错误详情", "接口密钥不正确，请检查客户端认证设置。", listOf(
            LogDetailSection("调用信息", listOf(LogDetailField("接口地址", "/v1/models", true),
                LogDetailField("响应 HTTP 状态码", "401", true))),
            LogDetailSection("失败详情", listOf(LogDetailField("原始错误（已脱敏）", "Invalid credentials", true)), true),
        ), "GET /v1/models · HTTP 401\nInvalid credentials")
    }
}
