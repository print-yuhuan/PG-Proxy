package dev.pgproxy.app

import java.net.URI

/** Top-level login navigation only; credential capture still requires AI Studio. */
internal object LoginNavigationPolicy {
    fun allows(url: String): Boolean = runCatching {
        val uri = URI(url)
        val host = uri.host?.lowercase().orEmpty()
        // Google sign-in performs a cross-domain account handoff after two-step
        // verification. This exact host does not permit general YouTube browsing.
        uri.scheme == "https" && (uri.port == -1 || uri.port == 443) && uri.rawUserInfo == null &&
            (host == "google.com" || host.endsWith(".google.com") || host == "accounts.youtube.com")
    }.getOrDefault(false)
}
