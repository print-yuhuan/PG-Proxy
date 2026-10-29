package dev.pgproxy.app

/** Private application bridge; never exposed to page JavaScript or a network endpoint. */
interface BrowserLoginHost {
    /** Must complete on the main thread. A successful import is not a generation test. */
    fun importSession(
        accountId: String,
        label: String,
        sessionJson: String,
        callback: (Boolean, String?) -> Unit,
    )
}
