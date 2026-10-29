package dev.pgproxy.app

import android.content.res.Configuration
import android.graphics.Bitmap
import android.os.SystemClock
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
import androidx.compose.ui.graphics.asAndroidBitmap
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.assertIsOff
import androidx.compose.ui.test.assertIsOn
import androidx.compose.ui.test.assertIsSelected
import androidx.compose.ui.test.assertTextContains
import androidx.compose.ui.test.assertTextEquals
import androidx.compose.ui.test.captureToImage
import androidx.compose.ui.test.getUnclippedBoundsInRoot
import androidx.compose.ui.test.hasTestTag
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.performScrollToNode
import androidx.compose.ui.test.performTextReplacement
import androidx.compose.ui.test.performSemanticsAction
import androidx.compose.ui.semantics.SemanticsActions
import androidx.compose.ui.text.TextLayoutResult
import androidx.compose.ui.unit.Density
import androidx.compose.ui.unit.dp
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import dev.pgproxy.app.ui.AccountUi
import dev.pgproxy.app.ui.BackgroundUiState
import dev.pgproxy.app.ui.PendingLoginUi
import dev.pgproxy.app.ui.NetworkInfoData
import dev.pgproxy.app.ui.NetworkInfoUi
import dev.pgproxy.app.ui.PgProxyScreen
import dev.pgproxy.app.ui.ProxyUiState
import dev.pgproxy.app.ui.ServiceSettings
import dev.pgproxy.app.ui.UiActions
import dev.pgproxy.app.ui.UiLogEvent
import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Presentation-only tests. The Compose rule launches its empty ComponentActivity,
 * never MainActivity. All callbacks are in-memory fakes: no service, browser,
 * credential store, public API, or Google endpoint is accessed by this class.
 *
 * Select this class explicitly when running UI validation; other instrumentation
 * classes in this project intentionally exercise runtime and account integration.
 */
@RunWith(AndroidJUnit4::class)
class UIInstrumentedTest {
    @get:Rule val compose = createComposeRule()

    private var state by mutableStateOf(sampleState())
    private var presentation by mutableStateOf(Presentation())
    private val added = mutableListOf<String>()
    private val deleted = mutableListOf<String>()
    private val discardedLogins = mutableListOf<String>()
    private val enabled = mutableListOf<Pair<String, Boolean>>()
    private val checked = mutableListOf<String>()
    private val relogins = mutableListOf<Pair<String, String>>()
    private val configured = mutableListOf<ServiceSettings>()
    private val copied = mutableListOf<String>()
    private val themes = mutableListOf<String>()
    private var serviceToggles = 0
    private var repositoryOpens = 0
    private var issuesOpens = 0
    private val groupOpens = mutableListOf<String>()
    private val systemSettingsOpens = mutableListOf<String>()

    private val actions = UiActions(
        onToggleService = { serviceToggles++; state = state.copy(running = !state.running) },
        onAddAccount = { added += it },
        onLogin = { id, label -> relogins += id to label },
        onEnableAccount = { id, value ->
            enabled += id to value
            state = state.copy(accounts = state.accounts.map { if (it.id == id) it.copy(enabled = value) else it })
        },
        onCheckAccount = { checked += it },
        onDeleteAccount = { id ->
            deleted += id
            state = state.copy(accounts = state.accounts.filterNot { it.id == id })
        },
        onDiscardLogin = { id ->
            discardedLogins += id
            state = state.copy(pendingLogins = state.pendingLogins.filterNot { it.id == id })
        },
        onConfigure = { value ->
            configured += value
            state = state.copy(port = value.port, lan = value.lan, apiKey = value.apiKey,
                timeout = value.timeout, queueTimeout = value.queueTimeout, keepAwake = value.keepAwake)
        },
        onCopy = { copied += it },
        onThemeChange = { themes += it; state = state.copy(themeMode = it) },
        onOpenRepository = { repositoryOpens++ },
        onOpenIssues = { issuesOpens++ },
        onOpenQqGroup = { groupOpens += it },
        onOpenNotificationSettings = { systemSettingsOpens += "notifications" },
        onOpenBatterySettings = { systemSettingsOpens += "battery" },
        onOpenAppSettings = { systemSettingsOpens += "app" },
    )

    private fun show(initial: ProxyUiState = sampleState(), style: Presentation = Presentation()) {
        state = initial
        presentation = style
        compose.setContent {
            val baseDensity = LocalDensity.current
            val baseConfiguration = LocalConfiguration.current
            val adjustedConfiguration = Configuration(baseConfiguration).apply {
                fontScale = presentation.fontScale
                presentation.widthDp?.let { screenWidthDp = it }
            }
            CompositionLocalProvider(
                LocalDensity provides Density(baseDensity.density, presentation.fontScale),
                LocalConfiguration provides adjustedConfiguration,
            ) {
                Box(Modifier.fillMaxSize(), contentAlignment = Alignment.TopCenter) {
                    val viewport = presentation.widthDp?.let { Modifier.width(it.dp).fillMaxHeight() }
                        ?: Modifier.fillMaxSize()
                    Box(viewport.testTag("ui_test_viewport")) { PgProxyScreen(state, actions) }
                }
            }
        }
    }

    private fun navigate(page: String) {
        compose.onNodeWithTag("nav_$page").assertIsDisplayed().performClick()
        compose.onNodeWithTag("nav_$page").assertIsSelected()
        compose.onNodeWithTag("page_$page").assertIsDisplayed()
    }

    private fun visible(tag: String, page: String) = compose.onNodeWithTag("page_$page")
        .performScrollToNode(hasTestTag(tag)).let { compose.onNodeWithTag(tag) }

    private fun field(tag: String, value: String) = visible(tag, "settings").performScrollTo().performTextReplacement(value)

    private fun saveDisabled() {
        visible("settings_save", "settings").performScrollTo().assertIsNotEnabled()
        compose.runOnIdle { assertTrue("Invalid edits must not escape into a configure callback", configured.isEmpty()) }
    }

    private fun screenshot(name: String) {
        compose.mainClock.advanceTimeBy(500)
        compose.waitForIdle()
        val bitmap = compose.onNodeWithTag("ui_test_viewport").captureToImage().asAndroidBitmap()
        saveScreenshot(name, bitmap)
    }

    /** Dialogs have a separate Android window, outside the Compose viewport capture. */
    private fun dialogScreenshot(name: String) {
        compose.mainClock.advanceTimeBy(500)
        compose.waitForIdle()
        // Android window alpha transitions use real time, not Compose's test clock.
        InstrumentationRegistry.getInstrumentation().waitForIdleSync()
        SystemClock.sleep(400)
        val bitmap = requireNotNull(InstrumentationRegistry.getInstrumentation().uiAutomation.takeScreenshot()) {
            "Could not capture the synthetic dialog window"
        }
        try { saveScreenshot(name, bitmap) } finally { bitmap.recycle() }
    }

    private fun saveScreenshot(name: String, bitmap: Bitmap) {
        val directory = File(InstrumentationRegistry.getInstrumentation().targetContext.filesDir, "ui-validation")
        check(directory.isDirectory || directory.mkdirs()) { "Could not create the UI screenshot directory" }
        val destination = File(directory, "$name.png")
        destination.outputStream().use { stream ->
            check(bitmap.compress(Bitmap.CompressFormat.PNG, 100, stream)) { "Could not encode UI screenshot" }
        }
        assertTrue("UI screenshot should contain pixels", destination.length() > 0)
    }

    private fun assertActionAboveNavigation(tag: String, page: String) {
        val action = visible(tag, page).performScrollTo().assertIsDisplayed().getUnclippedBoundsInRoot()
        val pageBounds = compose.onNodeWithTag("page_$page").getUnclippedBoundsInRoot()
        val navigation = compose.onNodeWithTag("nav_$page").getUnclippedBoundsInRoot()
        // Unclipped bounds catch partially hidden controls which assertIsDisplayed alone allows.
        assertTrue("$tag must fit above the bottom navigation", action.bottom <= navigation.top + 1.dp)
        assertTrue("$tag must fit inside the visible page vertically",
            action.top >= pageBounds.top - 1.dp && action.bottom <= pageBounds.bottom + 1.dp)
        assertTrue("$tag must fit inside the narrow page horizontally",
            action.left >= pageBounds.left - 1.dp && action.right <= pageBounds.right + 1.dp)
    }

    @Test fun navigationAndServiceControlUseOnlyInjectedCallbacks() {
        show()
        compose.onNodeWithTag("page_overview").assertIsDisplayed()
        listOf("accounts", "logs", "settings", "overview").forEach(::navigate)
        visible("service_toggle", "overview").assertIsEnabled().performClick()
        compose.runOnIdle {
            assertEquals(1, serviceToggles)
            assertTrue(state.running)
            assertTrue(configured.isEmpty())
            assertTrue(checked.isEmpty())
        }
        screenshot("overview-running-light")
    }

    @Test fun accountDialogsTrimNamesAndRequireDeleteConfirmation() {
        show()
        navigate("accounts")
        compose.onNodeWithTag("account_add").performClick()
        dialogScreenshot("account-add-dialog-light")
        compose.onNodeWithTag("account_add_name").performTextReplacement("   ")
        compose.onNodeWithTag("account_add_confirm").assertIsEnabled()
        compose.onNodeWithTag("account_add_name").performTextReplacement("A".repeat(81))
        compose.onNodeWithTag("account_add_confirm").assertIsNotEnabled()
        compose.onNodeWithText("取消").performClick()
        compose.runOnIdle { assertTrue(added.isEmpty()) }

        compose.onNodeWithTag("account_add").performClick()
        compose.onNodeWithTag("account_add_name").performTextReplacement("  界面测试账号  ")
        compose.onNodeWithTag("account_add_confirm").assertIsEnabled().performClick()
        compose.runOnIdle { assertEquals(listOf("界面测试账号"), added) }

        visible("account_delete_$ACCOUNT_ID", "accounts").performClick()
        dialogScreenshot("account-delete-dialog-light")
        compose.onNodeWithText("取消").performClick()
        compose.runOnIdle { assertTrue(deleted.isEmpty()) }
        visible("account_delete_$ACCOUNT_ID", "accounts").performClick()
        compose.onNodeWithTag("account_delete_confirm").performClick()
        compose.runOnIdle { assertEquals(listOf(ACCOUNT_ID), deleted) }
        compose.onNodeWithTag("account_delete_$ACCOUNT_ID").assertDoesNotExist()
    }

    @Test fun pendingLoginContinueAndDiscardRequireExplicitUserActions() {
        show()
        navigate("accounts")
        visible("account_continue_$PENDING_ID", "accounts").performClick()
        compose.runOnIdle {
            assertEquals(listOf(PENDING_ID to "合成待完成登录"), relogins)
            assertTrue(discardedLogins.isEmpty())
        }
        visible("account_discard_$PENDING_ID", "accounts").performClick()
        compose.onNodeWithText("取消").performClick()
        compose.runOnIdle {
            assertTrue(discardedLogins.isEmpty())
            assertTrue(state.pendingLogins.any { it.id == PENDING_ID })
        }
        visible("account_discard_$PENDING_ID", "accounts").performClick()
        compose.onNodeWithTag("account_discard_confirm").performClick()
        compose.runOnIdle {
            assertEquals(listOf(PENDING_ID), discardedLogins)
            assertTrue(state.pendingLogins.isEmpty())
            assertEquals(listOf(ACCOUNT_ID), state.accounts.map { it.id })
            assertTrue(deleted.isEmpty())
        }
        compose.onNodeWithTag("account_continue_$PENDING_ID").assertDoesNotExist()
    }

    @Test fun accountSwitchCheckAndReloginStayInsideFakeCallbacks() {
        show()
        navigate("accounts")
        visible("account_toggle_$ACCOUNT_ID", "accounts").performClick()
        compose.runOnIdle { assertEquals(listOf(ACCOUNT_ID to false), enabled) }
        visible("account_toggle_$ACCOUNT_ID", "accounts").performClick()
        visible("account_check_$ACCOUNT_ID", "accounts").performClick()
        visible("account_relogin_$ACCOUNT_ID", "accounts").performClick()
        compose.runOnIdle {
            assertEquals(listOf(ACCOUNT_ID to false, ACCOUNT_ID to true), enabled)
            assertEquals(listOf(ACCOUNT_ID), checked)
            assertEquals(listOf(ACCOUNT_ID to "合成账号 A"), relogins)
        }
    }

    @Test fun settingsRejectInvalidInputAndSubmitOneValidatedConfiguration() {
        show()
        navigate("settings")
        saveDisabled()
        listOf("", "1023", "65536").forEach { field("settings_port", it); saveDisabled() }
        field("settings_port", "9090")
        listOf("", "ab", "A".repeat(257), "not a valid token", "界面测试密钥十二个字符").forEach {
            field("settings_key", it); saveDisabled()
        }
        field("settings_key", SYNTHETIC_KEY)
        listOf("0", "601").forEach { field("settings_timeout", it); saveDisabled() }
        field("settings_timeout", "240")
        listOf("0", "601").forEach { field("settings_queue_timeout", it); saveDisabled() }
        field("settings_queue_timeout", "45")
        visible("settings_lan", "settings").performClick()
        visible("settings_save", "settings").assertIsEnabled().performClick()
        compose.runOnIdle {
            assertEquals(listOf(ServiceSettings(9090, true, SYNTHETIC_KEY, 240, 45)), configured)
        }
        compose.onNodeWithTag("settings_save").assertIsNotEnabled()
    }

    @Test fun credentialNavigationAndMetricsMatchTheNewDefaultSettings() {
        show()
        compose.onNodeWithText("启用凭证").assertIsDisplayed()
        compose.onNodeWithText("有效凭证").assertIsDisplayed()
        // Inspect the bottom of the lazy page as well: an uncomposed shortcut
        // below the fold must not make this removal regression pass accidentally.
        compose.onNodeWithTag("page_overview").performScrollToNode(hasText("PG PROXY  /  UI TEST"))
        compose.onNodeWithText("快捷入口").assertDoesNotExist()
        compose.onNodeWithText("账号管理").assertDoesNotExist()
        compose.onNodeWithText("查看服务动态与异常记录").assertDoesNotExist()

        compose.onNodeWithTag("nav_accounts").assertTextEquals("凭证").performClick()
        compose.onNodeWithTag("page_accounts").assertIsDisplayed()
        visible("account_check_$ACCOUNT_ID", "accounts").assertTextEquals("检查凭证")

        navigate("settings")
        visible("settings_port", "settings").assertTextContains("8888")
        field("settings_key", "123")
        visible("settings_save", "settings").assertIsEnabled().performClick()
        compose.runOnIdle {
            assertEquals(listOf(ServiceSettings(8888, false, "123", 180, 30)), configured)
            assertEquals(0, serviceToggles)
            assertTrue(checked.isEmpty() && relogins.isEmpty())
        }
    }

    @Test fun themeSelectionIsIndependentFromServiceSettings() {
        show()
        navigate("settings")
        listOf("dark", "light", "system").forEach { theme ->
            visible("settings_theme_$theme", "settings").performClick()
            compose.runOnIdle { assertEquals(theme, state.themeMode) }
        }
        compose.runOnIdle {
            assertEquals(listOf("dark", "light", "system"), themes)
            assertTrue(configured.isEmpty())
        }
    }

    @Test fun logsFilterSearchPauseAndCopyUseSyntheticEvents() {
        show()
        navigate("logs")
        visible("logs_filter_error", "logs").performScrollTo().performClick()
        visible("log_code_waa_unavailable", "logs").assertIsDisplayed()
        compose.onNodeWithTag("log_code_service_started").assertDoesNotExist()
        compose.onNodeWithTag("log_code_rate_limit").assertDoesNotExist()
        visible("logs_filter_warning", "logs").performScrollTo().performClick()
        visible("log_code_rate_limit", "logs").assertIsDisplayed()
        compose.onNodeWithTag("log_code_waa_unavailable").assertDoesNotExist()
        visible("logs_filter_info", "logs").performScrollTo().performClick()
        visible("log_code_service_started", "logs").assertIsDisplayed()
        visible("logs_filter_all", "logs").performScrollTo().performClick()
        visible("logs_search", "logs").performTextReplacement("校验运行环境")
        visible("log_code_waa_unavailable", "logs").assertIsDisplayed()
        compose.onNodeWithTag("log_code_service_started").assertDoesNotExist()
        visible("logs_copy", "logs").performClick()
        compose.runOnIdle {
            assertEquals(1, copied.size)
            assertTrue(copied.single().contains("校验运行环境不可用"))
            assertFalse(copied.single().contains("服务已启动"))
            assertFalse(copied.single().contains(SYNTHETIC_KEY))
        }
        visible("logs_search", "logs").performTextReplacement("没有这条合成日志")
        compose.onNodeWithTag("page_logs").performScrollToNode(hasText("没有匹配的记录"))
        compose.onNodeWithText("没有匹配的记录").assertIsDisplayed()
        visible("logs_search", "logs").performTextReplacement("")
        visible("logs_pause", "logs").performClick()
        navigate("overview")
        compose.runOnIdle { state = state.copy(logs = state.logs + UiLogEvent(1_780_000_004.0, "account_disabled")) }
        navigate("logs")
        compose.onNodeWithTag("log_code_account_disabled").assertDoesNotExist()
        visible("logs_pause", "logs").performClick()
        visible("log_code_account_disabled", "logs").assertIsDisplayed()
    }

    @Test fun busyAndDisconnectedStatesDisableAccountAndServiceMutations() {
        show(sampleState().copy(busy = true))
        visible("service_toggle", "overview").assertIsNotEnabled()
        navigate("accounts")
        compose.onNodeWithTag("account_add").assertIsNotEnabled()
        visible("account_toggle_$ACCOUNT_ID", "accounts").assertIsNotEnabled()
        visible("account_check_$ACCOUNT_ID", "accounts").assertIsNotEnabled()
        visible("account_delete_$ACCOUNT_ID", "accounts").assertIsNotEnabled()
        compose.runOnIdle { state = state.copy(busy = false, ready = false) }
        visible("account_add", "accounts").assertIsNotEnabled()
        navigate("overview")
        visible("service_toggle", "overview").assertIsNotEnabled()
        compose.runOnIdle {
            assertEquals(0, serviceToggles)
            assertTrue(added.isEmpty() && enabled.isEmpty() && checked.isEmpty() && deleted.isEmpty())
        }
    }

    @Test fun emptyAccountsAndLogsRemainUsable() {
        show(sampleState().copy(accounts = emptyList(), pendingLogins = emptyList(), logs = emptyList()))
        navigate("accounts")
        compose.onNodeWithTag("account_add").assertIsEnabled()
        screenshot("accounts-empty-light")
        navigate("logs")
        compose.onNodeWithTag("page_logs").performScrollToNode(hasText("暂无运行记录"))
        compose.onNodeWithText("暂无运行记录").assertIsDisplayed()
        screenshot("logs-empty-light")
    }

    @Test fun disconnectedStateExplainsRecoveryAndDoesNotExposeServiceActions() {
        show(sampleState().copy(ready = false, running = true, connectionError = "服务连接已中断，请刷新状态。"))
        compose.onNodeWithTag("connection_error").assertTextEquals("服务连接已中断，请刷新状态。")
        compose.onNodeWithText("连接暂时中断").assertIsDisplayed()
        visible("service_toggle", "overview").assertIsNotEnabled()
        compose.onNodeWithTag("overview_refresh").assertIsEnabled()
        compose.runOnIdle { state = state.copy(refreshing = true) }
        compose.onNodeWithTag("overview_refresh").assertIsNotEnabled()
        screenshot("overview-disconnected")
        compose.runOnIdle { state = state.copy(ready = true, refreshing = false, connectionError = "") }
        compose.onNodeWithTag("connection_error").assertDoesNotExist()
        compose.onNodeWithText("运行中").assertIsDisplayed()
        assertEquals(0, serviceToggles)
    }

    @Test fun communityLinksRemainAvailableWhenTheServiceIsDisconnected() {
        show(sampleState().copy(ready = false))
        navigate("settings")
        visible("settings_repository", "settings").performClick()
        visible("settings_issues", "settings").assertIsEnabled().performClick()
        listOf("1", "2", "gms").forEach { visible("settings_qq_group_$it", "settings").performClick() }
        compose.runOnIdle {
            assertEquals(1, repositoryOpens)
            assertEquals(1, issuesOpens)
            assertEquals(listOf(ProjectLinks.QQ_GROUP_ONE, ProjectLinks.QQ_GROUP_TWO, ProjectLinks.QQ_GROUP_GMS), groupOpens)
            assertTrue(configured.isEmpty())
        }
        screenshot("settings-community")
    }

    @Test fun unsavedConfigurationSurvivesSnapshotUpdatesAndPageChanges() {
        show()
        navigate("settings")
        field("settings_port", "9999")
        navigate("overview")
        compose.runOnIdle { state = state.copy(requestsSucceeded = 8, requestsFailed = 2, running = true) }
        navigate("settings")
        visible("settings_port", "settings").assertTextContains("9999")
        visible("settings_save", "settings").assertIsEnabled()
        visible("settings_reset", "settings").performClick()
        visible("settings_port", "settings").assertTextContains("8888")
        compose.runOnIdle { assertTrue(configured.isEmpty()) }
    }

    @Test fun backgroundSettingsRemainAvailableWithoutAServiceAndUnknownStatesStayUnknown() {
        show(sampleState().copy(ready = false, background = BackgroundUiState()))
        navigate("settings")
        visible("settings_background_notifications", "settings")
            .assertTextContains("状态未获取，点击检查通知设置").assertIsEnabled().performClick()
        visible("settings_background_battery", "settings")
            .assertTextContains("状态未获取，点击检查电池设置").assertIsEnabled().performClick()
        visible("settings_background_app", "settings").assertIsEnabled().performClick()
        visible("settings_keep_awake", "settings").assertIsNotEnabled()
        visible("settings_wake_lock_status", "settings").assertTextEquals("运行状态待同步")
        compose.runOnIdle {
            assertEquals(listOf("notifications", "battery", "app"), systemSettingsOpens)
            assertTrue(configured.isEmpty())
        }
        compose.runOnIdle { state = state.copy(background = BackgroundUiState(true, true, false)) }
        visible("settings_background_notifications", "settings").assertTextContains("已允许，服务运行时显示通知")
        visible("settings_background_battery", "settings").assertTextContains("已忽略 Android 电池优化")
        screenshot("settings-background-permissions-light")
    }

    @Test fun keepAwakeDraftSurvivesNavigationAndOnlyTakesEffectAfterSave() {
        show()
        navigate("settings")
        visible("settings_keep_awake", "settings").assertIsOff().performClick()
        visible("settings_wake_lock_status", "settings").assertTextEquals("当前未保持 CPU 唤醒")
        compose.runOnIdle { assertFalse(state.keepAwake); assertTrue(configured.isEmpty()) }
        navigate("overview")
        compose.runOnIdle { state = state.copy(requestsSucceeded = 13) }
        navigate("settings")
        visible("settings_keep_awake", "settings").assertIsOn()
        visible("settings_save", "settings").assertIsEnabled().performClick()
        compose.runOnIdle {
            assertEquals(listOf(ServiceSettings(8888, false, SYNTHETIC_KEY, 180, 30, true)), configured)
            assertTrue(state.keepAwake)
            assertEquals(0, serviceToggles)
        }
        visible("settings_wake_lock_status", "settings").assertTextEquals("已开启，启动服务后生效")
        visible("settings_keep_awake", "settings").performClick()
        visible("settings_reset", "settings").performClick()
        visible("settings_keep_awake", "settings").assertIsOn()
        // A subsequent external snapshot still updates a field without an unsaved edit.
        compose.runOnIdle { state = state.copy(keepAwake = false) }
        visible("settings_keep_awake", "settings").assertIsOff()
    }

    @Test fun backgroundControlsAndFailureFeedbackFitNarrowScreensWithLargeText() {
        show(sampleState().copy(running = true, keepAwake = true,
            wakeLockError = "CPU 唤醒暂不可用，请关闭后重试。",
            background = BackgroundUiState(false, false, true)), Presentation(320, 2f))
        navigate("settings")
        listOf("settings_background_notifications", "settings_background_battery", "settings_background_app").forEach {
            assertActionAboveNavigation(it, "settings")
            visible(it, "settings").performClick()
        }
        visible("settings_background_app", "settings").assertTextContains("系统已限制后台活动，请手动调整")
        screenshot("settings-background-permissions-large-font")
        assertActionAboveNavigation("settings_keep_awake", "settings")
        visible("settings_keep_awake", "settings").assertIsOn()
        visible("settings_wake_lock_status", "settings").assertTextEquals("CPU 唤醒暂不可用，请关闭后重试。")
        screenshot("settings-background-wake-error-large-font")
        compose.runOnIdle { state = state.copy(wakeLockError = "", wakeLockHeld = true) }
        visible("settings_wake_lock_status", "settings").assertTextEquals("当前正在保持 CPU 唤醒")
        compose.runOnIdle {
            assertEquals(listOf("notifications", "battery", "app"), systemSettingsOpens)
            assertTrue(configured.isEmpty())
        }
    }

    @Test fun networkDetailsAndRetryKeepCachedIpv6UsableWithLargeText() {
        val ip = "2001:db8:1234:5678:abcd:ef01:2345:6789"
        show(sampleState().copy(networkInfo = NetworkInfoUi(
            data = NetworkInfoData(ip, "US", "合成地区 · 城市", "较长的合成网络组织名称", "AS64500", "商业",
                12, listOf("代理"), "IPQuery", 1_780_000_000), error = "网络查询超时")), Presentation(320, 2f))
        val titleLayout = mutableListOf<TextLayoutResult>()
        visible("network_title", "overview").performSemanticsAction(SemanticsActions.GetTextLayoutResult) { it(titleLayout) }
        assertEquals("Large text must not squeeze the network heading into a one-character last line", 1, titleLayout.single().lineCount)
        assertActionAboveNavigation("network_copy_ip", "overview")
        visible("network_copy_ip", "overview").performClick()
        visible("network_details_toggle", "overview").performClick()
        visible("network_details", "overview").assertIsDisplayed()
        screenshot("network-ipv6-error-large-font")
        compose.runOnIdle { state = state.copy(networkInfo = state.networkInfo.copy(loading = true)) }
        visible("network_refresh", "overview").assertIsNotEnabled()
        compose.onNodeWithText("刷新未完成 · 网络查询超时").assertDoesNotExist()
        compose.runOnIdle { assertEquals(listOf(ip), copied) }
    }

    @Test fun screenshotAllPagesInLightTheme() = capturePages("light", Presentation())

    @Test fun screenshotAllPagesInDarkTheme() = capturePages("dark", Presentation())

    @Test fun screenshotAllPagesAtNarrowWidthWithLargeFont() =
        capturePages("light", Presentation(widthDp = 320, fontScale = 1.6f))

    @Test fun screenshotAllPagesInDarkThemeWithExtraLargeFont() =
        capturePages("dark", Presentation(widthDp = 320, fontScale = 2.0f))

    private fun capturePages(theme: String, style: Presentation) {
        show(sampleState().copy(themeMode = theme), style)
        val suffix = if (style.widthDp == null) theme else "$theme-${style.widthDp}dp-font${(style.fontScale * 100).toInt()}"
        listOf("overview", "accounts", "logs", "settings").forEach { page ->
            navigate(page)
            screenshot("$page-$suffix")
            if (style.fontScale > 1f) {
                val actions = when (page) {
                    "accounts" -> listOf("account_check_$ACCOUNT_ID", "account_delete_$ACCOUNT_ID")
                    "logs" -> listOf("logs_pause", "logs_copy")
                    else -> emptyList()
                }
                actions.forEach { assertActionAboveNavigation(it, page) }
                if (actions.isNotEmpty()) screenshot("$page-actions-$suffix")
            }
        }
        // A screenshot of the top of a long form is insufficient: its final action
        // must still be reachable with large text on a narrow screen.
        assertActionAboveNavigation("settings_save", "settings")
        if (style.widthDp != null) screenshot("settings-save-$suffix")
        compose.runOnIdle {
            assertEquals(0, serviceToggles)
            assertTrue(configured.isEmpty() && checked.isEmpty() && relogins.isEmpty())
        }
    }

    private data class Presentation(val widthDp: Int? = null, val fontScale: Float = 1f)

    companion object {
        private const val ACCOUNT_ID = "11111111111111111111111111111111"
        private const val PENDING_ID = "22222222222222222222222222222222"
        private const val SYNTHETIC_KEY = "ui-only-synthetic-token-123456"

        private fun sampleState() = ProxyUiState(
            ready = true, running = false, port = 8888, apiKey = SYNTHETIC_KEY,
            accounts = listOf(AccountUi(ACCOUNT_ID, "合成账号 A", enabled = true,
                status = "verified", lastChecked = 1_780_000_000.0, lastModel = "界面演示模型", modelCount = 3)),
            pendingLogins = listOf(PendingLoginUi(PENDING_ID, "合成待完成登录")),
            logs = listOf(
                UiLogEvent(1_780_000_001.0, "service_started"),
                UiLogEvent(1_780_000_002.0, "rate_limit"),
                UiLogEvent(1_780_000_003.0, "waa_unavailable"),
            ),
            addresses = listOf("192.0.2.10"), themeMode = "light", version = "UI TEST",
            requestsSucceeded = 12, requestsFailed = 1,
            networkInfo = NetworkInfoUi(data = NetworkInfoData("203.0.113.10", "US", "美国 · 合成地区",
                "Example Network", "AS64500", "商业", 5, emptyList(), "IPQuery", 1_780_000_000)),
        )
    }
}
