package dev.pgproxy.app

import android.content.Intent
import android.net.Uri
import java.net.URISyntaxException

internal object VerificationLink {
    private const val PLAY_SERVICES = "com.google.android.gms"

    fun playServicesIntent(url: String): Intent? {
        val data = if (url.startsWith("intent://")) {
            val webIntent = try {
                Intent.parseUri(url, Intent.URI_INTENT_SCHEME)
            } catch (_: URISyntaxException) {
                return null
            }
            if (webIntent.action != Intent.ACTION_VIEW || webIntent.component != null || webIntent.selector != null ||
                (webIntent.`package` != null && webIntent.`package` != PLAY_SERVICES)) return null
            webIntent.data ?: return null
        } else Uri.parse(url)

        val authority = data.encodedAuthority?.lowercase()?.removeSuffix(":443")
        val path = data.encodedPath.orEmpty()
        if (data.scheme != "https" || (authority != "recaptcha.net" && authority != "recaptcha.google.com") ||
            (path != "/qr" && !path.startsWith("/qr/"))) return null
        // Only forward the official app link. Web-supplied extras, targets and flags stay in the browser.
        return Intent(Intent.ACTION_VIEW, data).addCategory(Intent.CATEGORY_BROWSABLE).setPackage(PLAY_SERVICES)
    }
}
