package dev.pgproxy.app

import android.app.Activity
import android.app.AlertDialog
import android.app.Application
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.WindowInsets
import android.widget.FrameLayout
import org.json.JSONObject
import org.mozilla.geckoview.AllowOrDeny
import org.mozilla.geckoview.GeckoResult
import org.mozilla.geckoview.GeckoRuntime
import org.mozilla.geckoview.GeckoRuntimeSettings
import org.mozilla.geckoview.GeckoSession
import org.mozilla.geckoview.GeckoSessionSettings
import org.mozilla.geckoview.GeckoView
import org.mozilla.geckoview.WebExtension
import org.mozilla.geckoview.WebRequestError

/** One complete Firefox profile per account, one runtime per disposable :login process. */
class GeckoLoginActivity : Activity() {
    companion object {
        const val HOME = "https://aistudio.google.com/prompts/new_chat"
        const val EXTRA_ACCOUNT_ID = "accountId"
        const val EXTRA_LABEL = "label"
        const val EXTRA_EXISTING_ACCOUNT = "existingAccount"
        private const val EXTENSION = "session-bridge@pgproxy.local"
        private var runtimeCreated = false

        internal fun loginSessionSettings(): GeckoSessionSettings = GeckoSessionSettings.Builder().build()
    }
    private val main = Handler(Looper.getMainLooper())
    private val sessions = mutableListOf<GeckoSession>()
    private var runtime: GeckoRuntime? = null
    private var session: GeckoSession? = null
    private var view: GeckoView? = null
    private var lease: GeckoProfiles.Lease? = null
    private var accountId = ""
    private var label = ""
    private var page = ""
    private var epoch = 0
    private var loaded = false
    private var secure = false
    private var secureHost = ""
    private var active = false
    private var importing = false
    private var imported = false
    private var closing = false
    private var errorShown = false
    private var canGoBack = false
    private var runtimeExited = false
    private var ownsRuntime = false
    private var extension: WebExtension? = null
    private var errorDialog: AlertDialog? = null
    private val deletionWatch = object : Runnable {
        override fun run() {
            if (closing) return
            if (GeckoProfiles.isDeleted(this@GeckoLoginActivity, accountId)) finish()
            else main.postDelayed(this, 1500)
        }
    }

    override fun onCreate(state: Bundle?) {
        super.onCreate(state)
        accountId = intent.getStringExtra(EXTRA_ACCOUNT_ID).orEmpty()
        label = intent.getStringExtra(EXTRA_LABEL).orEmpty().take(80).ifBlank { "Google 凭证" }
        if (!GeckoProfiles.validAccountId(accountId) || application !is BrowserLoginHost ||
            Application.getProcessName() != "$packageName:login" || runtimeCreated) {
            super.finish()
            return
        }
        val root = FrameLayout(this).apply {
            setBackgroundColor(0xfff6f8fc.toInt())
            setOnApplyWindowInsetsListener { v, insets ->
                if (android.os.Build.VERSION.SDK_INT >= 30) {
                    val bars = insets.getInsets(WindowInsets.Type.systemBars() or WindowInsets.Type.displayCutout())
                    v.setPadding(bars.left, bars.top, bars.right, bars.bottom)
                } else {
                    @Suppress("DEPRECATION")
                    v.setPadding(insets.systemWindowInsetLeft, insets.systemWindowInsetTop,
                        insets.systemWindowInsetRight, insets.systemWindowInsetBottom)
                }
                insets
            }
        }
        setContentView(root)
        stage("opening")
        try {
            if (intent.getBooleanExtra(EXTRA_EXISTING_ACCOUNT, false) && !GeckoProfiles.confirmImport(this, accountId)) {
                fail("登录会话不可用", "该凭证已标记删除或无法保存会话状态，请返回凭证页检查。")
                return
            }
            lease = GeckoProfiles.acquire(this, accountId, label)
            ownsRuntime = true
            runtimeCreated = true
            runtime = GeckoRuntime.create(applicationContext, GeckoRuntimeSettings.Builder()
                .arguments(arrayOf("-profile", lease!!.profile.absolutePath))
                .configFilePath("").consoleOutput(false).remoteDebuggingEnabled(false).build())
            runtime!!.setDelegate { runtimeExited = true; stopLoginProcess() }
            view = GeckoView(this)
            root.addView(view, FrameLayout.LayoutParams(-1, -1))
            session = newSession()
            session!!.open(runtime!!)
            view!!.setSession(session!!)
            runtime!!.webExtensionController.ensureBuiltIn("resource://android/assets/gecko-bridge/", EXTENSION)
                .accept({ installed ->
                    if (closing || isFinishing || isDestroyed) return@accept
                    if (installed == null) {
                        fail("浏览器初始化失败", "请返回后重新打开 Firefox 登录页。")
                        return@accept
                    }
                    extension = installed
                    installed.setMessageDelegate(object : WebExtension.MessageDelegate {
                        override fun onMessage(nativeApp: String, raw: Any, sender: WebExtension.MessageSender): GeckoResult<Any>? {
                            if (closing || isFinishing || isDestroyed) return null
                            if (nativeApp != "pgproxy_bridge" || sender.webExtension.id != EXTENSION || sender.session != null ||
                                sender.environmentType != WebExtension.MessageSender.ENV_TYPE_EXTENSION || raw !is JSONObject) return null
                            if (raw.optString("op") == "status") return GeckoResult.fromValue(JSONObject()
                                .put("eligible", eligible()).put("epoch", epoch).toString() as Any)
                            if (raw.optString("op") == "account_selection_error" && eligible()) {
                                stage("multiple_google_accounts")
                                fail("请使用独立账号会话", "请返回凭证页，通过“添加凭证”单独登录另一个 Google 账号。")
                            }
                            if (raw.optString("op") == "session" && raw.optInt("epoch", -1) == epoch && eligible()) {
                                runCatching { deliver(raw) }.onFailure {
                                    importing = false
                                    stage("capture_error")
                                    fail("会话读取失败", "未能读取完整登录会话，请返回后重试。")
                                }
                            }
                            return null
                        }
                    }, "pgproxy_bridge")
                    stage("bridge_ready")
                    session!!.loadUri(HOME)
                }, { fail("浏览器初始化失败", "请返回后重新打开 Firefox 登录页。") })
            main.post(deletionWatch)
        } catch (_: Exception) {
            fail("登录页暂时无法打开", "该账号可能正在另一个登录页面使用，请关闭后重试。")
        }
    }

    private fun newSession(): GeckoSession {
        val next = GeckoSession(loginSessionSettings())
        sessions.add(next)
        next.navigationDelegate = object : GeckoSession.NavigationDelegate {
            override fun onLocationChange(s: GeckoSession, url: String?, permissions: MutableList<GeckoSession.PermissionDelegate.ContentPermission>, gesture: Boolean) {
                if (!acceptsPageEvents(s)) return
                page = url.orEmpty()
                val uri = Uri.parse(page)
                if (uri.host == "accounts.google.com" &&
                    (uri.path.orEmpty().contains("rejected", true) || uri.getQueryParameter("error") == "disallowed_useragent")) {
                    stage("google_rejected")
                    fail("Google 未接受当前登录环境", "Google 拒绝了当前登录环境。请返回并保留错误页面供诊断，已有登录会话会保留。")
                }
            }
            override fun onCanGoBack(s: GeckoSession, value: Boolean) { if (acceptsPageEvents(s)) canGoBack = value }
            override fun onLoadRequest(s: GeckoSession, request: GeckoSession.NavigationDelegate.LoadRequest): GeckoResult<AllowOrDeny> {
                if (importing || imported || closing || isFinishing || isDestroyed) return GeckoResult.fromValue(AllowOrDeny.DENY)
                val permitted = allowed(request.uri)
                if (!permitted) recordDenied(request.uri)
                return GeckoResult.fromValue(if (permitted) AllowOrDeny.ALLOW else AllowOrDeny.DENY)
            }
            override fun onLoadError(s: GeckoSession, uri: String?, error: WebRequestError): GeckoResult<String>? {
                if (acceptsPageEvents(s)) {
                    loaded = false
                    secure = false
                    stage("page_load_error")
                    GeckoProfiles.recordLoadError(this@GeckoLoginActivity, error.category, error.code)
                    showLoadError(uri, error)
                }
                // Never install certificate exceptions or retry without HTTPS verification.
                return null
            }
            override fun onNewSession(old: GeckoSession, uri: String): GeckoResult<GeckoSession>? {
                if (!acceptsPageEvents(old)) return null
                if (!allowed(uri)) { recordDenied(uri); return null }
                val popup = newSession()
                session = popup
                loaded = false; secure = false; epoch++
                main.post { if (!closing && !isFinishing && !isDestroyed) view?.setSession(popup) }
                return GeckoResult.fromValue(popup)
            }
        }
        next.progressDelegate = object : GeckoSession.ProgressDelegate {
            override fun onPageStart(s: GeckoSession, url: String) { if (acceptsPageEvents(s)) { loaded = false; secure = false; epoch++; stage("loading_page") } }
            override fun onPageStop(s: GeckoSession, success: Boolean) {
                if (acceptsPageEvents(s)) {
                    loaded = success
                    if (!errorShown) stage(if (!success) "page_error" else if (Uri.parse(page).host == "aistudio.google.com") "waiting_for_prompt" else "waiting_for_google_login")
                }
            }
            override fun onSecurityChange(s: GeckoSession, info: GeckoSession.ProgressDelegate.SecurityInformation) {
                if (acceptsPageEvents(s)) { secure = info.isSecure && !info.isException; secureHost = info.host.orEmpty() }
            }
        }
        return next
    }

    // Import has its own acknowledged result. Late redirects/errors must not replace it
    // with a browser failure or overwrite the completed diagnostic stage.
    private fun acceptsPageEvents(source: GeckoSession): Boolean = source === session &&
        !importing && !imported && !closing && !isFinishing && !isDestroyed

    private fun allowed(url: String): Boolean = LoginNavigationPolicy.allows(url)

    private fun recordDenied(url: String) {
        val uri = Uri.parse(url)
        val host = uri.host.orEmpty().lowercase()
        val googleHost = host == "google.com" || host.endsWith(".google.com")
        val reason = when {
            uri.scheme != "https" -> "scheme"
            uri.userInfo != null -> "userinfo"
            uri.port != -1 && uri.port != 443 -> "port"
            !googleHost -> "host"
            else -> "unknown"
        }
        GeckoProfiles.recordDeniedNavigation(this, host, reason, googleHost)
    }

    private fun showLoadError(uri: String?, error: WebRequestError) {
        if (errorShown || importing || imported || closing || isFinishing || isDestroyed) return
        errorShown = true
        val retry = uri?.takeIf(::allowed)
        val message = if (error.category == WebRequestError.ERROR_CATEGORY_SECURITY)
            "网站安全连接验证未通过。重试仍会验证证书，不会忽略安全错误。"
        else "页面未能完成加载。登录会话已保留，可以重试或稍后从凭证页继续。"
        errorDialog = AlertDialog.Builder(this).setTitle("页面暂时无法打开")
            .setMessage(message + "\n错误分类 ${error.category} · 错误代码 ${error.code}")
            .setPositiveButton(if (retry != null) "重试当前页面" else "打开 Playground") { _, _ ->
                errorShown = false
                session?.loadUri(retry ?: HOME)
            }
            .setNeutralButton("打开 Playground") { _, _ ->
                errorShown = false
                session?.loadUri(HOME)
            }
            .setNegativeButton("返回") { _, _ -> finish() }
            .setOnCancelListener { finish() }.show()
    }

    private fun eligible(): Boolean {
        if (closing || errorShown || !active || !loaded || !secure || importing || imported || GeckoProfiles.isDeleted(this, accountId)) return false
        val uri = Uri.parse(page)
        return uri.scheme == "https" && uri.host == "aistudio.google.com" && secureHost == "aistudio.google.com" &&
            uri.path.orEmpty().startsWith("/prompts/") && uri.getQueryParameter("authuser").let { it == null || it == "0" }
    }

    private fun deliver(message: JSONObject) {
        val payload = JSONObject().put("cookies", message.getJSONArray("cookies"))
            .put("origins", message.optJSONArray("origins"))
            .put("browserMetadata", message.optJSONObject("browserMetadata"))
        val json = payload.toString()
        if (json.toByteArray(Charsets.UTF_8).size > 512 * 1024 || !GeckoProfiles.markImportPending(this, accountId)) {
            fail("无法保存导入状态", "请检查应用存储空间后重试。")
            return
        }
        importing = true
        stage("importing")
        (application as BrowserLoginHost).importSession(accountId, label, json) { success, messageText ->
            if (success) GeckoProfiles.confirmImport(this, accountId)
            if (isDestroyed || closing) return@importSession
            importing = false
            if (GeckoProfiles.isDeleted(this, accountId)) { finish(); return@importSession }
            if (success) {
                imported = true
                stage("imported")
                setResult(RESULT_OK, Intent().putExtra(EXTRA_ACCOUNT_ID, accountId))
                finish()
            } else {
                loaded = false
                stage("import_error")
                fail("导入结果尚未确认", (messageText ?: "本机服务暂时无法确认导入结果。") + "\n请返回凭证页查看，浏览器会话会保留。")
            }
        }
    }

    private fun stage(value: String) = GeckoProfiles.recordStage(this, value)

    private fun fail(title: String, text: String) {
        if (errorShown || imported || closing || isFinishing || isDestroyed) return
        errorShown = true
        errorDialog = AlertDialog.Builder(this).setTitle(title).setMessage(text).setPositiveButton("返回") { _, _ -> finish() }
            .setOnCancelListener { finish() }.show()
    }
    override fun onResume() { super.onResume(); active = true }
    override fun onPause() { active = false; super.onPause() }
    @Deprecated("Deprecated in Java")
    override fun onBackPressed() {
        // The durable import journal lets the main activity reconcile a late IPC result.
        if (importing) finish() else if (canGoBack) session?.goBack() else finish()
    }

    override fun onDestroy() {
        // A duplicate launch must not terminate the already active login activity/runtime.
        if (runtimeCreated && !ownsRuntime) {
            super.onDestroy()
            return
        }
        main.removeCallbacksAndMessages(null)
        closing = true
        runCatching { errorDialog?.dismiss() }
        errorDialog = null
        runCatching { extension?.setMessageDelegate(null, "pgproxy_bridge") }
        extension = null
        runCatching { view?.releaseSession() }
        view = null
        sessions.forEach { item ->
            item.navigationDelegate = null
            item.progressDelegate = null
            runCatching { item.close() }
        }
        sessions.clear()
        session = null
        // Preserve unfinished login state so the account page can resume the same profile.
        if (runtime != null) runCatching { runtime!!.shutdown() }
        // A runtime cannot be reinitialized with a different profile in the same process.
        main.postDelayed({ stopLoginProcess() }, 1500)
        super.onDestroy()
    }
    private fun stopLoginProcess() {
        // Only a confirmed Gecko shutdown permits erasing a profile. On forced exit the OS
        // releases the lease; the parent UI later retries its durable cleanup marker.
        if (runtimeExited || runtime == null) {
            runCatching { lease?.close() }
            lease = null
            if (GeckoProfiles.validAccountId(accountId) && GeckoProfiles.isDeleted(this, accountId)) GeckoProfiles.delete(this, accountId)
        }
        if (Application.getProcessName() == "$packageName:login") android.os.Process.killProcess(android.os.Process.myPid())
    }
}
