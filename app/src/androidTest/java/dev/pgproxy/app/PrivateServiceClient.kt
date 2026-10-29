package dev.pgproxy.app

import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.os.Bundle
import android.os.Handler
import android.os.HandlerThread
import android.os.IBinder
import android.os.Message
import android.os.Messenger
import org.json.JSONObject
import java.io.Closeable
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.CountDownLatch
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

/** Exercises the same non-exported Binder endpoint as the app UI. */
internal class PrivateServiceClient(private val context: Context) : Closeable {
    private val thread = HandlerThread("pg-instrumentation-replies").apply { start() }
    private val pending = ConcurrentHashMap<Int, LinkedBlockingQueue<String>>()
    private val ids = AtomicInteger()
    private val connected = CountDownLatch(1)
    private var service: Messenger? = null
    private val reply = Messenger(Handler(thread.looper) { message ->
        if (message.what == 200) message.data.getString("response")?.let {
            pending[message.arg1]?.offer(it)
        }
        true
    })
    private val connection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName?, binder: IBinder?) {
            service = Messenger(binder)
            connected.countDown()
        }
        override fun onServiceDisconnected(name: ComponentName?) { disconnected() }
        override fun onBindingDied(name: ComponentName?) { disconnected() }
        override fun onNullBinding(name: ComponentName?) { disconnected() }
    }
    private var bound = false

    init {
        try {
            bound = context.bindService(Intent(context, ProxyService::class.java), connection, Context.BIND_AUTO_CREATE)
            check(bound && connected.await(60, TimeUnit.SECONDS) && service != null) { "Private service binding failed" }
        } catch (error: Exception) {
            close()
            throw error
        }
    }

    private fun disconnected() {
        service = null
        connected.countDown()
        val failure = "{\"ok\":false,\"error\":{\"code\":\"service_disconnected\"}}"
        pending.values.forEach { it.offer(failure) }
    }

    fun call(action: String, payload: JSONObject = JSONObject(), timeoutSeconds: Long = 240): JSONObject {
        val id = ids.incrementAndGet()
        val queue = LinkedBlockingQueue<String>(1)
        pending[id] = queue
        try {
            checkNotNull(service) { "Private service disconnected" }.send(Message.obtain(null, 100, id, 0).apply {
                replyTo = reply
                data = Bundle().apply { putString("action", action); putString("payload", payload.toString()) }
            })
            return JSONObject(checkNotNull(queue.poll(timeoutSeconds, TimeUnit.SECONDS)) { "Private service request timed out" })
        } finally { pending.remove(id) }
    }

    fun result(action: String, payload: JSONObject = JSONObject(), timeoutSeconds: Long = 240): JSONObject {
        val response = call(action, payload, timeoutSeconds)
        check(response.optBoolean("ok")) {
            // Error codes are safe metadata; never include response bodies or session values.
            "Private action failed: $action (${response.optJSONObject("error")?.optString("code") ?: "unknown"})"
        }
        return response.getJSONObject("result")
    }

    override fun close() {
        if (bound) { runCatching { context.unbindService(connection) }; bound = false }
        pending.clear()
        thread.quitSafely()
        thread.join(1000)
    }
}
