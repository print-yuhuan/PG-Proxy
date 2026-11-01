package dev.pgproxy.app.ui

import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.outlined.ContentCopy
import androidx.compose.material.icons.outlined.Refresh
import androidx.compose.material.icons.outlined.WarningAmber
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import org.json.JSONArray
import org.json.JSONObject

private fun logStage(value: String): String = when (value) {
    "received" -> "收到请求"
    "authentication" -> "客户端接口鉴权"
    "request_body" -> "读取请求体"
    "parameters" -> "处理请求参数"
    "routing" -> "匹配接口地址"
    "queue" -> "等待请求队列"
    "credential" -> "选择与加载凭证"
    "page_initialization" -> "初始化 Playground 页面"
    "model_catalog" -> "读取模型目录"
    "request_encoding" -> "编码上游请求"
    "waa_challenge" -> "获取 WAA 校验挑战"
    "waa_interpreter" -> "获取 WAA 校验程序"
    "waa_execution" -> "执行 WAA 校验程序"
    "upstream_generate" -> "请求上游生成"
    "upstream_count_tokens" -> "请求上游计数"
    "response_parsing" -> "解析上游响应"
    else -> boundedLogText(value, 96)
}

private fun networkType(value: String): String = when (value) {
    "hosting" -> "机房"; "mobile" -> "移动"; "business" -> "商业"; "residential" -> "住宅"
    else -> boundedLogText(value, 64)
}

private fun riskFlag(value: String): String = when (value) {
    "proxy" -> "代理"; "vpn" -> "VPN"; "tor" -> "Tor"; "abuser" -> "滥用记录"
    else -> boundedLogText(value, 40)
}

/** The only accepted source is the private log_detail result, never the application snapshot. */
fun parseLogDetail(value: JSONObject): LogDetailUi {
    val sections = mutableListOf<LogDetailSection>()
    var remaining = 48_000
    fun scalar(value: Any?, limit: Int = 4096): String {
        if (value == null || value == JSONObject.NULL) return ""
        val text = when (value) {
            is Boolean -> if (value) "是" else "否"
            is String, is Number -> value.toString()
            else -> return ""
        }
        val safe = boundedLogText(text, minOf(limit, remaining.coerceAtLeast(0)))
        remaining -= safe.length
        return safe
    }
    fun field(name: String, value: Any?, mono: Boolean = false, limit: Int = 4096): LogDetailField? =
        scalar(value, limit).takeIf { it.isNotBlank() }?.let { LogDetailField(name, it, mono) }
    fun time(value: Any?): String = (value as? Number)?.toDouble()?.takeIf { it.isFinite() && it > 0 && it <= 253402300799.0 }
        ?.let { formatLogTimestamp((it * 1000).toLong()) }.orEmpty()
    fun duration(value: Any?): String = formatLogDuration((value as? Number)?.toDouble()?.takeIf { it.isFinite() && it >= 0 })
    fun add(title: String, fields: List<LogDetailField?>, warning: Boolean = false) {
        fields.filterNotNull().takeIf { it.isNotEmpty() }?.let { sections.add(LogDetailSection(title, it, warning)) }
    }
    val fallback = mapLogEvents(listOf(UiLogEvent(value.optDouble("at", 0.0), value.optString("event")))).single()
    val knownEvent = fallback.code != "unknown_event"
    val title = scalar(value.opt("title"), 80).ifBlank { if (knownEvent) fallback.title else "日志详情" }
    val summary = scalar(value.opt("summary"), 640).ifBlank { if (knownEvent) fallback.description else "" }
    val level = LogLevel.entries.firstOrNull { it.name.equals(value.optString("level"), true) }?.label
        ?: if (knownEvent) fallback.level.label else ""
    val outcome = when (value.optString("outcome")) { "running" -> "处理中"; "success" -> "调用成功"; "failure" -> "调用失败"; else -> "" }
    add("调用信息", listOf(
        field("结果", outcome), field("级别", level), field("开始时间", time(value.opt("startedAt") ?: value.opt("at"))),
        field("结束时间", time(value.opt("finishedAt"))), field("请求方法", value.opt("method"), true),
        field("接口地址", value.opt("address") ?: value.opt("path"), true), field("响应 HTTP 状态码", value.opt("status"), true),
        field("调用耗时", duration(value.opt("durationMs"))), field("处理阶段", logStage(value.optString("stage"))),
        field("请求 ID", value.opt("requestId") ?: value.opt("id"), true), field("事件代码", value.opt("event"), true),
    ))

    value.optJSONObject("error")?.let { error ->
        add("失败详情", listOf(
            field("失败类型", error.opt("typeLabel") ?: error.opt("type")), field("类型代码", error.opt("type"), true),
            field("业务错误码", error.opt("code"), true), field("错误记录 HTTP 状态码", error.opt("httpStatus"), true),
            field("业务错误 HTTP 状态码", error.opt("businessHttpStatus"), true),
            field("上游 HTTP 状态码", error.opt("upstreamStatus"), true), field("上游业务错误码", error.opt("upstreamCode"), true),
            field("错误说明", error.opt("message")),
            field("原始错误（已脱敏）", error.opt("raw"), true, 12_000),
            field("异常名称", error.opt("exceptionType"), true),
        ), warning = true)
    }
    value.optJSONObject("credential")?.let { credential ->
        add("凭证异常", listOf(field("异常类型", value.optJSONObject("error")?.opt("typeLabel") ?: credential.opt("kind")),
            field("凭证标识（匿名）", credential.opt("accountId"), true)), warning = true)
    }
    if (value.optBoolean("credentialIssue") && sections.none { it.title == "凭证异常" }) {
        val localAuth = value.optString("stage") == "authentication" || value.optJSONObject("error")?.optString("code") == "authentication_error"
        add(if (localAuth) "接口密钥异常" else "凭证异常", listOf(field("排查建议", if (localAuth)
            "请核对客户端填写的接口密钥与 PG Proxy 设置是否一致。这是本机接口鉴权结果，不代表 Google 登录凭证失效。"
            else "请检查凭证是否存在、已启用且会话仍有效；具体原因以上方错误信息为准。")), warning = true)
    }

    // Parameters are already allowlisted by the backend. Render bounded trees without exposing any other top-level data.
    fun flatten(data: Any?, prefix: String = "", depth: Int = 0): List<LogDetailField> {
        if (depth > 5 || remaining <= 0) return emptyList()
        return when (data) {
            is JSONObject -> data.keys().asSequence().take(64).flatMap { key ->
                val label = boundedLogText(key, 96)
                flatten(data.opt(key), if (prefix.isBlank()) label else "$prefix.$label", depth + 1).asSequence()
            }.take(96).toList()
            is JSONArray -> (0 until minOf(data.length(), 32)).flatMap { index ->
                flatten(data.opt(index), "$prefix[$index]", depth + 1)
            }.take(96)
            else -> listOfNotNull(field(prefix.ifBlank { "参数" }, data, true, 1024))
        }
    }
    value.optJSONObject("parameters")?.let { add("关键请求参数", flatten(it)) }

    value.optJSONArray("upstream")?.let { calls ->
        for (index in 0 until minOf(calls.length(), 32)) {
            val call = calls.optJSONObject(index) ?: continue
            add("上游调用 ${index + 1}", listOf(
                field("时间", time(call.opt("at"))), field("方法", call.opt("method"), true),
                field("接口", call.opt("url"), true), field("HTTP 状态码", call.opt("status"), true),
                field("业务错误码", call.opt("code"), true), field("耗时", duration(call.opt("durationMs"))),
                field("原始错误（已脱敏）", call.opt("raw"), true, 8192),
            ))
        }
        if (value.optBoolean("upstreamTruncated")) add("上游记录范围", listOf(field("说明", "上游步骤较多，此处保留最近的调用记录；较早记录已省略。")))
    }
    value.optJSONArray("stages")?.let { stages ->
        add("处理过程", (0 until minOf(stages.length(), 64)).mapNotNull { index ->
            val stage = stages.optJSONObject(index) ?: return@mapNotNull null
            field(time(stage.opt("at")).ifBlank { "阶段 ${index + 1}" }, logStage(stage.optString("stage")))
        })
        if (value.optBoolean("stagesTruncated")) add("处理过程范围", listOf(field("说明", "部分中间阶段因记录数量限制已省略，当前阶段及最终错误仍保留。")))
    }
    value.optJSONObject("network")?.let { network ->
        val fields = mutableListOf<LogDetailField?>()
        val data = network.optJSONObject("data")
        if (data != null) {
            fields += field("出口 IP（已脱敏）", data.opt("ip"), true)
            fields += field("地区", listOf(data.optString("countryCode"), data.optString("country"), data.optString("region"))
                .filter { it.isNotBlank() }.distinct().joinToString(" · "))
            fields += field("网络组织", data.opt("organization"))
            fields += field("网络类型", networkType(data.optString("networkType")))
            fields += field("风险分", (data.opt("riskScore") as? Number)?.let { "${it.toInt()}/100" })
            fields += field("风险标记", data.optJSONArray("riskFlags")?.let { flags ->
                (0 until minOf(flags.length(), 8)).joinToString("、") { riskFlag(flags.optString(it)) }
            })
            fields += field("查询来源", data.opt("source"))
            fields += field("快照时间", time(data.opt("updatedAt")))
        }
        if (network.optBoolean("stale")) fields += field("缓存状态", "快照较旧，请在概览页刷新网络信息后再对照。")
        network.optJSONArray("hints")?.let { hints ->
            for (index in 0 until minOf(hints.length(), 10)) {
                val hint = hints.opt(index)
                fields += field("排查线索 ${index + 1}", if (hint is JSONObject) hint.opt("message") else hint, limit = 2048)
            }
        }
        fields += field("说明", network.opt("note"), limit = 2048)
        fields += field("网络查询状态", network.opt("lookupError"), limit = 512)
        add("失败时的网络环境", fields)
    }
    val export = buildString {
        appendLine(title)
        if (summary.isNotBlank()) appendLine(summary)
        sections.forEach { section ->
            appendLine("\n${section.title}")
            section.fields.forEach { appendLine("${it.label}：${it.value}") }
        }
        append("\n密钥和凭证已脱敏。网络信息仅为排查线索，不代表 Google 的实际判定。")
    }
    return LogDetailUi(title, summary, sections, export)
}

@Composable
internal fun LogDetailPage(id: String?, legacy: LogEntry?, actions: UiActions, onBack: () -> Unit) {
    var refresh by remember(id) { mutableIntStateOf(0) }
    var result by remember(id) { mutableStateOf<LogDetailResult?>(null) }
    var loading by remember(id) { mutableStateOf(id != null) }
    var copied by remember(id) { mutableStateOf(false) }
    val listState = rememberLazyListState()
    val refreshRotation = remember(id) { Animatable(0f) }
    val refreshInteraction = remember { MutableInteractionSource() }
    LaunchedEffect(refresh) {
        if (refresh > 0) refreshRotation.animateTo(refresh * 360f, tween(400, easing = PgMotion.Easing))
    }
    val refreshDetail: () -> Unit = {
        if (!loading) {
            loading = true
            copied = false
            refresh++
        }
    }
    val loadDetail by rememberUpdatedState(actions.onLoadLogDetail)
    DisposableEffect(id, refresh) {
        var active = true
        if (id != null) {
            loading = true
            loadDetail(id) { response -> if (active) {
                // A temporary bridge failure must not remove the visible detail
                // and collapse its scroll position while the user retries.
                result = response.copy(data = response.data ?: result?.data)
                loading = false
            } }
        }
        onDispose { active = false }
    }
    val detail = result?.data ?: legacy?.let { entry ->
        LogDetailUi(entry.title, entry.description, listOf(LogDetailSection("事件信息", listOf(
            LogDetailField("时间", formatLogTimestamp(entry.timestampMillis)), LogDetailField("级别", entry.level.label),
            LogDetailField("事件代码", entry.code, true),
        ))), exportLogEntries(listOf(entry)))
    }
    LazyColumn(Modifier.fillMaxSize().testTag("page_log_detail"), state = listState,
        contentPadding = PaddingValues(start = 20.dp, end = 20.dp, top = 12.dp, bottom = 24.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp)) {
        item(key = "detail-header") {
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                IconButton(onClick = onBack, modifier = Modifier.testTag("log_detail_back")) {
                    Icon(Icons.AutoMirrored.Outlined.ArrowBack, "返回日志列表")
                }
                Column(Modifier.weight(1f).padding(horizontal = 8.dp)) {
                    Text("日志详情", style = MaterialTheme.typography.headlineSmall)
                    Text("仅展示已脱敏的调用信息", color = MaterialTheme.colorScheme.onSurfaceVariant, style = MaterialTheme.typography.bodySmall)
                }
                if (id != null) IconButton(onClick = refreshDetail, enabled = !loading,
                    interactionSource = refreshInteraction,
                    modifier = Modifier.size(48.dp).pressFeedback(refreshInteraction, !loading)
                        .clip(CircleShape).background(MaterialTheme.colorScheme.surface)
                        .semantics { stateDescription = if (loading) "正在读取日志详情" else "可刷新日志详情" }
                        .testTag("log_detail_refresh")) {
                    // Render complete turns as the identity transform. Keeping
                    // 360/720/... in the layer can introduce subpixel raster
                    // differences even though the icon has visibly returned.
                    Icon(Icons.Outlined.Refresh, "刷新日志详情", Modifier.rotate(refreshRotation.value % 360f)
                        .testTag("log_detail_refresh_icon"),
                        tint = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
        }
        // Loading only affects the fixed header button. Inserting/removing a
        // progress row here shifts every item below it on each refresh.
        if (result?.error?.isNotBlank() == true) item(key = "detail-error") {
            PgCard(spacing = 12.dp) {
                Icon(Icons.Outlined.WarningAmber, null, tint = MaterialTheme.colorScheme.error)
                Text(result?.error.orEmpty(), color = MaterialTheme.colorScheme.onSurface, style = MaterialTheme.typography.bodyMedium)
                if (result?.data != null) Text("以下保留上次读取的详情，尚未取得最新状态。",
                    color = MaterialTheme.colorScheme.onSurfaceVariant, style = MaterialTheme.typography.bodySmall)
                TextButton(onClick = refreshDetail, enabled = !loading,
                    modifier = Modifier.testTag("log_detail_retry")) { Text("重新加载") }
            }
        }
        if (detail != null) {
            item(key = "detail-title") {
                PgCard(Modifier.testTag("log_detail_summary"), spacing = 10.dp) {
                    Text(detail.title, style = MaterialTheme.typography.titleMedium)
                    if (detail.summary.isNotBlank()) SelectionContainer {
                        Text(detail.summary, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    FilledTonalButton(onClick = { actions.onCopy(detail.exportText); copied = true },
                        modifier = Modifier.fillMaxWidth().testTag("log_detail_copy")) {
                        Icon(Icons.Outlined.ContentCopy, null, Modifier.size(18.dp))
                        Spacer(Modifier.width(8.dp))
                        Text(if (copied) "已复制脱敏详情" else "复制详情")
                    }
                }
            }
            itemsIndexed(detail.sections, key = { index, _ -> "section-$index" }) { _, section ->
                PgCard(spacing = 12.dp) {
                    Text(section.title, style = MaterialTheme.typography.titleSmall,
                        color = if (section.warning) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.primary)
                    SelectionContainer {
                        Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                            section.fields.forEach { field ->
                                Column(verticalArrangement = Arrangement.spacedBy(3.dp)) {
                                    Text(field.label, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                                    Text(field.value, style = MaterialTheme.typography.bodySmall,
                                        fontFamily = if (field.monospace) FontFamily.Monospace else FontFamily.Default,
                                        fontWeight = if (section.warning && field.label == "失败类型") FontWeight.SemiBold else FontWeight.Normal)
                                }
                            }
                        }
                    }
                }
            }
            item(key = "detail-footer") {
                Text("网络地区与风险分来自概览页的查询缓存，仅供排查；不会据此阻止请求，也不代表 Google 的实际判定。",
                    style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}
