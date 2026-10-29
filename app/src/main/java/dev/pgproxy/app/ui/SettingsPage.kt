package dev.pgproxy.app.ui

import dev.pgproxy.app.ProjectLinks

import androidx.compose.animation.*
import androidx.compose.animation.core.tween
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.automirrored.outlined.Article
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.foundation.LocalIndication
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp

/** Lives only in the screen's memory; in particular, the key never enters a saved-state Bundle. */
class SettingsDraft {
    var port by mutableStateOf("8888")
    var lan by mutableStateOf(false)
    var key by mutableStateOf("")
    var timeout by mutableStateOf("180")
    var queueTimeout by mutableStateOf("30")
    var keepAwake by mutableStateOf(false)
    private var baseline by mutableStateOf<ServiceSettings?>(null)

    val dirty: Boolean
        get() = baseline?.let { saved ->
            port != saved.port.toString() || lan != saved.lan || key != saved.apiKey ||
                timeout != saved.timeout.toString() || queueTimeout != saved.queueTimeout.toString() ||
                keepAwake != saved.keepAwake
        } ?: false

    internal fun reset(settings: ServiceSettings) {
        port = settings.port.toString()
        lan = settings.lan
        key = settings.apiKey
        timeout = settings.timeout.toString()
        queueTimeout = settings.queueTimeout.toString()
        keepAwake = settings.keepAwake
        baseline = settings
    }

    internal fun reconcile(settings: ServiceSettings) {
        val saved = baseline
        if (saved == null) { reset(settings); return }
        // A new snapshot can update untouched fields, while an in-progress edit
        // remains intact even if this page left composition between snapshots.
        if (port == saved.port.toString()) port = settings.port.toString()
        if (lan == saved.lan) lan = settings.lan
        if (key == saved.apiKey) key = settings.apiKey
        if (timeout == saved.timeout.toString()) timeout = settings.timeout.toString()
        if (queueTimeout == saved.queueTimeout.toString()) queueTimeout = settings.queueTimeout.toString()
        if (keepAwake == saved.keepAwake) keepAwake = settings.keepAwake
        baseline = settings
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun SettingsPage(state: ProxyUiState, actions: UiActions, modifier: Modifier = Modifier,
                 draft: SettingsDraft = remember { SettingsDraft() }) {
    val available = state.ready && !state.busy && state.connectionError.isBlank()
    val chipHeight = maxOf(36f, 32f * LocalDensity.current.fontScale).dp
    val incoming = ServiceSettings(state.port, state.lan, state.apiKey, state.timeout, state.queueTimeout, state.keepAwake)
    val port = draft.port
    val lan = draft.lan
    val key = draft.key
    val timeout = draft.timeout
    val queueTimeout = draft.queueTimeout
    val keepAwake = draft.keepAwake
    var showKey by remember { mutableStateOf(false) }
    var cleanupOpen by remember { mutableStateOf(false) }
    val dirty = draft.dirty
    LaunchedEffect(incoming, state.ready, draft) {
        if (state.ready) draft.reconcile(incoming)
    }
    val portValue = port.trim().toIntOrNull()?.takeIf { it in 1024..65535 }
    val timeoutValue = timeout.trim().toIntOrNull()?.takeIf { it in 1..600 }
    val queueValue = queueTimeout.trim().toIntOrNull()?.takeIf { it in 1..600 }
    val keyValid = key.length in 3..256 && key.all { it.code in 33..126 }
    val valid = portValue != null && timeoutValue != null && queueValue != null && keyValid
    val saveInteraction = remember { MutableInteractionSource() }
    val resetInteraction = remember { MutableInteractionSource() }
    val copyInteraction = remember { MutableInteractionSource() }
    val canReset by rememberUpdatedState(available && dirty)

    LazyColumn(
        modifier.fillMaxSize().testTag("page_settings"),
        contentPadding = PaddingValues(start = 20.dp, top = 12.dp, end = 20.dp, bottom = 20.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item { PageHeader("设置", "连接、外观与应用信息") }
        if (!state.ready || state.connectionError.isNotBlank()) item {
            PgCard(contentPadding = PaddingValues(12.dp), spacing = 6.dp) {
                Text(state.connectionError.ifBlank { "正在读取设置…" },
                    color = if (state.connectionError.isNotBlank()) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant)
                if (state.connectionError.isNotBlank()) TextButton(onClick = actions.onRefresh, enabled = !state.refreshing,
                    modifier = Modifier.testTag("settings_retry")) { Text(if (state.refreshing) "正在重连…" else "重新连接") }
            }
        }
        item { SectionLabel("连接") }
        item {
            PgCard(contentPadding = PaddingValues(16.dp), spacing = 12.dp) {
                SettingsCardTitle(Icons.Outlined.Dns, "本地服务")
                OutlinedTextField(value = port, onValueChange = { draft.port = it }, enabled = available,
                    label = { Text("监听端口") }, singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                    isError = state.ready && portValue == null,
                    supportingText = { SettingsFieldHint(portValue == null && state.ready, "请输入 1024–65535 之间的整数", "客户端需使用相同的端口") },
                    modifier = Modifier.fillMaxWidth().testTag("settings_port"))
                Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                        Text("局域网访问", style = MaterialTheme.typography.titleMedium)
                        Text("同一网络可访问，仍需接口密钥", style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    Switch(checked = lan, onCheckedChange = { draft.lan = it }, enabled = available,
                        modifier = Modifier.testTag("settings_lan").semantics { contentDescription = "局域网访问" })
                }
            }
        }
        item {
            PgCard(contentPadding = PaddingValues(16.dp), spacing = 12.dp) {
                SettingsCardTitle(Icons.Outlined.Key, "接口密钥")
                OutlinedTextField(value = key, onValueChange = { draft.key = it }, enabled = available,
                    label = { Text("API Key") }, singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
                    visualTransformation = if (showKey) VisualTransformation.None else PasswordVisualTransformation(),
                    isError = state.ready && !keyValid,
                    supportingText = {
                        SettingsFieldHint(!keyValid && state.ready, "使用 3–256 个可见 ASCII 字符，不含空格", "修改后需同步更新客户端")
                    },
                    trailingIcon = {
                        IconButton(onClick = { showKey = !showKey }, enabled = available,
                            modifier = Modifier.testTag("settings_key_visibility")) {
                            Icon(if (showKey) Icons.Outlined.VisibilityOff else Icons.Outlined.Visibility,
                                if (showKey) "隐藏密钥" else "显示密钥")
                        }
                    }, modifier = Modifier.fillMaxWidth().testTag("settings_key"))
                TextButton(onClick = { actions.onCopy(key) }, enabled = available && keyValid,
                    interactionSource = copyInteraction,
                    modifier = Modifier.pressFeedback(copyInteraction, available && keyValid).testTag("settings_key_copy")) {
                    Icon(Icons.Outlined.ContentCopy, null, Modifier.size(18.dp))
                    Spacer(Modifier.width(8.dp))
                    Text("复制密钥")
                }
            }
        }
        item { SectionLabel("请求") }
        item {
            PgCard(contentPadding = PaddingValues(16.dp), spacing = 12.dp) {
                SettingsCardTitle(Icons.Outlined.Timer, "等待时间")
                OutlinedTextField(value = timeout, onValueChange = { draft.timeout = it }, enabled = available,
                    label = { Text("请求超时（秒）") }, singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                    isError = state.ready && timeoutValue == null,
                    supportingText = { SettingsFieldHint(timeoutValue == null && state.ready, "请输入 1–600 之间的整数", "每次上游读写的等待上限，不含排队时间") },
                    modifier = Modifier.fillMaxWidth().testTag("settings_timeout"))
                OutlinedTextField(value = queueTimeout, onValueChange = { draft.queueTimeout = it }, enabled = available,
                    label = { Text("排队超时（秒）") }, singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                    isError = state.ready && queueValue == null,
                    supportingText = { SettingsFieldHint(queueValue == null && state.ready, "请输入 1–600 之间的整数", "请求逐个执行；空闲即开始，排队超时则返回错误") },
                    modifier = Modifier.fillMaxWidth().testTag("settings_queue_timeout"))
            }
        }
        item { SectionLabel("后台运行") }
        item {
            PgCard(contentPadding = PaddingValues(16.dp), spacing = 6.dp) {
                SettingsAction(Icons.Outlined.NotificationsActive, "运行通知", when (state.background.notificationsAllowed) {
                    true -> "已允许，服务运行时显示通知"
                    false -> "未允许，点击前往通知设置"
                    null -> "状态未获取，点击检查通知设置"
                }, "settings_background_notifications", true, actions.onOpenNotificationSettings)
                HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
                SettingsAction(Icons.Outlined.BatteryChargingFull, "电池优化", when (state.background.batteryExempt) {
                    true -> "已忽略 Android 电池优化"
                    false -> "仍受 Android 电池优化限制"
                    null -> "状态未获取，点击检查电池设置"
                }, "settings_background_battery", true, actions.onOpenBatterySettings)
                HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
                SettingsAction(Icons.Outlined.SettingsApplications, "后台耗电管理", when (state.background.backgroundRestricted) {
                    true -> "系统已限制后台活动，请手动调整"
                    false -> "进入应用详情设置后台运行"
                    null -> "进入应用详情检查后台权限"
                }, "settings_background_app", true, actions.onOpenAppSettings)
            }
        }
        item {
            Text("在系统页面允许 PG Proxy 后台耗电，并按需设置自启动或锁定最近任务。厂商开关无法自动读取，请以系统页面为准。",
                Modifier.testTag("settings_background_guidance"), style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        item {
            PgCard(contentPadding = PaddingValues(16.dp), spacing = 10.dp) {
                Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    Text("保持 CPU 唤醒", Modifier.weight(1f), style = MaterialTheme.typography.titleMedium)
                    Switch(checked = keepAwake, onCheckedChange = { draft.keepAwake = it }, enabled = available,
                        modifier = Modifier.testTag("settings_keep_awake").semantics { contentDescription = "保持 CPU 唤醒" })
                }
                Text("锁屏后持续提供接口时按需开启；仅在服务运行时生效，会增加耗电。修改后需保存。",
                    style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                Text(when {
                    !state.ready || state.connectionError.isNotBlank() -> "运行状态待同步"
                    state.wakeLockError.isNotBlank() -> state.wakeLockError
                    state.wakeLockHeld -> "当前正在保持 CPU 唤醒"
                    state.keepAwake && !state.running -> "已开启，启动服务后生效"
                    state.keepAwake -> "已开启，等待唤醒状态更新"
                    else -> "当前未保持 CPU 唤醒"
                }, Modifier.testTag("settings_wake_lock_status"), style = MaterialTheme.typography.bodySmall,
                    color = if (state.ready && state.wakeLockError.isNotBlank()) MaterialTheme.colorScheme.error
                    else MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
        item {
            Text("以上设置可降低后台中断概率，不能阻止系统强制停止应用。",
                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        item {
            PgCard(contentPadding = PaddingValues(16.dp), spacing = 10.dp) {
                Text(if (dirty) "有修改尚未保存" else if (state.ready && state.connectionError.isBlank()) "设置已同步" else "当前设置",
                    style = MaterialTheme.typography.titleMedium,
                    color = if (dirty) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurface)
                if (dirty) Text(if (state.running) "连接参数变更时，服务将按新配置重新连接。" else "保存后生效。",
                    style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                AnimatedVisibility(visible = dirty && !valid, enter = settingsHintEnter(), exit = settingsHintExit()) {
                    Text("请先修正上方标出的内容。", style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.error)
                }
                Button(onClick = {
                    if (available && dirty && valid) {
                        val settings = ServiceSettings(portValue, lan, key, timeoutValue, queueValue, keepAwake)
                        // Normalize numeric presentation without claiming that the save succeeded.
                        draft.port = settings.port.toString()
                        draft.timeout = settings.timeout.toString()
                        draft.queueTimeout = settings.queueTimeout.toString()
                        actions.onConfigure(settings)
                    }
                }, enabled = available && dirty && valid, interactionSource = saveInteraction,
                    modifier = Modifier.fillMaxWidth().pressFeedback(saveInteraction, available && dirty && valid).testTag("settings_save")) {
                    Icon(Icons.Outlined.Check, null, Modifier.size(18.dp))
                    Spacer(Modifier.width(8.dp))
                    Text(if (!state.ready || state.connectionError.isNotBlank()) "等待服务连接" else if (state.busy) "正在处理…" else "保存设置")
                }
                AnimatedVisibility(visible = dirty, enter = settingsHintEnter(), exit = settingsHintExit()) {
                    TextButton(onClick = { if (canReset) draft.reset(incoming) }, enabled = canReset,
                        interactionSource = resetInteraction,
                        modifier = Modifier.fillMaxWidth().pressFeedback(resetInteraction, canReset).testTag("settings_reset")) { Text("还原修改") }
                }
            }
        }
        item { SectionLabel("外观") }
        item {
            PgCard(contentPadding = PaddingValues(16.dp), spacing = 10.dp) {
                SettingsCardTitle(Icons.Outlined.Palette, "主题")
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    listOf("system" to "跟随系统", "light" to "浅色", "dark" to "深色").forEach { (mode, label) ->
                        FilterChip(selected = state.themeMode == mode, onClick = { actions.onThemeChange(mode) },
                            label = { Text(label) }, modifier = Modifier.heightIn(min = chipHeight).testTag("settings_theme_$mode"))
                    }
                }
            }
        }
        item { SectionLabel("项目与交流") }
        item {
            PgCard(contentPadding = PaddingValues(16.dp), spacing = 6.dp) {
                SettingsAction(Icons.Outlined.Code, "项目仓库", ProjectLinks.REPOSITORY.removePrefix("https://"),
                    "settings_repository", true, actions.onOpenRepository)
                HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
                SettingsAction(Icons.Outlined.BugReport, "问题反馈", "提交问题与查看处理进度",
                    "settings_issues", true, actions.onOpenIssues)
                HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
                SettingsAction(Icons.Outlined.Groups, "QQ一群", ProjectLinks.QQ_GROUP_ONE, "settings_qq_group_1", true) {
                    actions.onOpenQqGroup(ProjectLinks.QQ_GROUP_ONE)
                }
                HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
                SettingsAction(Icons.Outlined.Groups, "QQ二群", ProjectLinks.QQ_GROUP_TWO, "settings_qq_group_2", true) {
                    actions.onOpenQqGroup(ProjectLinks.QQ_GROUP_TWO)
                }
                HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
                SettingsAction(Icons.Outlined.Forum, "GMS吹水群", ProjectLinks.QQ_GROUP_GMS, "settings_qq_group_gms", true) {
                    actions.onOpenQqGroup(ProjectLinks.QQ_GROUP_GMS)
                }
            }
        }
        item { SectionLabel("关于与维护") }
        item {
            PgCard(contentPadding = PaddingValues(16.dp), spacing = 6.dp) {
                SettingsAction(Icons.Outlined.Troubleshoot, "运行诊断", "检查运行环境与组件状态", "settings_diagnostics", available, actions.onDiagnostics)
                HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
                SettingsAction(Icons.AutoMirrored.Outlined.Article, "开源许可", "查看项目与组件的许可声明", "settings_licenses", true, actions.onOpenLicenses)
                HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
                SettingsAction(Icons.Outlined.CleaningServices, "清理登录资料", "重试清理已删除凭证的本机资料", "settings_cleanup", available) { cleanupOpen = true }
            }
        }
        if (state.version.isNotBlank()) item {
            Text("PG Proxy · ${state.version}", Modifier.fillMaxWidth(), style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
    if (cleanupOpen) AlertDialog(
        onDismissRequest = { cleanupOpen = false },
        icon = { Icon(Icons.Outlined.CleaningServices, null) },
        title = { Text("清理登录资料？") },
        text = { Text("重试清理已删除凭证留下的登录资料，不影响当前凭证。") },
        confirmButton = {
            TextButton(onClick = { if (available) { cleanupOpen = false; actions.onCleanup() } }, enabled = available,
                modifier = Modifier.testTag("settings_cleanup_confirm")) { Text("开始清理") }
        },
        dismissButton = { TextButton(onClick = { cleanupOpen = false }) { Text("取消") } },
    )
}

@Composable
private fun SettingsCardTitle(icon: ImageVector, title: String) {
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
        Icon(icon, null, tint = MaterialTheme.colorScheme.primary, modifier = Modifier.size(22.dp))
        Text(title, style = MaterialTheme.typography.titleMedium)
    }
}

@Composable
private fun SettingsAction(icon: ImageVector, title: String, subtitle: String, tag: String, enabled: Boolean, onClick: () -> Unit) {
    val interaction = remember { MutableInteractionSource() }
    Row(Modifier.fillMaxWidth().testTag(tag).pressFeedback(interaction, enabled).clip(RoundedCornerShape(12.dp)).heightIn(min = 56.dp)
        .clickable(interactionSource = interaction, indication = LocalIndication.current, enabled = enabled, onClick = onClick).padding(vertical = 5.dp),
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        Icon(icon, null, tint = if (enabled) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurface.copy(alpha = .38f),
            modifier = Modifier.size(24.dp))
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(3.dp)) {
            Text(title, style = MaterialTheme.typography.titleMedium,
                color = MaterialTheme.colorScheme.onSurface.copy(alpha = if (enabled) 1f else .38f))
            Text(subtitle, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Icon(Icons.Outlined.ChevronRight, null, tint = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.size(20.dp))
    }
}

private fun settingsHintEnter() = fadeIn(tween(PgMotion.Fast)) +
    expandVertically(tween(PgMotion.Medium, easing = PgMotion.Easing), expandFrom = Alignment.Top)

private fun settingsHintExit() = fadeOut(tween(PgMotion.Fast)) +
    shrinkVertically(tween(PgMotion.Medium, easing = PgMotion.Easing), shrinkTowards = Alignment.Top)

@Composable
private fun SettingsFieldHint(error: Boolean, errorMessage: String, helpMessage: String) {
    // Validation must follow typing immediately, without animating obsolete errors.
    Text(if (error) errorMessage else helpMessage)
}
