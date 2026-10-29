package dev.pgproxy.app

import android.Manifest
import android.content.ActivityNotFoundException
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.enableEdgeToEdge
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.luminance
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import dev.pgproxy.app.ui.*
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import org.json.JSONObject
import java.net.Inet4Address
import java.net.NetworkInterface
import java.util.Locale
import java.util.UUID

class MainActivity : ComponentActivity() {
    private var snapshot by mutableStateOf(JSONObject())
    private var busy by mutableStateOf(false)
    private var connected by mutableStateOf(false)
    private var snapshotPending by mutableStateOf(false)
    private var connectionError by mutableStateOf("")
    private var snapshotRevision = 0
    private val pendingCalls = mutableSetOf<Int>()
    private var noticeText by mutableStateOf("")
    private var noticeSequence by mutableIntStateOf(0)
    private var notice: String
        get() = noticeText
        set(value) { noticeText = value; noticeSequence++ }
    private var detail by mutableStateOf<String?>(null)
    private var pendingLogins by mutableStateOf<List<JSONObject>>(emptyList())
    private var themeMode by mutableStateOf("system")
    private var networkInfo by mutableStateOf(NetworkInfoUi())
    private var background by mutableStateOf(BackgroundUiState())
    private val pendingDeletions = mutableSetOf<String>()
    private val connector get() = (application as PgApplication).connector
    private val notificationPermission = registerForActivityResult(ActivityResultContracts.RequestPermission()) {
        background = BackgroundSettings.read(this)
    }
    private val login = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
        refresh()
        if (result.resultCode == RESULT_OK) notice = "凭证已导入，可在凭证页检查可用性"
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        GeckoProfiles.retryPending(this)
        themeMode = getSharedPreferences("appearance", MODE_PRIVATE).getString("theme", "system") ?: "system"
        setContent {
            PgProxyScreen(uiState(), uiActions()) {
                // Switch system-bar icon contrast when the animated background crosses its midpoint.
                val darkBars = MaterialTheme.colorScheme.background.luminance() < .179f
                LaunchedEffect(darkBars) {
                    enableEdgeToEdge(
                        statusBarStyle = SystemBarStyle.auto(android.graphics.Color.TRANSPARENT, android.graphics.Color.TRANSPARENT) { darkBars },
                        navigationBarStyle = SystemBarStyle.auto(android.graphics.Color.TRANSPARENT, android.graphics.Color.TRANSPARENT) { darkBars })
                }
                detail?.let { value -> AlertDialog(onDismissRequest = { detail = null },
                    title = { Text("运行诊断") },
                    text = { SelectionContainer { Text(value, Modifier.heightIn(max = 420.dp).verticalScroll(rememberScrollState()), style = MaterialTheme.typography.bodySmall) } },
                    confirmButton = { TextButton(onClick = { detail = null }) { Text("完成") } }) }
            }
        }
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                while (true) { delay(5000); if (!busy) refresh() }
            }
        }
    }

    override fun onResume() {
        super.onResume()
        background = BackgroundSettings.read(this)
        refresh()
        refreshNetworkInfo()
    }

    override fun onDestroy() {
        pendingCalls.toList().forEach(connector::cancel)
        pendingCalls.clear()
        super.onDestroy()
    }

    /** Release screen callbacks on destruction without cancelling service-side operations. */
    private fun call(name: String, payload: JSONObject = JSONObject(), callback: (JSONObject) -> Unit) {
        var completed = false
        var id = 0
        id = connector.call(name, payload) { response ->
            completed = true
            pendingCalls.remove(id)
            if (!isDestroyed) callback(response)
        }
        if (!completed) pendingCalls.add(id)
    }

    private fun refreshNetworkInfo(force: Boolean = false) {
        if (networkInfo.loading) return
        networkInfo = networkInfo.copy(loading = true, error = "")
        call("network_info", JSONObject().put("force", force)) { response ->
            if (lifecycle.currentState == Lifecycle.State.DESTROYED) return@call
            val result = response.optJSONObject("result")
            val data = result?.optJSONObject("data")?.let { value ->
                val countryCode = value.optString("countryCode")
                val country = if (countryCode.matches(Regex("[A-Z]{2}")))
                    Locale.Builder().setRegion(countryCode).build().getDisplayCountry(Locale.SIMPLIFIED_CHINESE)
                else value.optString("country")
                NetworkInfoData(
                    ip = value.optString("ip"), countryCode = countryCode,
                    location = listOf(country, value.optString("region"), value.optString("city"))
                        .filter { it.isNotBlank() }.distinct().joinToString(" · "),
                    organization = value.optString("organization").ifBlank { value.optString("isp") },
                    asn = value.optString("asn"),
                    networkType = when (value.optString("networkType")) {
                        "hosting" -> "机房"; "mobile" -> "移动"; "business" -> "商业"; "residential" -> "住宅"; else -> ""
                    },
                    riskScore = (value.opt("riskScore") as? Number)?.toInt()?.takeIf { it in 0..100 },
                    riskLabels = value.optJSONArray("riskFlags")?.let { flags ->
                        (0 until flags.length()).mapNotNull { index -> when (flags.optString(index)) {
                            "vpn" -> "VPN"; "proxy" -> "代理"; "tor" -> "Tor"; "abuser" -> "滥用记录"; else -> null
                        } }
                    } ?: emptyList(),
                    source = value.optString("source"), updatedAt = value.optLong("updatedAt"),
                )
            }
            val error = if (response.optBoolean("ok") && result != null) result.optString("error")
                else "网络信息暂时不可用，请稍后重试"
            networkInfo = NetworkInfoUi(data = data ?: networkInfo.data, error =
                if (error.isBlank() && data == null) "暂未获取到网络信息，请重试" else error)
        }
    }

    private fun refresh() {
        if (snapshotPending || busy || isDestroyed) return
        snapshotPending = true
        val revision = snapshotRevision
        call("snapshot") { response ->
            snapshotPending = false
            // A snapshot started before a mutation must never restore stale settings or credentials.
            if (revision != snapshotRevision) return@call
            pendingLogins = GeckoProfiles.pendingLogins(this)
            val result = response.optJSONObject("result")
            if (response.optBoolean("ok") && result != null && result.has("apiKey") && result.has("accounts")) {
                snapshot = result
                connected = true
                connectionError = ""
                val deleted = GeckoProfiles.deletedAccountIds(this).toSet()
                val ids = accountList().map { account -> account.optString("id") }.filter { id -> id !in deleted }
                GeckoProfiles.reconcileImports(this, ids)
                restorePendingDeletions(deleted)
            } else {
                connected = false
                connectionError = if (response.optBoolean("ok")) "服务状态读取失败，请刷新重试。" else ErrorMessages.fromResponse(response)
            }
        }
    }

    /** Replay a durable removal intent if the app stopped before its private IPC completed. */
    private fun restorePendingDeletions(deleted: Set<String>) {
        accountList().filter { it.optString("id") in deleted }.forEach { account ->
            val id = account.getString("id")
            account.put("enabled", false).put("status", "deleting")
            if (pendingDeletions.add(id)) call("delete_account", JSONObject().put("id", id)) { result ->
                pendingDeletions.remove(id)
                if (result.optBoolean("ok") || result.optJSONObject("error")?.optString("code") == "account_not_found") {
                    GeckoProfiles.delete(this, id)
                }
            }
        }
    }

    private fun action(name: String, payload: JSONObject = JSONObject(), completed: ((JSONObject) -> Unit)? = null) {
        if (busy || !connected) return
        snapshotRevision++
        busy = true; notice = ""
        call(name, payload) { response ->
            busy = false
            response.optJSONObject("result")?.let { if (it.has("accounts") && it.has("apiKey")) snapshot = it }
            if (!response.optBoolean("ok")) {
                notice = ErrorMessages.fromResponse(response)
                if (response.optJSONObject("error")?.optString("code") in setOf("service_timeout", "service_disconnected", "service_bind_failed", "service_invalid_response", "backend_unavailable")) {
                    connected = false
                    connectionError = notice
                }
            }
            else {
                notice = when (name) {
                    "start" -> "服务已启动"
                    "stop" -> "服务已停止"
                    "configure" -> "设置已保存"
                    "check_account" -> "凭证检查通过，已完成一次真实生成"
                    "delete_account" -> "凭证已删除"
                    else -> "已更新"
                }
                response.optJSONObject("result")?.let { completed?.invoke(it) }
            }
        }
    }

    private fun openLogin(id: String = UUID.randomUUID().toString().replace("-", ""), label: String) {
        login.launch(Intent(this, GeckoLoginActivity::class.java)
            .putExtra(GeckoLoginActivity.EXTRA_ACCOUNT_ID, id).putExtra(GeckoLoginActivity.EXTRA_LABEL, label)
            .putExtra(GeckoLoginActivity.EXTRA_EXISTING_ACCOUNT, accountList().any { it.optString("id") == id }))
    }

    private fun copy(text: String) {
        getSystemService(ClipboardManager::class.java).setPrimaryClip(ClipData.newPlainText("PG Proxy", text))
        notice = "已复制"
    }

    private fun openProjectPage(url: String, label: String) {
        val intent = Intent(Intent.ACTION_VIEW, Uri.parse(url))
            .addCategory(Intent.CATEGORY_BROWSABLE)
        if (!openExternal(intent)) {
            copy(url)
            notice = "无法打开${label}页面，地址已复制"
        }
    }

    private fun openQqGroup(number: String) {
        if (number !in ProjectLinks.qqGroups) return
        val card = Uri.Builder().scheme("mqqapi").authority("card").appendPath("show_pslcard")
            .appendQueryParameter("src_type", "internal").appendQueryParameter("version", "1")
            .appendQueryParameter("uin", number).appendQueryParameter("card_type", "group")
            .appendQueryParameter("source", "qrcode").build()
        // Launch directly: Android package-visibility queries are unnecessary.
        // This only opens the group card; joining remains an action inside QQ.
        if (!openExternal(Intent(Intent.ACTION_VIEW, card).setPackage("com.tencent.mobileqq"))) {
            copy(number)
            notice = "未找到支持群名片的 QQ，群号已复制，可在 QQ 中搜索"
        }
    }

    private fun openExternal(intent: Intent): Boolean = try {
        startActivity(intent)
        true
    } catch (_: ActivityNotFoundException) {
        false
    } catch (_: SecurityException) {
        false
    }

    private fun uiState() = ProxyUiState(
        ready = connected, running = snapshot.optBoolean("running"),
        port = snapshot.optInt("port", 8888), lan = snapshot.optBoolean("lan"),
        apiKey = snapshot.optString("apiKey"), timeout = snapshot.optInt("timeout", 180),
        queueTimeout = snapshot.optInt("queueTimeout", 30),
        keepAwake = snapshot.optBoolean("keepAwake"), wakeLockHeld = snapshot.optBoolean("wakeLockHeld"),
        wakeLockError = snapshot.optString("wakeLockError"), background = background,
        requestsSucceeded = snapshot.optLong("requestsSucceeded"), requestsFailed = snapshot.optLong("requestsFailed"),
        networkInfo = networkInfo,
        refreshing = snapshotPending, connectionError = connectionError,
        accounts = accountList().map { AccountUi(
            id = it.optString("id"), label = it.optString("label"), enabled = it.optBoolean("enabled"),
            status = it.optString("status"), lastChecked = it.optDouble("lastChecked", 0.0),
            lastModel = (it.opt("lastModel") as? String).orEmpty(), lastError = (it.opt("lastError") as? String).orEmpty(),
            modelCount = it.optInt("modelCount"), cooldownSeconds = it.optInt("cooldownSeconds"), active = it.optBoolean("active")) },
        pendingLogins = pendingLogins.mapNotNull { value -> value.optString("id").takeIf { it.isNotBlank() }?.let { PendingLoginUi(it, value.optString("label", "Google 凭证")) } },
        logs = snapshot.optJSONArray("logs")?.let { list -> (0 until list.length()).mapNotNull { list.optJSONObject(it) }
            .map { UiLogEvent(it.optDouble("at", 0.0), it.optString("event")) } } ?: emptyList(),
        addresses = if (snapshot.optBoolean("lan")) localAddresses() else emptyList(),
        busy = busy, notice = notice, noticeSequence = noticeSequence, themeMode = themeMode, version = BuildConfig.VERSION_NAME,
    )

    private fun uiActions() = UiActions(
        onRefresh = { refresh(); refreshNetworkInfo(force = true) },
        onRefreshNetwork = { refreshNetworkInfo(force = true) },
        onToggleService = {
            val running = snapshot.optBoolean("running")
            if (!running && Build.VERSION.SDK_INT >= 33) notificationPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
            action(if (running) "stop" else "start")
        },
        onAddAccount = { openLogin(label = it) }, onLogin = { id, label -> openLogin(id, label) },
        onEnableAccount = { id, enabled -> action("set_enabled", JSONObject().put("id", id).put("enabled", enabled)) },
        onCheckAccount = { action("check_account", JSONObject().put("id", it)) },
        onDeleteAccount = { id ->
            if (busy || !connected) Unit
            else if (!GeckoProfiles.markForDeletion(this, id)) notice = "无法记录清理状态，请检查存储空间后重试"
            else action("delete_account", JSONObject().put("id", id)) {
                notice = if (GeckoProfiles.delete(this, id)) "凭证及浏览器会话已清理" else "浏览器清理未完成，请在设置中重试"
            }
        },
        onDiscardLogin = { id ->
            if (busy || !connected) Unit
            else if (!GeckoProfiles.markForDeletion(this, id)) notice = "无法记录清理状态，请检查存储空间后重试"
            else {
                snapshotRevision++
                busy = true
                call("delete_account", JSONObject().put("id", id)) { result ->
                    busy = false
                    val absent = result.optJSONObject("error")?.optString("code") == "account_not_found"
                    if (result.optBoolean("ok") || absent) {
                        notice = if (GeckoProfiles.delete(this, id)) "登录会话已清除" else "已标记清理，请关闭登录页后重试"
                    } else notice = "删除已排队，稍后会继续清理"
                    refresh()
                }
            }
        },
        onConfigure = { config -> action("configure", JSONObject().put("port", config.port).put("lan", config.lan)
            .put("apiKey", config.apiKey).put("timeout", config.timeout).put("queueTimeout", config.queueTimeout)
            .put("keepAwake", config.keepAwake)) },
        onCopy = ::copy,
        onDiagnostics = { action("diagnostics") { detail = it.put("browser", GeckoProfiles.diagnostics(this)).toString(2) } },
        onCleanup = { notice = if (GeckoProfiles.retryPending(this)) "浏览器会话已清理" else "清理未完成，请关闭登录页面后重试" },
        onOpenLicenses = { startActivity(Intent(this, LicensesActivity::class.java)) },
        onOpenRepository = { openProjectPage(ProjectLinks.REPOSITORY, "仓库") },
        onOpenIssues = { openProjectPage(ProjectLinks.ISSUES, "问题反馈") },
        onOpenQqGroup = ::openQqGroup,
        onOpenNotificationSettings = { openBackgroundSettings(BackgroundDestination.NOTIFICATIONS) },
        onOpenBatterySettings = { openBackgroundSettings(BackgroundDestination.BATTERY) },
        onOpenAppSettings = { openBackgroundSettings(BackgroundDestination.APP_DETAILS) },
        onThemeChange = { mode ->
            themeMode = mode
            getSharedPreferences("appearance", MODE_PRIVATE).edit().putString("theme", mode).apply()
        },
    )

    private fun openBackgroundSettings(destination: BackgroundDestination) {
        if (!BackgroundSettings.open(this, destination)) {
            notice = "无法打开系统设置，请手动进入设置 → 应用 → PG Proxy，检查通知与后台耗电权限。"
        }
    }

    private fun accountList(): List<JSONObject> = snapshot.optJSONArray("accounts")?.let { list ->
        (0 until list.length()).mapNotNull { list.optJSONObject(it) }.filter { it.optString("id").isNotBlank() }
    } ?: emptyList()
    private fun localAddresses(): List<String> = try {
        NetworkInterface.getNetworkInterfaces().toList().filter { it.isUp && !it.isLoopback }
            .flatMap { it.inetAddresses.toList() }.filterIsInstance<Inet4Address>()
            .filter { it.isSiteLocalAddress }.mapNotNull { it.hostAddress }.distinct()
    } catch (_: Exception) { emptyList() }
}
