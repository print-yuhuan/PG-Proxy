package dev.pgproxy.app.ui

import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.PlatformTextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.sp

private val LightPalette = lightColorScheme(
    primary = Color(0xFF2F66CF), onPrimary = Color.White,
    primaryContainer = Color(0xFFE8EFFF), onPrimaryContainer = Color(0xFF244D91),
    secondary = Color(0xFF66758B), secondaryContainer = Color(0xFFECF0F6), onSecondaryContainer = Color(0xFF33445F),
    background = Color(0xFFF3F5F9), onBackground = Color(0xFF1B2638),
    surface = Color(0xFFFFFFFF), onSurface = Color(0xFF1B2638),
    surfaceVariant = Color(0xFFEEF2F7), onSurfaceVariant = Color(0xFF626F84),
    outline = Color(0xFF9AA6B7), outlineVariant = Color(0xFFE5EAF2),
    error = Color(0xFFB74C54), errorContainer = Color(0xFFFFECEC), onErrorContainer = Color(0xFF81323A),
    surfaceContainer = Color(0xFFFFFFFF), surfaceContainerHigh = Color(0xFFEEF2F7),
    surfaceContainerLowest = Color.White, surfaceContainerLow = Color(0xFFF7F9FC), surfaceContainerHighest = Color(0xFFE3E9F2),
    surfaceTint = Color(0xFF2F66CF), inverseSurface = Color(0xFF293547), inverseOnSurface = Color(0xFFF2F5FA),
)
private val DarkPalette = darkColorScheme(
    primary = Color(0xFF8EB4FF), onPrimary = Color(0xFF143166),
    primaryContainer = Color(0xFF253A5B), onPrimaryContainer = Color(0xFFD9E6FF),
    secondary = Color(0xFFACB8CC), secondaryContainer = Color(0xFF293240), onSecondaryContainer = Color(0xFFD9E2EF),
    background = Color(0xFF11151D), onBackground = Color(0xFFE7EDF8),
    surface = Color(0xFF1C2330), onSurface = Color(0xFFE7EDF8),
    surfaceVariant = Color(0xFF252E3C), onSurfaceVariant = Color(0xFF9AA8BC),
    outline = Color(0xFF6E7D94), outlineVariant = Color(0xFF303A4A),
    error = Color(0xFFF2A2A5), errorContainer = Color(0xFF492C36), onErrorContainer = Color(0xFFFFDADC),
    surfaceContainer = Color(0xFF1C2330), surfaceContainerHigh = Color(0xFF252E3C),
    surfaceContainerLowest = Color(0xFF0C1017), surfaceContainerLow = Color(0xFF171D28), surfaceContainerHighest = Color(0xFF303B4D),
    surfaceTint = Color(0xFF8EB4FF), inverseSurface = Color(0xFFDDE5F0), inverseOnSurface = Color(0xFF253044),
)

@Composable
fun PgTheme(themeMode: String = "system", content: @Composable () -> Unit) {
    val dark = when (themeMode) { "dark" -> true; "light" -> false; else -> isSystemInDarkTheme() }
    MaterialTheme(colorScheme = animatedPalette(if (dark) DarkPalette else LightPalette),
        typography = Typography(
            headlineLarge = TextStyle(platformStyle = PlatformTextStyle(includeFontPadding = false), fontSize = 30.sp, lineHeight = 38.sp, fontWeight = FontWeight.Bold, letterSpacing = (-0.5).sp),
            headlineSmall = TextStyle(platformStyle = PlatformTextStyle(includeFontPadding = false), fontSize = 24.sp, lineHeight = 32.sp, fontWeight = FontWeight.SemiBold),
            titleLarge = TextStyle(platformStyle = PlatformTextStyle(includeFontPadding = false), fontSize = 20.sp, lineHeight = 28.sp, fontWeight = FontWeight.SemiBold),
            titleMedium = TextStyle(platformStyle = PlatformTextStyle(includeFontPadding = false), fontSize = 16.sp, lineHeight = 24.sp, fontWeight = FontWeight.SemiBold),
            titleSmall = TextStyle(platformStyle = PlatformTextStyle(includeFontPadding = false), fontSize = 14.sp, lineHeight = 21.sp, fontWeight = FontWeight.SemiBold),
            bodyLarge = TextStyle(platformStyle = PlatformTextStyle(includeFontPadding = false), fontSize = 15.sp, lineHeight = 24.sp),
            bodyMedium = TextStyle(platformStyle = PlatformTextStyle(includeFontPadding = false), fontSize = 13.sp, lineHeight = 21.sp),
            bodySmall = TextStyle(platformStyle = PlatformTextStyle(includeFontPadding = false), fontSize = 12.sp, lineHeight = 19.sp),
            labelLarge = TextStyle(platformStyle = PlatformTextStyle(includeFontPadding = false), fontSize = 14.sp, lineHeight = 20.sp, fontWeight = FontWeight.Medium),
            labelMedium = TextStyle(platformStyle = PlatformTextStyle(includeFontPadding = false), fontSize = 12.sp, lineHeight = 18.sp, fontWeight = FontWeight.Medium),
            labelSmall = TextStyle(platformStyle = PlatformTextStyle(includeFontPadding = false), fontSize = 11.sp, lineHeight = 16.sp, fontWeight = FontWeight.Medium),
        ), shapes = Shapes(extraSmall = RoundedCornerShape(12.dp), small = RoundedCornerShape(12.dp), medium = RoundedCornerShape(16.dp),
            large = RoundedCornerShape(22.dp), extraLarge = RoundedCornerShape(28.dp)), content = content)
}

@Composable
fun PgCard(
    modifier: Modifier = Modifier,
    contentPadding: PaddingValues = PaddingValues(18.dp),
    spacing: Dp = 14.dp,
    content: @Composable ColumnScope.() -> Unit,
) {
    Surface(modifier.fillMaxWidth(), shape = RoundedCornerShape(24.dp),
        color = MaterialTheme.colorScheme.surface, border = BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant.copy(alpha = .55f))) {
        Column(Modifier.padding(contentPadding), verticalArrangement = Arrangement.spacedBy(spacing), content = content)
    }
}

@Composable
fun PageHeader(title: String, subtitle: String, action: (@Composable () -> Unit)? = null) {
    Row(Modifier.fillMaxWidth().padding(bottom = 2.dp), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(3.dp)) {
            Text(title, style = MaterialTheme.typography.headlineSmall.copy(fontSize = 26.sp, lineHeight = 34.sp))
            Text(subtitle, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        if (action != null) { Spacer(Modifier.width(12.dp)); action() }
    }
}

@Composable
fun SectionLabel(text: String) {
    Text(text, Modifier.padding(start = 3.dp, top = 4.dp), style = MaterialTheme.typography.labelLarge,
        color = MaterialTheme.colorScheme.onSurfaceVariant)
}

@Composable
private fun animatedPalette(target: ColorScheme): ColorScheme = target.copy(
    primary = animatedTone(target.primary),
    onPrimary = animatedTone(target.onPrimary),
    primaryContainer = animatedTone(target.primaryContainer),
    onPrimaryContainer = animatedTone(target.onPrimaryContainer),
    secondary = animatedTone(target.secondary),
    onSecondary = animatedTone(target.onSecondary),
    secondaryContainer = animatedTone(target.secondaryContainer),
    onSecondaryContainer = animatedTone(target.onSecondaryContainer),
    background = animatedTone(target.background),
    onBackground = animatedTone(target.onBackground),
    surface = animatedTone(target.surface),
    onSurface = animatedTone(target.onSurface),
    surfaceVariant = animatedTone(target.surfaceVariant),
    onSurfaceVariant = animatedTone(target.onSurfaceVariant),
    outline = animatedTone(target.outline),
    outlineVariant = animatedTone(target.outlineVariant),
    error = animatedTone(target.error),
    onError = animatedTone(target.onError),
    errorContainer = animatedTone(target.errorContainer),
    onErrorContainer = animatedTone(target.onErrorContainer),
    surfaceContainer = animatedTone(target.surfaceContainer),
    surfaceContainerHigh = animatedTone(target.surfaceContainerHigh),
    surfaceContainerLowest = animatedTone(target.surfaceContainerLowest),
    surfaceContainerLow = animatedTone(target.surfaceContainerLow),
    surfaceContainerHighest = animatedTone(target.surfaceContainerHighest),
    surfaceTint = animatedTone(target.surfaceTint),
    inverseSurface = animatedTone(target.inverseSurface),
    inverseOnSurface = animatedTone(target.inverseOnSurface),
)

@Composable
private fun animatedTone(target: Color): Color =
    animateColorAsState(target, tween(PgMotion.Slow, easing = PgMotion.Easing), label = "theme-tone").value
