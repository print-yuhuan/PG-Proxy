package dev.pgproxy.app

import android.app.Application
import android.content.*
import android.os.*
import org.json.JSONObject
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicInteger

class PgApplication : Application(), BrowserLoginHost {
    val connector by lazy { ProxyConnector(this) }
    override fun importSession(accountId: String, label: String, sessionJson: String, callback: (Boolean, String?) -> Unit) {
        connector.call("import_account", JSONObject().put("id", accountId).put("label", label)
            .put("session", JSONObject(sessionJson))) { response ->
            callback(response.optBoolean("ok"), if (response.optBoolean("ok")) null else ErrorMessages.fromResponse(response))
        }
    }
}

/** Private Binder control channel, separate from authenticated public inference HTTP routes. */
class ProxyConnector(private val context: Context, private val timeoutMillis: Long = 300_000L) {
    private data class Pending(val callback: (JSONObject) -> Unit, val timeout: Runnable)
    private val main = Handler(Looper.getMainLooper())
    private val counter = AtomicInteger()
    private val requests = ConcurrentHashMap<Int, Pending>()
    // Binding and the unsent queue are accessed only on the main thread.
    private val queue = linkedMapOf<Int, Message>()
    private var remote: Messenger? = null
    private var connection: ServiceConnection? = null
    private val replies = Messenger(Handler(Looper.getMainLooper()) { message ->
        if (message.what != 200 || !requests.containsKey(message.arg1)) return@Handler true
        val result = try {
            JSONObject(message.data.getString("response", "{}")).also { body ->
                check(body.opt("ok") is Boolean)
                check(if (body.getBoolean("ok")) body.optJSONObject("result") != null else body.optJSONObject("error") != null)
            }
        } catch (_: Exception) { failure("service_invalid_response", "服务响应格式错误") }
        complete(message.arg1, result)
        true
    })

    /** Returns before the callback. Cancellation releases UI resources, not sent mutations. */
    fun call(action: String, payload: JSONObject = JSONObject(), callback: (JSONObject) -> Unit): Int {
        val id = counter.incrementAndGet()
        val serialized = payload.toString()
        val timeout = Runnable {
            complete(id, failure("service_timeout", "操作超时，请检查服务状态"))
            releaseUnconnectedBinding()
        }
        val pending = Pending(callback, timeout)
        requests[id] = pending
        main.post {
            if (requests[id] !== pending) return@post
            val actionLimit = if (action in setOf("snapshot", "network_info", "diagnostics")) 30_000L else 300_000L
            main.postDelayed(timeout, minOf(timeoutMillis, actionLimit).coerceAtLeast(1))
            if (requests[id] !== pending) { main.removeCallbacks(timeout); return@post }
            val message = Message.obtain(null, 100, id, 0).apply {
                data = Bundle().apply { putString("action", action); putString("payload", serialized) }
                replyTo = replies
            }
            if (remote != null) send(message) else {
                queue[id] = message
                ensureBinding()
            }
        }
        return id
    }

    fun cancel(id: Int) {
        val pending = requests.remove(id) ?: return
        main.removeCallbacks(pending.timeout)
        main.post { queue.remove(id); releaseUnconnectedBinding() }
    }

    private fun ensureBinding() {
        if (connection != null) return
        val next = object : ServiceConnection {
            override fun onServiceConnected(name: ComponentName, binder: IBinder) {
                if (connection !== this) return
                remote = Messenger(binder)
                val waiting = queue.values.toList()
                queue.clear()
                waiting.forEach(::send)
            }
            override fun onServiceDisconnected(name: ComponentName) {
                if (connection === this) failAll("service_disconnected", "服务已断开，请重试")
            }
            override fun onBindingDied(name: ComponentName) {
                if (connection === this) failAll("service_disconnected", "服务连接已失效，请重试")
            }
            override fun onNullBinding(name: ComponentName) {
                if (connection === this) failAll("service_bind_failed", "无法连接本机服务")
            }
        }
        connection = next
        try {
            if (!context.bindService(Intent(context, ProxyService::class.java), next, Context.BIND_AUTO_CREATE)) {
                failAll("service_bind_failed", "无法连接本机服务")
            }
        } catch (_: Exception) {
            failAll("service_bind_failed", "无法连接本机服务")
        }
    }

    private fun send(message: Message) {
        if (!requests.containsKey(message.arg1)) return
        try { remote?.send(message) ?: throw IllegalStateException() }
        catch (_: Exception) { failAll("service_disconnected", "服务已断开，请重试") }
    }

    private fun complete(id: Int, result: JSONObject) {
        val pending = requests.remove(id) ?: return
        main.removeCallbacks(pending.timeout)
        queue.remove(id)
        notify(pending, result)
    }

    private fun notify(pending: Pending, result: JSONObject) {
        try { pending.callback(result) }
        catch (error: Exception) { android.util.Log.e("PGProxy", "Control callback failed: ${error.javaClass.simpleName}") }
    }

    private fun releaseBinding() {
        val previous = connection
        connection = null
        remote = null
        if (previous != null) try { context.unbindService(previous) } catch (_: Exception) { }
    }

    private fun releaseUnconnectedBinding() {
        if (remote == null && requests.isEmpty()) releaseBinding()
    }

    private fun failAll(code: String, message: String) {
        releaseBinding()
        queue.clear()
        val waiting = requests.keys.toList().mapNotNull { requests.remove(it) }
        waiting.forEach { main.removeCallbacks(it.timeout) }
        val result = failure(code, message)
        waiting.forEach { notify(it, result) }
    }

    private fun failure(code: String, message: String) = JSONObject().put("ok", false)
        .put("error", JSONObject().put("code", code).put("message", message).put("status", 503))
}
