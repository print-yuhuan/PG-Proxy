package dev.pgproxy.app.ui

data class AccountUi(
    val id: String, val label: String, val enabled: Boolean = true, val status: String = "unchecked",
    val lastChecked: Double = 0.0, val lastModel: String = "", val lastError: String = "",
    val modelCount: Int = 0, val cooldownSeconds: Int = 0, val active: Boolean = false,
)
data class PendingLoginUi(val id: String, val label: String)
data class UiLogEvent(val at: Double, val code: String)
data class ServiceSettings(val port: Int, val lan: Boolean, val apiKey: String, val timeout: Int,
    val queueTimeout: Int, val keepAwake: Boolean = false)

/** These platform states do not describe vendor-specific background or auto-start switches. */
data class BackgroundUiState(
    val notificationsAllowed: Boolean? = null,
    val batteryExempt: Boolean? = null,
    val backgroundRestricted: Boolean? = null,
)
data class ProxyUiState(
    val ready: Boolean = false, val running: Boolean = false, val port: Int = 8888,
    val lan: Boolean = false, val apiKey: String = "", val timeout: Int = 180, val queueTimeout: Int = 30,
    val accounts: List<AccountUi> = emptyList(), val pendingLogins: List<PendingLoginUi> = emptyList(),
    val logs: List<UiLogEvent> = emptyList(), val addresses: List<String> = emptyList(),
    val requestsSucceeded: Long = 0, val requestsFailed: Long = 0,
    val networkInfo: NetworkInfoUi = NetworkInfoUi(),
    val keepAwake: Boolean = false, val wakeLockHeld: Boolean = false, val wakeLockError: String = "",
    val background: BackgroundUiState = BackgroundUiState(),
    val refreshing: Boolean = false, val connectionError: String = "",
    val busy: Boolean = false, val notice: String = "", val noticeSequence: Int = 0, val themeMode: String = "system",
    val version: String = "",
)

/** UI callbacks are injected: previews and UI tests never bind the service or reach Google. */
data class UiActions(
    val onRefresh: () -> Unit = {}, val onToggleService: () -> Unit = {},
    val onAddAccount: (String) -> Unit = {}, val onLogin: (String, String) -> Unit = { _, _ -> },
    val onEnableAccount: (String, Boolean) -> Unit = { _, _ -> }, val onCheckAccount: (String) -> Unit = {},
    val onDeleteAccount: (String) -> Unit = {}, val onDiscardLogin: (String) -> Unit = {},
    val onConfigure: (ServiceSettings) -> Unit = {}, val onCopy: (String) -> Unit = {},
    val onDiagnostics: () -> Unit = {}, val onCleanup: () -> Unit = {}, val onOpenLicenses: () -> Unit = {},
    val onThemeChange: (String) -> Unit = {},
    val onRefreshNetwork: () -> Unit = {},
    val onOpenRepository: () -> Unit = {}, val onOpenIssues: () -> Unit = {},
    val onOpenQqGroup: (String) -> Unit = {},
    val onOpenNotificationSettings: () -> Unit = {}, val onOpenBatterySettings: () -> Unit = {},
    val onOpenAppSettings: () -> Unit = {},
)
