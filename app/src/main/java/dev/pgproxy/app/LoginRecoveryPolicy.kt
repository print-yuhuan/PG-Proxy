package dev.pgproxy.app

import java.net.URI

/** Main-thread state for one automatic recovery during one login activity's lifetime. */
internal class LoginRecoveryPolicy {
    private var observedLogin = false
    private var attemptedRecovery = false
    private var disabled = false

    var isRecovering = false
        private set

    /** Only trusted account navigation is evidence of a sign-in flow. */
    fun observeNavigation(uri: String?) {
        if (disabled || uri == null || !LoginNavigationPolicy.allows(uri)) return
        val host = URI(uri).host?.lowercase()
        if (host == "accounts.google.com" || host == "accounts.youtube.com") observedLogin = true
    }

    /**
     * The caller must identify the exact connection-reset category and code.
     * Consume the budget before posting navigation so repeated error callbacks
     * cannot enqueue another recovery. The recovery target is chosen by the caller.
     */
    fun tryStartRecovery(uri: String?, isConnectionReset: Boolean): Boolean {
        if (disabled || attemptedRecovery || !observedLogin || !isConnectionReset ||
            uri == null || !LoginNavigationPolicy.allows(uri)) return false
        attemptedRecovery = true
        isRecovering = true
        return true
    }

    /** Success, failure and manual retry end recovery without replenishing its budget. */
    fun markRecoveryFinished() {
        isRecovering = false
    }

    /** Import and shutdown are terminal: late browser callbacks cannot restart recovery. */
    fun disable() {
        disabled = true
        isRecovering = false
    }
}
