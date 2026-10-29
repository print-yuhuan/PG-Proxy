package dev.pgproxy.app.ui

import androidx.compose.animation.*
import androidx.compose.animation.core.*
import androidx.compose.foundation.background
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.automirrored.outlined.Article
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.saveable.rememberSaveableStateHolder
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.luminance
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.input.pointer.PointerEventPass
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.unit.IntOffset

@Composable
fun PgProxyScreen(state: ProxyUiState, actions: UiActions = UiActions(), overlay: @Composable () -> Unit = {}) {
    PgTheme(state.themeMode) {
        var tab by rememberSaveable { mutableIntStateOf(0) }
        val savedPages = rememberSaveableStateHolder()
        // Keep unsaved settings across tabs in memory; never place the API key in saved-state Bundles.
        val settingsDraft = remember { SettingsDraft() }
        val snackbar = remember { SnackbarHostState() }
        val focus = LocalFocusManager.current
        val keyboard = LocalSoftwareKeyboardController.current
        val selectTab: (Int) -> Unit = { next ->
            if (next != tab) { focus.clearFocus(force = true); keyboard?.hide(); tab = next }
        }
        LaunchedEffect(state.notice, state.noticeSequence) { if (state.notice.isNotBlank()) snackbar.showSnackbar(state.notice) }
        Scaffold(containerColor = MaterialTheme.colorScheme.background,
            snackbarHost = { SnackbarHost(snackbar, Modifier.padding(horizontal = 20.dp)) },
            bottomBar = { BottomNavigation(tab, selectTab) }) { padding ->
            Box(Modifier.fillMaxSize().padding(padding).clipToBounds(), contentAlignment = Alignment.TopCenter) {
                val distance = with(LocalDensity.current) { 16.dp.roundToPx() }
                AnimatedContent(tab, modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.TopCenter,
                    transitionSpec = {
                        val direction = if (targetState > initialState) 1 else -1
                        (fadeIn(tween(150, easing = LinearOutSlowInEasing)) + slideInHorizontally(tween(PgMotion.Medium, easing = PgMotion.Easing)) { direction * distance })
                            .togetherWith(fadeOut(tween(90, easing = FastOutLinearInEasing)) + slideOutHorizontally(tween(PgMotion.Medium, easing = PgMotion.Easing)) { -direction * distance })
                            .using(null)
                    }, label = "page-transition") { page ->
                    savedPages.SaveableStateProvider(page) {
                        // Keep outgoing pages visible for the transition, but remove their input and accessibility targets.
                        val inactive = if (page == tab) Modifier else Modifier.clearAndSetSemantics { }.pointerInput(Unit) {
                            awaitPointerEventScope { while (true) awaitPointerEvent(PointerEventPass.Initial).changes.forEach { it.consume() } }
                        }
                        Box(Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background).then(inactive), contentAlignment = Alignment.TopCenter) {
                            val modifier = Modifier.widthIn(max = 760.dp).fillMaxSize()
                            when (page) {
                                0 -> OverviewPage(state, actions, modifier)
                                1 -> AccountsPage(state, actions, modifier)
                                2 -> LogsPage(state, actions, modifier)
                                3 -> SettingsPage(state, actions, modifier, draft = settingsDraft)
                            }
                        }
                    }
                }
                AnimatedVisibility(state.busy, enter = fadeIn(tween(PgMotion.Fast)), exit = fadeOut(tween(PgMotion.Fast))) {
                    LinearProgressIndicator(Modifier.fillMaxWidth().height(2.dp))
                }
            }
        }
        overlay()
    }
}

@Composable
private fun BottomNavigation(selected: Int, select: (Int) -> Unit) {
    val titles = listOf("概览", "凭证", "日志", "设置")
    val tags = listOf("overview", "accounts", "logs", "settings")
    val icons = listOf(Icons.Outlined.SpaceDashboard, Icons.Outlined.VpnKey, Icons.AutoMirrored.Outlined.Article, Icons.Outlined.Tune)
    Box(Modifier.fillMaxWidth().navigationBarsPadding().padding(horizontal = 20.dp, vertical = 10.dp), contentAlignment = Alignment.Center) {
        Surface(Modifier.widthIn(max = 520.dp).fillMaxWidth(), shape = RoundedCornerShape(32.dp),
            color = MaterialTheme.colorScheme.surface, shadowElevation = 1.dp) {
            BoxWithConstraints(Modifier.padding(6.dp)) {
                val cellWidth = maxWidth / titles.size
                val position by animateDpAsState(cellWidth * selected, spring(dampingRatio = .9f, stiffness = 900f), label = "navigation-position")
                var rowHeight by remember { mutableIntStateOf(0) }
                val density = LocalDensity.current
                Box(Modifier.offset { IntOffset(position.roundToPx(), 0) }.width(cellWidth)
                    .height(with(density) { rowHeight.toDp() }).clip(RoundedCornerShape(26.dp))
                    .background(MaterialTheme.colorScheme.primaryContainer).testTag("nav_indicator"))
                Row(Modifier.fillMaxWidth().onSizeChanged { rowHeight = it.height }, verticalAlignment = Alignment.CenterVertically) {
                titles.forEachIndexed { index, title ->
                    val chosen = selected == index
                    val interaction = remember { MutableInteractionSource() }
                    Column(Modifier.weight(1f).pressFeedback(interaction).clip(RoundedCornerShape(26.dp))
                        .selectable(chosen, interactionSource = interaction, indication = null, onClick = { select(index) }, role = Role.Tab)
                        .testTag("nav_${tags[index]}").heightIn(min = 62.dp).padding(vertical = 9.dp),
                        horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(3.dp, Alignment.CenterVertically)) {
                        val color by animateColorAsState(if (chosen) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant,
                            tween(PgMotion.Fast), label = "navigation-tint")
                        Icon(icons[index], null, Modifier.size(22.dp), tint = color)
                        Text(title, style = MaterialTheme.typography.labelMedium, color = color, fontWeight = if (chosen) FontWeight.SemiBold else FontWeight.Normal)
                    }
                }
                }
            }
        }
    }
}

@Composable
private fun OverviewPage(state: ProxyUiState, actions: UiActions, modifier: Modifier) {
    val colors = MaterialTheme.colorScheme
    val runningText = if (colors.surface.luminance() < .5f) Color(0xFF65C7A6) else Color(0xFF187B5D)
    val statusColor by animateColorAsState(if (!state.ready && state.connectionError.isNotBlank()) colors.error else if (state.ready && state.running) runningText else colors.onSurfaceVariant,
        tween(PgMotion.Medium), label = "service-status-text")
    val refreshRotation = remember { Animatable(0f) }
    var refreshTurns by remember { mutableIntStateOf(0) }
    LaunchedEffect(refreshTurns) {
        if (refreshTurns > 0) refreshRotation.animateTo(refreshTurns * 360f, tween(400, easing = PgMotion.Easing))
    }
    val canRefresh = !state.busy && !state.refreshing
    val powerInteraction = remember { MutableInteractionSource() }
    val refreshInteraction = remember { MutableInteractionSource() }
    LazyColumn(modifier.testTag("page_overview"), contentPadding = PaddingValues(start = 20.dp, end = 20.dp, top = 4.dp, bottom = 12.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp)) {
        item { Row(Modifier.fillMaxWidth().heightIn(min = 48.dp), verticalAlignment = Alignment.CenterVertically) {
            Text("PG Proxy", Modifier.weight(1f), style = MaterialTheme.typography.headlineSmall.copy(fontSize = 26.sp))
            IconButton({
                actions.onRefresh()
                refreshTurns++
            }, enabled = canRefresh, interactionSource = refreshInteraction,
                modifier = Modifier.size(48.dp).pressFeedback(refreshInteraction, canRefresh).clip(CircleShape).background(colors.surface).testTag("overview_refresh")) {
                Icon(Icons.Outlined.Refresh, "刷新状态", Modifier.rotate(refreshRotation.value), tint = colors.onSurfaceVariant)
            }
        } }
        item { PgCard(contentPadding = PaddingValues(16.dp), spacing = 12.dp) {
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    AnimatedLabel(if (!state.ready && state.connectionError.isNotBlank()) "连接暂时中断" else if (!state.ready) "连接服务中" else if (state.running) "服务已启动" else "准备就绪", style = MaterialTheme.typography.titleLarge, color = colors.onSurface)
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        Box(Modifier.size(6.dp).background(statusColor, CircleShape))
                        AnimatedLabel(if (!state.ready && state.connectionError.isNotBlank()) "等待重连" else if (!state.ready) "正在连接" else if (state.running) "运行中" else "已停止", style = MaterialTheme.typography.bodySmall, color = statusColor)
                        if (state.ready) Text(if (state.running) "· ${state.port} · ${if (state.lan) "局域网" else "仅本机"}" else "· 轻触按钮启动",
                            Modifier.weight(1f), style = MaterialTheme.typography.bodySmall, color = colors.onSurfaceVariant)
                    }
                }
                Spacer(Modifier.width(12.dp))
                FilledIconButton(onClick = actions.onToggleService, enabled = state.ready && !state.busy,
                    interactionSource = powerInteraction,
                    modifier = Modifier.pressFeedback(powerInteraction, state.ready && !state.busy).size(64.dp).testTag("service_toggle"),
                    colors = IconButtonDefaults.filledIconButtonColors(containerColor = colors.primary, contentColor = colors.onPrimary)) {
                    AnimatedContent(state.running, transitionSpec = {
                        (fadeIn(tween(PgMotion.Fast)) + scaleIn(tween(PgMotion.Medium), initialScale = .8f))
                            .togetherWith(fadeOut(tween(100)) + scaleOut(tween(PgMotion.Fast), targetScale = .8f))
                    }, label = "power-icon") { running ->
                        Icon(if (running) Icons.Outlined.Pause else Icons.Outlined.PowerSettingsNew,
                            if (running) "停止服务" else "启动服务", Modifier.size(32.dp).then(if (running == state.running) Modifier else Modifier.clearAndSetSemantics { }))
                    }
                }
            }
            if (!state.ready && state.connectionError.isNotBlank()) Text(state.connectionError,
                Modifier.testTag("connection_error"), style = MaterialTheme.typography.bodySmall, color = colors.error)
            HorizontalDivider(color = colors.outlineVariant.copy(alpha = .6f))
            OverviewMetrics(state, runningText)
        } }
        item { PgCard(contentPadding = PaddingValues(horizontal = 16.dp, vertical = 12.dp), spacing = 6.dp) {
            AddressRow("连接地址 · 本机", "http://127.0.0.1:${state.port}/v1", actions.onCopy)
            if (state.lan) {
                state.addresses.forEach { address ->
                    HorizontalDivider(color = colors.outlineVariant.copy(alpha = .6f))
                    AddressRow("局域网访问", "http://$address:${state.port}/v1", actions.onCopy)
                }
                if (state.addresses.isEmpty()) Text("连接 Wi-Fi 后，局域网地址会显示在这里。", style = MaterialTheme.typography.bodySmall, color = colors.onSurfaceVariant)
            }
            Text("支持 OpenAI 与原生 Gemini 接口", style = MaterialTheme.typography.bodySmall, color = colors.onSurfaceVariant)
        } }
        item { NetworkInfoCard(state.networkInfo, actions.onRefreshNetwork, actions.onCopy) }
        item { Text("PG PROXY  /  ${state.version.ifBlank { "PLAYGROUND" }}", Modifier.fillMaxWidth().padding(top = 2.dp),
            style = MaterialTheme.typography.labelSmall, color = colors.onSurfaceVariant, letterSpacing = 1.sp) }
    }
}

@Composable
private fun MiniMetric(label: String, value: String, modifier: Modifier, color: Color = MaterialTheme.colorScheme.onSurface) {
    Column(modifier, verticalArrangement = Arrangement.spacedBy(3.dp)) {
        AnimatedLabel(value, style = MaterialTheme.typography.titleLarge.copy(fontSize = 23.sp), color = color)
        Text(label, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
private fun OverviewMetrics(state: ProxyUiState, successColor: Color) {
    val largeText = LocalDensity.current.fontScale > 1.3f
    BoxWithConstraints(Modifier.fillMaxWidth()) {
        val pairs = listOf("启用凭证" to state.accounts.count { it.enabled }.toString(),
            "有效凭证" to state.accounts.count { it.status == "verified" }.toString(),
            "请求成功" to state.requestsSucceeded.toString(), "请求失败" to state.requestsFailed.toString())
        val columns = if (largeText || maxWidth < 300.dp) 2 else 4
        Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
            pairs.chunked(columns).forEachIndexed { row, values ->
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    values.forEachIndexed { column, (label, value) ->
                        val index = row * columns + column
                        MiniMetric(label, value, Modifier.weight(1f).then(when (index) {
                            2 -> Modifier.testTag("requests_succeeded")
                            3 -> Modifier.testTag("requests_failed")
                            else -> Modifier
                        }), when (index) {
                            2 -> successColor
                            3 -> MaterialTheme.colorScheme.error
                            else -> MaterialTheme.colorScheme.onSurface
                        })
                    }
                }
            }
        }
    }
}

@Composable
private fun AddressRow(label: String, value: String, copy: (String) -> Unit) {
    val interaction = remember { MutableInteractionSource() }
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(5.dp)) {
            Text(label, style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Text(value, style = MaterialTheme.typography.bodyLarge)
        }
        IconButton({ copy(value) }, Modifier.padding(start = 4.dp).pressFeedback(interaction), interactionSource = interaction) { Icon(Icons.Outlined.ContentCopy, "复制$label 地址", Modifier.size(18.dp), tint = MaterialTheme.colorScheme.primary) }
    }
}
