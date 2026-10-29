package dev.pgproxy.app.ui

import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.animateContentSize
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.ContentCopy
import androidx.compose.material.icons.outlined.ErrorOutline
import androidx.compose.material.icons.outlined.HourglassEmpty
import androidx.compose.material.icons.outlined.Public
import androidx.compose.material.icons.outlined.Refresh
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** Values supplied by the network client; the card never infers location or IP quality. */
data class NetworkInfoData(
    val ip: String,
    val countryCode: String = "",
    val location: String = "",
    val organization: String = "",
    val asn: String = "",
    val networkType: String = "",
    val riskScore: Int? = null,
    val riskLabels: List<String> = emptyList(),
    val source: String = "",
    val updatedAt: Long = 0,
)

data class NetworkInfoUi(
    val data: NetworkInfoData? = null,
    val loading: Boolean = false,
    val error: String = "",
)

/** Pure presentation: querying and copying happen only through explicit callbacks. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun NetworkInfoCard(
    state: NetworkInfoUi,
    onRefresh: () -> Unit,
    onCopy: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    val colors = MaterialTheme.colorScheme
    val refreshInteraction = remember { MutableInteractionSource() }
    // One finite turn on a loading-state change, without an endless spinner or
    // invented percentage for a request whose progress is unknown.
    val refreshRotation by animateFloatAsState(if (state.loading) 180f else 0f,
        tween(PgMotion.Medium, easing = PgMotion.Easing), label = "network-refresh-turn")
    val refreshColor by animateColorAsState(if (state.loading) colors.onSurfaceVariant else colors.primary,
        tween(PgMotion.Fast), label = "network-refresh-tint")
    val data = state.data
    // A retry is a new attempt; keep old data, but do not present the previous
    // failure as though this in-flight request has already failed too.
    val hasError = !state.loading && state.error.isNotBlank()
    val latestLoading by rememberUpdatedState(state.loading)
    val latestRefresh by rememberUpdatedState(onRefresh)
    val refresh: () -> Unit = { if (!latestLoading) latestRefresh() }
    val riskScore = data?.riskScore?.takeIf { it in 0..100 }
    val riskLabels = data?.riskLabels.orEmpty().filter { it.isNotBlank() }.distinct()
    val hasDetails = data != null && (data.asn.isNotBlank() || riskScore != null)
    var detailsExpanded by rememberSaveable { mutableStateOf(false) }
    val detailsInteraction = remember { MutableInteractionSource() }

    val largeHeader = LocalDensity.current.fontScale > 1.3f
    val headerControls: @Composable () -> Unit = {
        if (hasDetails) {
            TextButton(onClick = { detailsExpanded = !detailsExpanded }, interactionSource = detailsInteraction,
                contentPadding = PaddingValues(horizontal = 8.dp),
                modifier = Modifier.heightIn(min = 48.dp).pressFeedback(detailsInteraction).testTag("network_details_toggle")
                    .semantics {
                        contentDescription = "网络详情"
                        stateDescription = if (detailsExpanded) "已展开" else "已收起"
                    }) {
                Text(if (detailsExpanded) "收起" else "详情", style = MaterialTheme.typography.labelMedium)
            }
        }
        IconButton(onClick = refresh, enabled = !state.loading, interactionSource = refreshInteraction,
            modifier = Modifier.size(48.dp).pressFeedback(refreshInteraction, !state.loading).testTag("network_refresh")) {
            Icon(Icons.Outlined.Refresh,
                if (state.loading) "正在查询网络信息" else if (data == null) "查询网络信息" else "刷新网络信息",
                Modifier.size(22.dp).rotate(refreshRotation), tint = refreshColor)
        }
    }
    PgCard(modifier.testTag("network_info_card").animateContentSize(tween(PgMotion.Medium, easing = PgMotion.Easing)),
        contentPadding = PaddingValues(horizontal = 16.dp, vertical = 12.dp), spacing = 8.dp) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            Icon(Icons.Outlined.Public, null, Modifier.size(20.dp), tint = colors.primary)
            Text("网络信息", Modifier.weight(1f).testTag("network_title"), style = MaterialTheme.typography.titleMedium)
            if (!largeHeader) headerControls()
        }
        if (largeHeader) Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End,
            verticalAlignment = Alignment.CenterVertically) { headerControls() }

        if (data != null) {
            NetworkAddress(data, onCopy)
            Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                NetworkDetail("地区", data.location.ifBlank { "地区信息暂未提供" })
                NetworkDetail("组织", data.organization.ifBlank { "组织信息暂未提供" })
            }

            if (data.networkType.isNotBlank() || riskScore != null || riskLabels.isNotEmpty()) {
                FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    if (data.networkType.isNotBlank()) NetworkBadge(data.networkType, "network_type")
                    if (riskScore != null) NetworkBadge("风险分 $riskScore/100", "network_risk_score")
                    riskLabels.forEach { NetworkBadge(it) }
                }
            }
            if (hasDetails && detailsExpanded) {
                Surface(shape = RoundedCornerShape(12.dp), color = colors.surfaceVariant,
                    modifier = Modifier.fillMaxWidth().testTag("network_details")) {
                    Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        if (data.asn.isNotBlank()) NetworkDetail("ASN", data.asn)
                        if (riskScore != null) Text("分值越高，风险越高",
                            style = MaterialTheme.typography.bodySmall, color = colors.onSurfaceVariant)
                    }
                }
            }

            if (state.loading) NetworkNotice(Icons.Outlined.HourglassEmpty, "正在更新网络信息…", isError = false)
            if (hasError) NetworkNotice(Icons.Outlined.ErrorOutline, "刷新未完成 · ${state.error}", isError = true)
            NetworkSource(data, stale = hasError || state.loading)
        } else {
            Text(when {
                state.loading -> "正在获取公网 IP、地区与网络信息…"
                hasError -> "暂时无法获取网络信息"
                else -> "查看公网 IP、所在地区与网络信息。"
            }, style = MaterialTheme.typography.bodyMedium, color = colors.onSurfaceVariant)
            if (hasError) NetworkNotice(Icons.Outlined.ErrorOutline, state.error, isError = true)
            NetworkQueryButton(if (state.loading) "正在查询…" else if (hasError) "重新查询" else "查询网络信息",
                enabled = !state.loading, onClick = refresh)
        }
        if (data != null && hasError) NetworkQueryButton("重试查询", true, refresh)
    }
}

@Composable
private fun NetworkAddress(data: NetworkInfoData, onCopy: (String) -> Unit) {
    val colors = MaterialTheme.colorScheme
    val copyInteraction = remember { MutableInteractionSource() }
    val country = data.countryCode.trim().uppercase(Locale.ROOT)
        .takeIf { it.length == 2 && it.all { letter -> letter in 'A'..'Z' } }
    val countryBadge: @Composable () -> Unit = {
        Surface(shape = RoundedCornerShape(10.dp), color = colors.secondaryContainer) {
            Text(country ?: "IP", Modifier.padding(horizontal = 8.dp, vertical = 5.dp),
                style = MaterialTheme.typography.labelMedium, fontWeight = FontWeight.SemiBold,
                color = colors.onSecondaryContainer)
        }
    }
    val copyButton: @Composable () -> Unit = {
        IconButton(onClick = { onCopy(data.ip) }, enabled = data.ip.isNotBlank(), interactionSource = copyInteraction,
            modifier = Modifier.size(48.dp).pressFeedback(copyInteraction, data.ip.isNotBlank()).testTag("network_copy_ip")) {
            Icon(Icons.Outlined.ContentCopy, "复制公网 IP", Modifier.size(20.dp), tint = colors.primary)
        }
    }
    if (LocalDensity.current.fontScale > 1.3f) {
        Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                countryBadge()
                Spacer(Modifier.weight(1f))
                copyButton()
            }
            NetworkIp(data.ip, Modifier.fillMaxWidth())
        }
    } else {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            countryBadge()
            NetworkIp(data.ip, Modifier.weight(1f))
            copyButton()
        }
    }
}

@Composable
private fun NetworkIp(ip: String, modifier: Modifier) {
    Text(ip.ifBlank { "暂未提供" }, modifier = modifier.testTag("network_ip"),
        style = MaterialTheme.typography.titleMedium, fontFamily = FontFamily.Monospace, softWrap = true)
}

@Composable
private fun NetworkDetail(label: String, value: String) {
    if (LocalDensity.current.fontScale > 1.3f) {
        Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(3.dp)) {
            Text(label, style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Text(value, style = MaterialTheme.typography.bodyMedium, softWrap = true)
        }
    } else {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.Top, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(label, Modifier.width(40.dp), style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
            Text(value, Modifier.weight(1f), style = MaterialTheme.typography.bodyMedium, softWrap = true)
        }
    }
}

@Composable
private fun NetworkBadge(text: String, tag: String? = null) {
    Surface(modifier = tag?.let { Modifier.testTag(it) } ?: Modifier, shape = RoundedCornerShape(10.dp),
        color = MaterialTheme.colorScheme.secondaryContainer) {
        Text(text, Modifier.padding(horizontal = 8.dp, vertical = 3.dp), style = MaterialTheme.typography.labelMedium,
            color = MaterialTheme.colorScheme.onSecondaryContainer, softWrap = true)
    }
}

@Composable
private fun NetworkNotice(icon: ImageVector, message: String, isError: Boolean) {
    val colors = MaterialTheme.colorScheme
    Surface(shape = RoundedCornerShape(12.dp), color = if (isError) colors.errorContainer else colors.surfaceVariant) {
        Row(Modifier.fillMaxWidth().padding(12.dp), verticalAlignment = Alignment.Top,
            horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            val color = if (isError) colors.onErrorContainer else colors.onSurfaceVariant
            Icon(icon, null, Modifier.size(18.dp), tint = color)
            Text(message, Modifier.weight(1f), style = MaterialTheme.typography.bodySmall, color = color)
        }
    }
}

@Composable
private fun NetworkQueryButton(text: String, enabled: Boolean, onClick: () -> Unit) {
    val interaction = remember { MutableInteractionSource() }
    FilledTonalButton(onClick = onClick, enabled = enabled, interactionSource = interaction,
        modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp).pressFeedback(interaction, enabled).testTag("network_query")) {
        Text(text)
    }
}

@Composable
private fun NetworkSource(data: NetworkInfoData, stale: Boolean) {
    val updated = remember(data.updatedAt) {
        if (data.updatedAt in 1..253_402_300_799L)
            SimpleDateFormat("MM-dd HH:mm", Locale.getDefault()).format(Date(data.updatedAt * 1000L))
        else "时间未知"
    }
    Text("${if (stale) "上次结果 · " else ""}${data.source.ifBlank { "来源未提供" }} · $updated",
        modifier = Modifier.testTag("network_source"), style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant, softWrap = true)
}
