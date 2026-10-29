package dev.pgproxy.app.ui

import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.SizeTransform
import androidx.compose.animation.core.tween
import androidx.compose.animation.expandVertically
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.shrinkVertically
import androidx.compose.animation.togetherWith
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material.icons.outlined.ContentCopy
import androidx.compose.material.icons.outlined.ErrorOutline
import androidx.compose.material.icons.outlined.History
import androidx.compose.material.icons.outlined.Info
import androidx.compose.material.icons.outlined.Pause
import androidx.compose.material.icons.outlined.PlayArrow
import androidx.compose.material.icons.outlined.Search
import androidx.compose.material.icons.outlined.WarningAmber
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.listSaver
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.luminance
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

enum class LogLevel(val label: String) {
    INFO("信息"), WARNING("警告"), ERROR("错误"),
}

enum class LogFilter(val label: String, val tag: String, val level: LogLevel?) {
    ALL("全部", "all", null), INFO("信息", "info", LogLevel.INFO),
    WARNING("警告", "warning", LogLevel.WARNING), ERROR("错误", "error", LogLevel.ERROR),
}

/** Only allowlisted text enters this view model; source event strings are not retained. */
data class LogEntry(
    val timestampMillis: Long?,
    val code: String,
    val title: String,
    val description: String,
    val level: LogLevel,
)

private data class EventDescriptor(val title: String, val description: String, val level: LogLevel)

private fun info(title: String, description: String) = EventDescriptor(title, description, LogLevel.INFO)
private fun warning(title: String, description: String) = EventDescriptor(title, description, LogLevel.WARNING)
private fun error(title: String, description: String) = EventDescriptor(title, description, LogLevel.ERROR)

private val logDescriptions = mapOf(
    "service_started" to info("服务已启动", "本机 API 已开始监听。"),
    "service_stopped" to info("服务已停止", "本次服务已停止，运行资源已进入清理流程。"),
    "configuration_saved" to info("设置已保存", "服务配置已更新。"),
    "account_imported" to info("凭证已导入", "登录会话已保存，生成检查结果单独记录。"),
    "account_enabled" to info("凭证已启用", "该凭证已参与后续请求分配。"),
    "account_disabled" to info("凭证已停用", "新请求不再分配给该凭证。"),
    // The backend also uses this code when a late operation meets a deletion
    // tombstone, so it does not prove that physical cleanup just completed.
    "account_deleted" to info("凭证处于删除状态", "该凭证不能再用于新请求或后续导入。"),
    "account_generation_verified" to info("凭证检查通过", "已完成一次真实生成检查。"),

    "account_busy" to warning("请求排队繁忙", "排队等待时间已到，请稍后再试。"),
    "rate_limit" to warning("请求暂时受限", "上游要求稍后再试，服务会遵守冷却时间。"),
    "no_available_account" to warning("暂无可用凭证", "请查看凭证是否已启用及其当前状态。"),
    "session_required" to warning("需要登录会话", "请在凭证页完成登录后继续。"),
    "session_invalid" to warning("会话需要更新", "保存的会话无法使用，请重新登录。"),
    "upstream_authentication_error" to warning("会话认证未通过", "请确认网站可访问，再更新登录会话。"),
    "account_deletion_pending" to warning("凭证清理仍在进行", "正在等待在途请求结束，后台会继续清理。"),
    "account_not_found" to warning("凭证记录不存在", "该凭证可能已删除，请查看最新列表。"),
    "invalid_port" to warning("端口设置无效", "请使用 1024 至 65535 之间的端口。"),
    "invalid_api_key" to warning("接口密钥格式无效", "请在设置中检查密钥格式。"),
    "invalid_timeout" to warning("等待时间无效", "请使用 1 至 600 秒之间的等待时间。"),
    "invalid_request" to warning("操作参数无效", "请检查输入内容后重试。"),
    "invalid_request_error" to warning("请求格式无效", "请求内容不符合当前接口要求。"),
    "invalid_session_import" to warning("会话导入未完成", "提供的会话格式不符合要求。"),
    "model_not_found" to warning("模型不在可用目录中", "请使用当前凭证支持的模型。"),
    "not_found" to warning("请求路径不存在", "请检查客户端填写的接口路径。"),
    "service_busy" to warning("服务正在忙", "管理操作正在排队，请稍后再试。"),
    "service_closed" to warning("服务已关闭", "请返回概览页查看当前运行状态。"),

    "service_error" to error("服务操作未完成", "内部运行出现异常，请查看服务状态。"),
    "service_start_failed" to error("服务未能启动", "本机 API 未能完成启动。"),
    "configuration_rollback_restart_failed" to error("恢复设置后启动失败", "原设置已恢复，但服务需要重新启动。"),
    "port_unavailable" to error("监听端口不可用", "端口可能已被占用，请在设置中更换。"),
    "account_deletion_failed" to error("凭证清理未完成", "凭证已停用，请重试删除。"),
    "session_storage_error" to error("会话保存失败", "无法保存会话更新，请检查应用存储状态。"),
    "upstream_connection_error" to error("上游连接失败", "网络请求未能完成。"),
    "upstream_timeout" to error("上游响应超时", "请求超过了配置的等待时间。"),
    "upstream_permission_denied" to error("上游未接受请求", "凭证权限或请求校验未通过。"),
    "upstream_error" to error("上游请求异常", "请求未能正常完成。"),
    "page_initialization_failed" to error("页面初始化失败", "无法取得本次请求所需的页面信息。"),
    "protocol_error" to error("响应解析失败", "上游数据未能按预期解析。"),
    "stream_interrupted" to error("流式响应中断", "响应未完整结束，请查看客户端请求状态。"),
    "generation_unverified" to error("生成检查未通过", "未收到完整的可见文本响应。"),
    "waa_unavailable" to error("校验运行环境不可用", "请求校验未能准备完成。"),
    "waa_protocol_error" to error("校验数据解析失败", "请求校验数据不符合预期。"),
    "waa_execution_error" to error("校验程序执行失败", "本次请求校验未能完成。"),
    "foreground_unavailable" to error("前台服务启动受阻", "请返回应用后查看服务状态。"),
    "backend_unavailable" to error("内置服务暂不可用", "应用未能完成本次管理操作。"),
)

private val unknownLog = info("未识别事件", "当前版本未收录此事件的说明。")
private const val LAST_VALID_TIMESTAMP_MILLIS = 253402300799999L

private fun safeLogEntry(timestampMillis: Long?, code: String): LogEntry {
    val descriptor = logDescriptions[code] ?: unknownLog
    val safeTime = timestampMillis?.takeIf { it in 1..LAST_VALID_TIMESTAMP_MILLIS }
    return LogEntry(safeTime, if (code in logDescriptions) code else "unknown_event",
        descriptor.title, descriptor.description, descriptor.level)
}

// Save only safe event codes and timestamps, not the ProxyUiState or its API key.
private val pausedLogsSaver = listSaver<List<LogEntry>, String>(
    save = { entries -> entries.take(100).flatMap { listOf(it.timestampMillis?.toString().orEmpty(), it.code) } },
    restore = { values -> values.chunked(2).filter { it.size == 2 }
        .take(100).map { safeLogEntry(it[0].toLongOrNull(), it[1]) } },
)

private data class LogRow(val id: Long, val entry: LogEntry)

/** Identity is assigned before filtering and never contains an untrusted event string. */
private class LogIdentityRegistry(
    private var nextId: Long = 0,
    private var chronological: List<LogRow> = emptyList(),
) {
    fun update(newestFirst: List<LogEntry>): List<LogRow> {
        val incoming = newestFirst.asReversed()
        // The backend appends events and drops the oldest at 100. Preserve the
        // overlapping suffix first so equal-time duplicate events keep distinct IDs.
        val overlap = (minOf(chronological.size, incoming.size) downTo 0).first { count ->
            chronological.takeLast(count).map { it.entry } == incoming.take(count)
        }
        val retained = chronological.takeLast(overlap)
        val unused = chronological.dropLast(overlap).toMutableList()
        val tail = incoming.drop(overlap).map { entry ->
            // Also retain identity if the same safe event moves in a reordered list.
            val existing = unused.indexOfFirst { it.entry == entry }
            if (existing >= 0) unused.removeAt(existing) else LogRow(nextId++, entry)
        }
        chronological = retained + tail
        return chronological.asReversed()
    }

    fun save(): List<String> = listOf(nextId.toString()) + chronological.flatMap {
        listOf(it.id.toString(), it.entry.timestampMillis?.toString().orEmpty(), it.entry.code)
    }

    companion object {
        fun restore(values: List<String>): LogIdentityRegistry {
            val rows = values.drop(1).chunked(3).filter { it.size == 3 }.takeLast(100).mapNotNull {
                val id = it[0].toLongOrNull()?.takeIf { value -> value >= 0 && value < Long.MAX_VALUE - 1000 }
                    ?: return@mapNotNull null
                LogRow(id, safeLogEntry(it[1].toLongOrNull(), it[2]))
            }.distinctBy { it.id }
            val next = maxOf(values.firstOrNull()?.toLongOrNull() ?: 0, (rows.maxOfOrNull { it.id } ?: -1) + 1)
            return LogIdentityRegistry(next.coerceIn(0, Long.MAX_VALUE - 1000), rows)
        }
    }
}

private val logIdentitySaver = listSaver<LogIdentityRegistry, String>(
    save = { it.save() }, restore = { LogIdentityRegistry.restore(it) },
)

/** Pure UI conversion: no service calls, credentials or arbitrary source text. */
fun mapLogEvents(events: List<UiLogEvent>): List<LogEntry> = events.takeLast(100).asReversed().map { event ->
    val millis = if (event.at.isFinite() && event.at > 0 && event.at <= 253402300799.0) {
        (event.at * 1000).toLong()
    } else null
    safeLogEntry(millis, event.code)
}

fun formatLogTimestamp(timestampMillis: Long?): String =
    if (timestampMillis == null || timestampMillis !in 1..LAST_VALID_TIMESTAMP_MILLIS) "时间未知"
    else SimpleDateFormat("MM-dd HH:mm:ss", Locale.ROOT).format(Date(timestampMillis))

/** Searches only the already-safe display fields. */
fun filterLogEntries(entries: List<LogEntry>, filter: LogFilter = LogFilter.ALL, query: String = ""): List<LogEntry> {
    val needle = query.trim()
    return entries.filter { entry ->
        (filter.level == null || entry.level == filter.level) &&
            (needle.isEmpty() || listOf(entry.title, entry.description, entry.code, entry.level.label,
                formatLogTimestamp(entry.timestampMillis)).any { it.contains(needle, ignoreCase = true) })
    }
}

/** Re-applies the allowlist before export, even if a caller constructs an entry directly. */
fun exportLogEntries(entries: List<LogEntry>): String = entries.joinToString("\n") { entry ->
    val descriptor = logDescriptions[entry.code] ?: unknownLog
    val code = if (entry.code in logDescriptions) entry.code else "unknown_event"
    "${formatLogTimestamp(entry.timestampMillis)} [${descriptor.level.label}] ${descriptor.title} · $code"
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun LogsPage(state: ProxyUiState, actions: UiActions, modifier: Modifier = Modifier) {
    var query by rememberSaveable { mutableStateOf("") }
    var filterName by rememberSaveable { mutableStateOf(LogFilter.ALL.name) }
    val filter = LogFilter.entries.firstOrNull { it.name == filterName } ?: LogFilter.ALL
    val incoming = remember(state.logs) { mapLogEvents(state.logs) }
    var paused by rememberSaveable { mutableStateOf(false) }
    var pausedEntries by rememberSaveable(stateSaver = pausedLogsSaver) { mutableStateOf(emptyList<LogEntry>()) }
    var copiedCount by remember { mutableIntStateOf(0) }
    var receiptCount by remember { mutableIntStateOf(0) }
    val displayed = if (paused) pausedEntries else incoming
    val filtered = remember(displayed, filter, query) { filterLogEntries(displayed, filter, query) }
    val identities = rememberSaveable(saver = logIdentitySaver) { LogIdentityRegistry() }
    val displayedRows = remember(displayed, identities) { identities.update(displayed) }
    val filteredRows = remember(displayedRows, filtered) {
        val selected = filtered.toSet()
        displayedRows.filter { it.entry in selected }
    }
    val searching = query.isNotBlank()
    val filtering = searching || filter != LogFilter.ALL
    val listState = rememberLazyListState()

    LazyColumn(
        modifier = modifier.fillMaxSize().testTag("page_logs"),
        state = listState,
        contentPadding = PaddingValues(start = 20.dp, top = 12.dp, end = 20.dp, bottom = 20.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item(key = "logs-header") {
            PageHeader("运行日志", "本次运行 · 最近 100 条管理记录")
        }
        item(key = "logs-controls") {
            PgCard(contentPadding = PaddingValues(16.dp), spacing = 10.dp) {
                Column {
                    OutlinedTextField(
                        value = query,
                        onValueChange = { query = it.take(160); copiedCount = 0 },
                        modifier = Modifier.fillMaxWidth().testTag("logs_search"),
                        singleLine = true,
                        placeholder = { Text("搜索事件、代码或时间") },
                        leadingIcon = { Icon(Icons.Outlined.Search, null) },
                        trailingIcon = if (query.isNotEmpty()) ({
                            IconButton(onClick = { query = ""; copiedCount = 0 }) {
                                Icon(Icons.Outlined.Close, "清空搜索")
                            }
                        }) else null,
                        shape = RoundedCornerShape(16.dp),
                    )
                    Spacer(Modifier.height(8.dp))
                    FlowRow(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                        LogFilter.entries.forEach { option ->
                            FilterChip(
                                selected = filter == option,
                                onClick = { filterName = option.name; copiedCount = 0 },
                                label = { Text(option.label) },
                                modifier = Modifier.heightIn(min = maxOf(36f, 32f * LocalDensity.current.fontScale).dp).testTag("logs_filter_${option.tag}"),
                            )
                        }
                    }
                    Spacer(Modifier.height(8.dp))
                    LogActionButtons(
                        paused = paused,
                        hasResults = filtered.isNotEmpty(),
                        onPause = {
                            if (!paused) pausedEntries = incoming.toList()
                            paused = !paused
                            copiedCount = 0
                        },
                        onCopy = {
                            actions.onCopy(exportLogEntries(filtered))
                            receiptCount = filtered.size
                            copiedCount = filtered.size
                        },
                    )
                    AnimatedVisibility(
                        visible = copiedCount > 0,
                        enter = fadeIn(tween(PgMotion.Fast, easing = PgMotion.Easing)) +
                            expandVertically(tween(PgMotion.Medium, easing = PgMotion.Easing)),
                        exit = fadeOut(tween(PgMotion.Fast, easing = PgMotion.Easing)) +
                            shrinkVertically(tween(PgMotion.Medium, easing = PgMotion.Easing)),
                    ) {
                        Column {
                            Spacer(Modifier.height(10.dp))
                            Text("已复制 $receiptCount 条脱敏记录", color = MaterialTheme.colorScheme.primary,
                                style = MaterialTheme.typography.bodySmall)
                        }
                    }
                }
            }
        }
        if (paused) item(key = "logs-paused") {
            PgCard(Modifier.animateItem(
                fadeInSpec = tween(PgMotion.Fast, easing = PgMotion.Easing),
                placementSpec = tween(PgMotion.Medium, easing = PgMotion.Easing),
                fadeOutSpec = tween(PgMotion.Fast, easing = PgMotion.Easing),
            ), contentPadding = PaddingValues(12.dp), spacing = 6.dp) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Icon(Icons.Outlined.Pause, null, tint = MaterialTheme.colorScheme.primary, modifier = Modifier.size(22.dp))
                    Spacer(Modifier.width(10.dp))
                    Column(Modifier.weight(1f)) {
                        Text("显示已暂停", fontWeight = FontWeight.SemiBold, color = MaterialTheme.colorScheme.primary)
                        Text("服务继续运行，恢复后显示新记录。",
                            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
            }
        }
        item(key = "logs-section") {
            SectionLabel(if (query.isBlank() && filter == LogFilter.ALL) "管理记录 · ${filtered.size} 条"
                else "筛选结果 · ${filtered.size} / ${displayed.size} 条")
        }
        if (filtered.isEmpty()) item(key = "logs-empty") {
            PgCard(Modifier.animateItem(
                fadeInSpec = tween(PgMotion.Fast, easing = PgMotion.Easing),
                placementSpec = tween(PgMotion.Medium, easing = PgMotion.Easing),
                fadeOutSpec = tween(PgMotion.Fast, easing = PgMotion.Easing),
            ), contentPadding = PaddingValues(16.dp), spacing = 10.dp) {
                AnimatedContent(targetState = displayed.isEmpty(), transitionSpec = {
                    (fadeIn(tween(PgMotion.Fast, easing = PgMotion.Easing)) togetherWith
                        fadeOut(tween(PgMotion.Fast, easing = PgMotion.Easing)))
                        .using(SizeTransform(sizeAnimationSpec = { _, _ -> tween(PgMotion.Medium, easing = PgMotion.Easing) }))
                }, label = "logs-empty-state") { noRecords ->
                    val contentModifier = if (noRecords == displayed.isEmpty()) Modifier else Modifier.clearAndSetSemantics { }
                    Column(contentModifier.fillMaxWidth().padding(vertical = 12.dp), horizontalAlignment = Alignment.CenterHorizontally,
                        verticalArrangement = Arrangement.spacedBy(10.dp)) {
                        Icon(if (noRecords) Icons.Outlined.History else Icons.Outlined.Search, null,
                            Modifier.size(28.dp), tint = MaterialTheme.colorScheme.onSurfaceVariant)
                        Text(if (noRecords) "暂无运行记录" else "没有匹配的记录", fontWeight = FontWeight.SemiBold)
                        Text(if (noRecords) "服务、凭证和设置操作会显示在这里。" else "换个关键词或日志级别试试。",
                            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
            }
        }
        items(filteredRows, key = { "log-row-${it.id}" }, contentType = { "log-entry" }) { row ->
            LogEntryCard(row.entry, Modifier.animateItem(
                fadeInSpec = if (filtering) null else tween(PgMotion.Fast, easing = PgMotion.Easing),
                placementSpec = if (filtering) null else tween(PgMotion.Medium, easing = PgMotion.Easing),
                fadeOutSpec = if (filtering) null else tween(PgMotion.Fast, easing = PgMotion.Easing),
            ))
        }
        item(key = "logs-footer") {
            Text("仅记录管理事件，不包含逐条 API 请求；复制内容不含凭证。",
                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@Composable
private fun LogActionButtons(paused: Boolean, hasResults: Boolean, onPause: () -> Unit, onCopy: () -> Unit) {
    val pauseInteraction = remember { MutableInteractionSource() }
    val copyInteraction = remember { MutableInteractionSource() }
    val pauseButton: @Composable (Modifier) -> Unit = { modifier ->
        OutlinedButton(
            onClick = onPause,
            interactionSource = pauseInteraction,
            modifier = modifier.heightIn(min = 48.dp).pressFeedback(pauseInteraction).testTag("logs_pause"),
            contentPadding = PaddingValues(horizontal = 12.dp, vertical = 10.dp),
        ) {
            Icon(if (paused) Icons.Outlined.PlayArrow else Icons.Outlined.Pause, null, Modifier.size(18.dp))
            Spacer(Modifier.width(6.dp))
            Text(if (paused) "继续显示" else "暂停显示")
        }
    }
    val copyButton: @Composable (Modifier) -> Unit = { modifier ->
        FilledTonalButton(
            onClick = onCopy,
            enabled = hasResults,
            interactionSource = copyInteraction,
            modifier = modifier.heightIn(min = 48.dp).pressFeedback(copyInteraction, hasResults).testTag("logs_copy"),
            contentPadding = PaddingValues(horizontal = 12.dp, vertical = 10.dp),
        ) {
            Icon(Icons.Outlined.ContentCopy, null, Modifier.size(17.dp))
            Spacer(Modifier.width(6.dp))
            Text("复制结果")
        }
    }
    val fontScale = LocalDensity.current.fontScale
    BoxWithConstraints(Modifier.fillMaxWidth()) {
        if (fontScale > 1.3f || maxWidth < 240.dp) {
            Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                pauseButton(Modifier.fillMaxWidth())
                copyButton(Modifier.fillMaxWidth())
            }
        } else {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                pauseButton(Modifier.weight(1f))
                copyButton(Modifier.weight(1f))
            }
        }
    }
}

@Composable
private fun LogEntryCard(entry: LogEntry, modifier: Modifier = Modifier) {
    val dark = MaterialTheme.colorScheme.surface.luminance() < 0.5f
    val tint = when (entry.level) {
        LogLevel.INFO -> MaterialTheme.colorScheme.primary
        LogLevel.WARNING -> if (dark) Color(0xFFE9C46A) else Color(0xFF956A0D)
        LogLevel.ERROR -> MaterialTheme.colorScheme.error
    }
    PgCard(modifier, contentPadding = PaddingValues(16.dp), spacing = 8.dp) {
        Column {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Icon(when (entry.level) {
                    LogLevel.INFO -> Icons.Outlined.Info
                    LogLevel.WARNING -> Icons.Outlined.WarningAmber
                    LogLevel.ERROR -> Icons.Outlined.ErrorOutline
                }, null, Modifier.size(20.dp), tint = tint)
                Spacer(Modifier.width(10.dp))
                Text(entry.title, modifier = Modifier.weight(1f), fontWeight = FontWeight.SemiBold,
                    style = MaterialTheme.typography.titleSmall)
            }
            Spacer(Modifier.height(8.dp))
            Text(entry.description, color = MaterialTheme.colorScheme.onSurfaceVariant,
                style = MaterialTheme.typography.bodySmall)
            Spacer(Modifier.height(8.dp))
            Text("${formatLogTimestamp(entry.timestampMillis)} · ${entry.level.label}", color = tint,
                style = MaterialTheme.typography.labelSmall)
            Spacer(Modifier.height(3.dp))
            Text(entry.code, modifier = Modifier.testTag("log_code_${entry.code}"),
                color = MaterialTheme.colorScheme.onSurfaceVariant, fontFamily = FontFamily.Monospace,
                style = MaterialTheme.typography.labelSmall)
        }
    }
}
