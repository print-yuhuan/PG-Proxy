package dev.pgproxy.app.ui

import androidx.compose.animation.*
import androidx.compose.animation.core.tween
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.automirrored.outlined.Login
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

@Composable
fun AccountsPage(state: ProxyUiState, actions: UiActions, modifier: Modifier = Modifier) {
    val available = state.ready && !state.busy && state.connectionError.isBlank()
    val latestState by rememberUpdatedState(state)
    fun canManage() = latestState.ready && !latestState.busy && latestState.connectionError.isBlank()
    val addInteraction = remember { MutableInteractionSource() }
    var addOpen by rememberSaveable { mutableStateOf(false) }
    var accountName by rememberSaveable { mutableStateOf("") }
    var deleteId by rememberSaveable { mutableStateOf<String?>(null) }
    var discardId by rememberSaveable { mutableStateOf<String?>(null) }
    val deleteAccount = state.accounts.find { it.id == deleteId }
    val discardLogin = state.pendingLogins.find { it.id == discardId }
    val defaultAccountName = run {
        val labels = (state.accounts.map { it.label } + state.pendingLogins.map { it.label }).toSet()
        var number = state.accounts.size + state.pendingLogins.size + 1
        while ("凭证 $number" in labels) number++
        "凭证 $number"
    }

    LazyColumn(
        modifier.fillMaxSize().testTag("page_accounts"),
        contentPadding = PaddingValues(start = 20.dp, top = 12.dp, end = 20.dp, bottom = 20.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item(key = "accounts_header") {
            PageHeader("凭证", "共 ${state.accounts.size} 份 · 已启用 ${state.accounts.count { it.enabled }} 份") {
                FilledIconButton(onClick = { accountName = ""; addOpen = true }, enabled = available,
                    interactionSource = addInteraction,
                    modifier = Modifier.size(48.dp).pressFeedback(addInteraction, available).testTag("account_add")) {
                    Icon(Icons.Outlined.Add, "添加凭证")
                }
            }
        }
        if (!state.ready || state.connectionError.isNotBlank()) item(key = "accounts_loading") {
            PgCard(Modifier.animateItem(fadeInSpec = tween(PgMotion.Fast), placementSpec = tween(PgMotion.Medium, easing = PgMotion.Easing),
                fadeOutSpec = tween(PgMotion.Fast)), contentPadding = PaddingValues(12.dp), spacing = 6.dp) {
                Text(state.connectionError.ifBlank { "正在读取凭证…" },
                    color = if (state.connectionError.isNotBlank()) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant)
                if (state.connectionError.isNotBlank()) TextButton(onClick = actions.onRefresh, enabled = !state.refreshing,
                    modifier = Modifier.testTag("accounts_retry")) { Text(if (state.refreshing) "正在重连…" else "重新连接") }
            }
        }
        if (state.pendingLogins.isNotEmpty()) {
            item(key = "accounts_pending_heading") { SectionLabel("继续登录") }
            items(state.pendingLogins, key = { "pending_${it.id}" }) { pending ->
                PendingLoginCard(pending, available,
                    isCurrent = { canManage() && latestState.pendingLogins.any { it.id == pending.id } },
                    modifier = Modifier.animateItem(fadeInSpec = tween(PgMotion.Fast), placementSpec = tween(PgMotion.Medium, easing = PgMotion.Easing),
                        fadeOutSpec = tween(PgMotion.Fast)),
                    onContinue = { actions.onLogin(pending.id, pending.label) },
                    onDiscard = { discardId = pending.id })
            }
        }
        if (state.ready && state.accounts.isEmpty()) item(key = "accounts_empty") {
            val emptyAddInteraction = remember { MutableInteractionSource() }
            val canAdd = canManage() && latestState.accounts.isEmpty()
            PgCard(Modifier.animateItem(fadeInSpec = tween(PgMotion.Fast), placementSpec = tween(PgMotion.Medium, easing = PgMotion.Easing),
                fadeOutSpec = tween(PgMotion.Fast))) {
                Surface(shape = CircleShape, color = MaterialTheme.colorScheme.primaryContainer) {
                    Icon(Icons.Outlined.Key, null, Modifier.padding(12.dp).size(24.dp),
                        tint = MaterialTheme.colorScheme.primary)
                }
                Text("添加第一份凭证", style = MaterialTheme.typography.titleLarge)
                Text("登录 AI Studio，凭证会加密保存在本机。",
                    style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                FilledTonalButton(onClick = {
                    if (canManage() && latestState.accounts.isEmpty()) { accountName = ""; addOpen = true }
                }, enabled = canAdd, interactionSource = emptyAddInteraction,
                    modifier = Modifier.fillMaxWidth().pressFeedback(emptyAddInteraction, canAdd).testTag("account_add_empty")) {
                    Icon(Icons.Outlined.Add, null, Modifier.size(18.dp))
                    Spacer(Modifier.width(8.dp))
                    Text("添加凭证")
                }
            }
        }
        if (state.accounts.isNotEmpty()) item(key = "accounts_saved_heading") { SectionLabel("已保存") }
        items(state.accounts, key = { "account_${it.id}" }) { account ->
            AccountCard(account, available, actions,
                isCurrent = { canManage() && latestState.accounts.any { it.id == account.id && it.status != "deleting" } },
                modifier = Modifier.animateItem(fadeInSpec = tween(PgMotion.Fast), placementSpec = tween(PgMotion.Medium, easing = PgMotion.Easing),
                    fadeOutSpec = tween(PgMotion.Fast)), onDelete = { deleteId = account.id })
        }
        if (state.accounts.isNotEmpty()) item(key = "accounts_note") {
            Text("请求逐个执行，并按顺序轮询可用凭证；停用后仍保留。“检查凭证”会发送简短生成请求。",
                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }

    if (addOpen) AlertDialog(
        onDismissRequest = { addOpen = false },
        icon = { Icon(Icons.Outlined.Key, null) },
        title = { Text("添加凭证") },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Text("即将打开 AI Studio 登录，可先填写备注以便区分。")
                OutlinedTextField(value = accountName, onValueChange = { accountName = it },
                    enabled = available, label = { Text("凭证备注（选填）") }, singleLine = true,
                    supportingText = { Text("留空使用“$defaultAccountName” · 最多 80 个字符") },
                    isError = accountName.length > 80 || accountName.any { it.code < 32 },
                    modifier = Modifier.fillMaxWidth().testTag("account_add_name"))
            }
        },
        confirmButton = {
            TextButton(onClick = {
                val label = accountName.trim().ifEmpty { defaultAccountName }
                if (canManage() && accountName.length <= 80 && accountName.none { it.code < 32 }) {
                    addOpen = false
                    actions.onAddAccount(label)
                }
            }, enabled = available && accountName.length <= 80 && accountName.none { it.code < 32 },
                modifier = Modifier.testTag("account_add_confirm")) { Text("开始登录") }
        },
        dismissButton = { TextButton(onClick = { addOpen = false }) { Text("取消") } },
    )
    if (deleteAccount != null) AlertDialog(
        onDismissRequest = { deleteId = null },
        icon = { Icon(Icons.Outlined.DeleteOutline, null) },
        title = { Text("删除凭证？") },
        text = { Text("将删除“${deleteAccount.label}”及其本机登录资料。再次使用需要重新登录。",
            Modifier.verticalScroll(rememberScrollState())) },
        confirmButton = {
            TextButton(onClick = {
                if (canManage() && latestState.accounts.any { it.id == deleteAccount.id && it.status != "deleting" }) {
                    deleteId = null; actions.onDeleteAccount(deleteAccount.id)
                }
            }, enabled = available && deleteAccount.status != "deleting", colors = ButtonDefaults.textButtonColors(contentColor = MaterialTheme.colorScheme.error),
                modifier = Modifier.testTag("account_delete_confirm")) { Text("删除凭证") }
        },
        dismissButton = { TextButton(onClick = { deleteId = null }) { Text("取消") } },
    )
    if (discardLogin != null) AlertDialog(
        onDismissRequest = { discardId = null },
        icon = { Icon(Icons.Outlined.Close, null) },
        title = { Text("放弃这次登录？") },
        text = { Text("将清理“${discardLogin.label}”未完成的登录资料，其他凭证不受影响。",
            Modifier.verticalScroll(rememberScrollState())) },
        confirmButton = {
            TextButton(onClick = {
                if (canManage() && latestState.pendingLogins.any { it.id == discardLogin.id }) {
                    discardId = null; actions.onDiscardLogin(discardLogin.id)
                }
            }, enabled = available, modifier = Modifier.testTag("account_discard_confirm")) { Text("放弃登录") }
        },
        dismissButton = { TextButton(onClick = { discardId = null }) { Text("取消") } },
    )
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun PendingLoginCard(login: PendingLoginUi, available: Boolean, isCurrent: () -> Boolean,
                             modifier: Modifier = Modifier, onContinue: () -> Unit, onDiscard: () -> Unit) {
    val interactive = available && isCurrent()
    val continueInteraction = remember { MutableInteractionSource() }
    val discardInteraction = remember { MutableInteractionSource() }
    PgCard(modifier, contentPadding = PaddingValues(16.dp), spacing = 10.dp) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Icon(Icons.AutoMirrored.Outlined.Login, null, tint = MaterialTheme.colorScheme.primary)
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(3.dp)) {
                Text(login.label, style = MaterialTheme.typography.titleMedium)
                Text("登录尚未完成", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            FilledTonalButton(onClick = { if (isCurrent()) onContinue() }, enabled = interactive, interactionSource = continueInteraction,
                modifier = Modifier.pressFeedback(continueInteraction, interactive).testTag("account_continue_${login.id}")) {
                Text("继续登录")
            }
            TextButton(onClick = { if (isCurrent()) onDiscard() }, enabled = interactive, interactionSource = discardInteraction,
                modifier = Modifier.pressFeedback(discardInteraction, interactive).testTag("account_discard_${login.id}")) {
                Text("放弃登录")
            }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun AccountCard(account: AccountUi, available: Boolean, actions: UiActions, isCurrent: () -> Boolean,
                        modifier: Modifier = Modifier, onDelete: () -> Unit) {
    val editable = available && account.status != "deleting" && isCurrent()
    val status = when {
        account.status == "deleting" -> "正在移除"
        !account.enabled -> "已停用"
        account.active -> "正在使用"
        account.status == "verified" -> "已验证"
        account.status in setOf("needs_login", "login_required") -> "需要重新登录"
        account.status == "validation_error" -> "校验未通过"
        account.status == "cooldown" -> "冷却中"
        account.status == "error" -> "暂时不可用"
        else -> "等待检查"
    }
    val attention = account.enabled && account.status in setOf("needs_login", "login_required", "validation_error", "error")
    val checkedAt = remember(account.lastChecked) {
        if (account.lastChecked.isFinite() && account.lastChecked > 0 && account.lastChecked <= 253402300799.0)
            SimpleDateFormat("MM-dd HH:mm", Locale.getDefault()).format(Date((account.lastChecked * 1000).toLong()))
        else "尚未检查"
    }
    val modelCount: @Composable () -> Unit = {
        AnimatedContent(targetState = account.modelCount, transitionSpec = { accountTextTransition() }, label = "account-model-count") { count ->
            Text(if (count > 0) "$count 个可用模型" else "尚未读取模型",
                modifier = if (count == account.modelCount) Modifier else Modifier.clearAndSetSemantics { },
                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
    val credentialSwitch: @Composable () -> Unit = {
        Switch(checked = account.enabled, onCheckedChange = { if (isCurrent()) actions.onEnableAccount(account.id, it) }, enabled = editable,
            modifier = Modifier.testTag("account_toggle_${account.id}").semantics { contentDescription = "启用凭证 ${account.label}" })
    }
    PgCard(modifier, contentPadding = PaddingValues(16.dp), spacing = 10.dp) {
        if (LocalDensity.current.fontScale > 1.3f) {
            Text(account.label, Modifier.fillMaxWidth(), style = MaterialTheme.typography.titleMedium)
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                Box(Modifier.weight(1f)) { modelCount() }
                credentialSwitch()
            }
        } else {
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                Surface(shape = CircleShape, color = MaterialTheme.colorScheme.primaryContainer) {
                    Icon(Icons.Outlined.Key, null, Modifier.padding(10.dp).size(20.dp), tint = MaterialTheme.colorScheme.primary)
                }
                Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(3.dp)) {
                    Text(account.label, style = MaterialTheme.typography.titleMedium)
                    modelCount()
                }
                credentialSwitch()
            }
        }
        FlowRow(horizontalArrangement = Arrangement.spacedBy(10.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            AnimatedContent(targetState = status to attention, transitionSpec = { accountTextTransition() }, label = "account-status") { (text, alert) ->
                Surface(modifier = if (text == status && alert == attention) Modifier else Modifier.clearAndSetSemantics { }, shape = CircleShape,
                    color = if (alert) MaterialTheme.colorScheme.errorContainer else MaterialTheme.colorScheme.secondaryContainer) {
                    Text(text, Modifier.padding(horizontal = 11.dp, vertical = 5.dp), style = MaterialTheme.typography.labelMedium,
                        color = if (alert) MaterialTheme.colorScheme.onErrorContainer else MaterialTheme.colorScheme.onSecondaryContainer)
                }
            }
            AnimatedVisibility(visible = account.cooldownSeconds > 0, enter = accountHintEnter(), exit = accountHintExit()) {
                Text("约 ${account.cooldownSeconds} 秒后重试", Modifier.padding(vertical = 5.dp),
                    style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
        Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
            AnimatedContent(targetState = checkedAt, transitionSpec = { accountTextTransition() }, label = "account-checked-time") { time ->
                Text("最近检查 · $time", modifier = if (time == checkedAt) Modifier else Modifier.clearAndSetSemantics { },
                    style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            AccountHint(if (account.lastModel.isNotBlank()) "最近使用 · ${account.lastModel}" else "", MaterialTheme.colorScheme.onSurfaceVariant)
            AccountHint(if (account.lastError.isNotBlank()) accountErrorMessage(account.lastError) else "",
                if (attention) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant)
        }
        HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
        FlowRow(horizontalArrangement = Arrangement.spacedBy(4.dp), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            AccountAction("检查凭证", Icons.Outlined.VerifiedUser, "account_check_${account.id}", editable) { if (isCurrent()) actions.onCheckAccount(account.id) }
            AccountAction("重新登录", Icons.AutoMirrored.Outlined.Login, "account_relogin_${account.id}", editable) { if (isCurrent()) actions.onLogin(account.id, account.label) }
            AccountAction("删除", Icons.Outlined.DeleteOutline, "account_delete_${account.id}", editable, destructive = true) { if (isCurrent()) onDelete() }
        }
    }
}

@Composable
private fun AccountAction(label: String, icon: ImageVector, tag: String, enabled: Boolean,
                          destructive: Boolean = false, onClick: () -> Unit) {
    val interaction = remember { MutableInteractionSource() }
    TextButton(onClick = onClick, enabled = enabled, interactionSource = interaction,
        modifier = Modifier.pressFeedback(interaction, enabled).testTag(tag),
        contentPadding = PaddingValues(horizontal = 10.dp, vertical = 8.dp),
        colors = ButtonDefaults.textButtonColors(contentColor = if (destructive) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.primary)) {
        Icon(icon, null, Modifier.size(17.dp))
        Spacer(Modifier.width(6.dp))
        Text(label, fontWeight = FontWeight.Medium)
    }
}

private fun <S> AnimatedContentTransitionScope<S>.accountTextTransition(): ContentTransform =
    (fadeIn(tween(PgMotion.Fast)) togetherWith fadeOut(tween(PgMotion.Fast)))
        .using(SizeTransform(clip = false) { _, _ -> tween(PgMotion.Medium, easing = PgMotion.Easing) })

private fun accountHintEnter() = fadeIn(tween(PgMotion.Fast)) +
    expandVertically(tween(PgMotion.Medium, easing = PgMotion.Easing), expandFrom = Alignment.Top)

private fun accountHintExit() = fadeOut(tween(PgMotion.Fast)) +
    shrinkVertically(tween(PgMotion.Medium, easing = PgMotion.Easing), shrinkTowards = Alignment.Top)

@Composable
private fun AccountHint(text: String, color: Color) {
    // Keep a non-sensitive message stable while its exit transition finishes.
    var retained by remember { mutableStateOf(text) }
    LaunchedEffect(text) { if (text.isNotBlank()) retained = text }
    AnimatedVisibility(visible = text.isNotBlank(), enter = accountHintEnter(), exit = accountHintExit()) {
        AnimatedContent(targetState = text.ifBlank { retained }, transitionSpec = { accountTextTransition() }, label = "account-hint") { message ->
            Text(message, modifier = if (text.isNotBlank() && message == text) Modifier else Modifier.clearAndSetSemantics { },
                style = MaterialTheme.typography.bodySmall, color = color)
        }
    }
}

private fun accountErrorMessage(code: String): String = when (code) {
    "session_required", "session_invalid", "upstream_authentication_error" -> "登录凭证已失效，请重新登录。"
    "upstream_permission_denied" -> "当前凭证暂时无法访问，请检查 AI Studio 使用权限。"
    "waa_execution_error", "waa_unavailable", "waa_protocol_error" -> "校验未完成，请稍后再次检查。"
    "rate_limit" -> "请求暂时受限，请等待冷却后重试。"
    "upstream_timeout" -> "请求超时，请检查网络或调整等待时间。"
    "upstream_connection_error" -> "无法连接上游，请检查网络后重试。"
    "generation_unverified" -> "未收到完整生成内容，请重新检查凭证。"
    "page_initialization_failed" -> "页面初始化失败，请确认登录和网站状态。"
    "protocol_error", "stream_interrupted" -> "响应未完整处理，请稍后重试。"
    "account_busy" -> "当前会话忙碌，请稍后重试。"
    "account_deleted", "account_not_found" -> "凭证已移除，请刷新列表。"
    "account_deletion_failed" -> "登录资料清理未完成，请重试删除。"
    else -> "上次请求未完成，可重新检查凭证。"
}
