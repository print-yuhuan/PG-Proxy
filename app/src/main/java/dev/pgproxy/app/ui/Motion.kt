package dev.pgproxy.app.ui

import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.SizeTransform
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.spring
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.slideInVertically
import androidx.compose.animation.slideOutVertically
import androidx.compose.animation.togetherWith
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsPressedAsState
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.text.TextStyle

/** Finite transitions: Compose also applies Android's animator duration scale. */
object PgMotion {
    const val Fast = 120
    const val Medium = 200
    const val Slow = 280
    val Easing = FastOutSlowInEasing
}

/** Share this source with the actual clickable, so cancellation also releases the scale. */
@Composable
fun Modifier.pressFeedback(interactionSource: MutableInteractionSource, enabled: Boolean = true): Modifier {
    val pressed by interactionSource.collectIsPressedAsState()
    val down = enabled && pressed
    val scale by animateFloatAsState(if (down) .975f else 1f,
        if (down) tween(90) else spring(dampingRatio = .86f, stiffness = 900f), label = "press-release")
    return graphicsLayer { scaleX = scale; scaleY = scale }
}

@Composable
fun AnimatedLabel(text: String, style: TextStyle, color: Color, modifier: Modifier = Modifier) {
    AnimatedContent(text, modifier = modifier, contentAlignment = Alignment.CenterStart,
        transitionSpec = {
            (fadeIn(tween(PgMotion.Fast)) + slideInVertically(tween(PgMotion.Medium, easing = PgMotion.Easing)) { it / 8 })
                .togetherWith(fadeOut(tween(90)) + slideOutVertically(tween(PgMotion.Fast)) { -it / 8 })
                .using(SizeTransform(clip = false))
        }, label = "label-change") { value ->
        Text(value, style = style, color = color,
            modifier = if (value == text) Modifier else Modifier.clearAndSetSemantics { })
    }
}
