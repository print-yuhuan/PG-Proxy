package dev.pgproxy.app

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.enableEdgeToEdge
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.outlined.ChevronRight
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import dev.pgproxy.app.ui.*
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/** Local, read-only attribution viewer. No browser or network is involved. */
class LicensesActivity : ComponentActivity() {
    private fun files(path: String): List<String> = assets.list(path).orEmpty().flatMap { name ->
        val next = "$path/$name"
        if (assets.list(next).orEmpty().isNotEmpty()) files(next) else listOf(next)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val theme = getSharedPreferences("appearance", MODE_PRIVATE).getString("theme", "system") ?: "system"
        setContent {
            val dark = theme == "dark" || (theme == "system" && isSystemInDarkTheme())
            SideEffect { enableEdgeToEdge(
                statusBarStyle = SystemBarStyle.auto(android.graphics.Color.TRANSPARENT, android.graphics.Color.TRANSPARENT) { dark },
                navigationBarStyle = SystemBarStyle.auto(android.graphics.Color.TRANSPARENT, android.graphics.Color.TRANSPARENT) { dark }) }
            var document by rememberSaveable { mutableStateOf<String?>(null) }
            var attempt by remember { mutableIntStateOf(0) }
            val entries by produceState<List<String>?>(null, attempt) {
                value = withContext(Dispatchers.IO) {
                    runCatching { files("third_party").filterNot { it.endsWith(".json") }.sorted() }.getOrDefault(emptyList())
                }
            }
            val documentText by produceState<String?>(null, document) {
                value = null
                val path = document ?: return@produceState
                value = withContext(Dispatchers.IO) {
                    runCatching {
                        assets.open(path).bufferedReader().use { it.readText() }.let {
                            if (path.endsWith(".html")) android.text.Html.fromHtml(it, android.text.Html.FROM_HTML_MODE_LEGACY).toString() else it
                        }
                    }.getOrElse { "许可内容暂时无法读取，请返回列表后重试。" }
                }
            }
            BackHandler(document != null) { document = null }
            PgTheme(theme) {
                Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
                    LazyColumn(Modifier.safeDrawingPadding().fillMaxSize(), contentPadding = PaddingValues(20.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                        item { IconButton(onClick = { if (document != null) document = null else finish() }) { Icon(Icons.AutoMirrored.Outlined.ArrowBack, "返回") } }
                        item { PageHeader(if (document == null) "开源许可" else document!!.substringAfterLast('/'), "感谢开源社区的贡献") }
                        if (document == null) {
                            if (entries == null) item { Text("正在读取许可清单…") }
                            else if (entries!!.isEmpty()) item { PgCard {
                                Text("暂时无法读取许可清单。")
                                TextButton(onClick = { attempt++ }) { Text("重试") }
                            } }
                            items(entries.orEmpty(), key = { it }) { path ->
                                Surface(onClick = { document = path }, shape = MaterialTheme.shapes.large, color = MaterialTheme.colorScheme.surface) {
                                    Row(Modifier.padding(18.dp), verticalAlignment = Alignment.CenterVertically) {
                                        Text(path.removePrefix("third_party/"), Modifier.weight(1f), style = MaterialTheme.typography.bodyLarge)
                                        Icon(Icons.Outlined.ChevronRight, null, tint = MaterialTheme.colorScheme.onSurfaceVariant)
                                    }
                                }
                            }
                        } else {
                            item { PgCard {
                                SelectionContainer { Text(documentText ?: "正在读取许可内容…", style = MaterialTheme.typography.bodySmall) }
                            } }
                        }
                    }
                }
            }
        }
    }
}
