package dev.pgproxy.app

import android.content.ComponentName
import android.content.Context
import android.content.ContextWrapper
import android.content.Intent
import android.content.ServiceConnection
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.Message
import android.os.Messenger
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import java.util.concurrent.CountDownLatch
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

/** Exercises local Binder/Handler state only: never binds ProxyService or reaches any API. */
@RunWith(AndroidJUnit4::class)
class ProxyConnectorInstrumentedTest {
    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val target = instrumentation.targetContext

    @Test fun completedRepliesRemoveTheirTimeoutAndAlwaysUseTheMainThread() {
        val context = FakeServiceContext(target)
        val connector = ProxyConnector(context, 10_000)
        val completed = CountDownLatch(1)
        var onMain = false
        var successful = false
        val id = call(connector) {
            onMain = Looper.myLooper() == Looper.getMainLooper()
            successful = it.optBoolean("ok")
            completed.countDown()
        }
        try {
            val request = context.request()
            val timeout = pendingTimeout(connector, id)
            context.respond(request)
            assertTrue(completed.await(5, TimeUnit.SECONDS))
            assertTrue(onMain)
            assertTrue(successful)
            instrumentation.waitForIdleSync()
            assertTrue(pending(connector).isEmpty())
            if (Build.VERSION.SDK_INT >= 29) assertFalse(handler(connector).hasCallbacks(timeout))
        } finally { connector.cancel(id); context.disconnect() }
    }

    @Test fun cancellationDropsUnsentRequestsAndIgnoresALateOldConnection() {
        val context = FakeServiceContext(target).apply { autoConnect = false }
        val connector = ProxyConnector(context, 10_000)
        val callbacks = AtomicInteger()
        val id = call(connector, "import_account") { callbacks.incrementAndGet() }
        instrumentation.waitForIdleSync()
        val previous = checkNotNull(context.lastConnection)
        connector.cancel(id)
        instrumentation.waitForIdleSync()
        instrumentation.runOnMainSync { previous.onServiceConnected(context.component, context.endpoint.binder) }
        instrumentation.waitForIdleSync()
        assertNull("Cancelled queued mutation must not be sent", context.incoming.poll())
        assertEquals(0, callbacks.get())
        assertTrue(pending(connector).isEmpty())
        assertEquals(1, context.unbinds.get())

        context.autoConnect = true
        val retried = CountDownLatch(1)
        val retryId = call(connector) { if (it.optBoolean("ok")) retried.countDown() }
        try {
            context.respond(context.request())
            assertTrue(retried.await(5, TimeUnit.SECONDS))
            assertEquals(2, context.binds.get())
        } finally { connector.cancel(retryId); context.disconnect() }
    }

    @Test fun cancellationOfSentWorkReleasesOnlyItsCallback() {
        val context = FakeServiceContext(target)
        val connector = ProxyConnector(context, 10_000)
        val callbacks = AtomicInteger()
        val id = call(connector, "configure") { callbacks.incrementAndGet() }
        try {
            val accepted = context.request()
            val timeout = pendingTimeout(connector, id)
            connector.cancel(id)
            context.respond(accepted)
            instrumentation.waitForIdleSync()
            assertEquals("Late replies cannot call a released screen", 0, callbacks.get())
            assertTrue(pending(connector).isEmpty())
            if (Build.VERSION.SDK_INT >= 29) assertFalse(handler(connector).hasCallbacks(timeout))
            assertEquals("Cancellation must not send a rollback or another mutation", 1, context.received.get())
        } finally { connector.cancel(id); context.disconnect() }
    }

    @Test fun stalledBindingTimesOutAndTheNextCallCanBindAgain() {
        val context = FakeServiceContext(target).apply { autoConnect = false }
        val connector = ProxyConnector(context, 100)
        val completed = CountDownLatch(1)
        var error = ""
        val id = call(connector) { error = it.optJSONObject("error")?.optString("code").orEmpty(); completed.countDown() }
        try {
            assertTrue(completed.await(5, TimeUnit.SECONDS))
            instrumentation.waitForIdleSync()
            assertEquals("service_timeout", error)
            assertTrue(pending(connector).isEmpty())
            assertEquals(1, context.unbinds.get())
            context.autoConnect = true
            context.autoRespond = true
            val retried = CountDownLatch(1)
            val retryId = call(connector) { if (it.optBoolean("ok")) retried.countDown() }
            try { assertTrue(retried.await(5, TimeUnit.SECONDS)) }
            finally { connector.cancel(retryId) }
            assertEquals(2, context.binds.get())
        } finally { connector.cancel(id); context.disconnect() }
    }

    @Test fun bindingExceptionsAndMalformedRepliesBecomeStableErrors() {
        val context = FakeServiceContext(target).apply { throwOnBind = true }
        val connector = ProxyConnector(context, 10_000)
        val failed = CountDownLatch(1)
        var code = ""
        val first = call(connector) { code = it.optJSONObject("error")?.optString("code").orEmpty(); failed.countDown() }
        assertTrue(failed.await(5, TimeUnit.SECONDS))
        assertEquals("service_bind_failed", code)
        assertTrue(pending(connector).isEmpty())
        context.throwOnBind = false
        val malformed = CountDownLatch(1)
        val second = call(connector) { code = it.optJSONObject("error")?.optString("code").orEmpty(); malformed.countDown() }
        try {
            context.respond(context.request(), "{invalid-json")
            assertTrue(malformed.await(5, TimeUnit.SECONDS))
            assertEquals("service_invalid_response", code)
            assertTrue(pending(connector).isEmpty())
        } finally { connector.cancel(first); connector.cancel(second); context.disconnect() }
    }

    @Test fun aDeadBindingFailsEveryPendingRequestAndAllowsRecovery() {
        val context = FakeServiceContext(target)
        val connector = ProxyConnector(context, 10_000)
        val failed = CountDownLatch(2)
        val errors = LinkedBlockingQueue<String>()
        val first = call(connector) { errors.offer(it.optJSONObject("error")?.optString("code").orEmpty()); failed.countDown() }
        val second = call(connector) { errors.offer(it.optJSONObject("error")?.optString("code").orEmpty()); failed.countDown() }
        try {
            context.request(); context.request()
            context.disconnect()
            assertTrue(failed.await(5, TimeUnit.SECONDS))
            assertEquals(listOf("service_disconnected", "service_disconnected"), listOf(errors.poll(), errors.poll()))
            assertTrue(pending(connector).isEmpty())
            val retried = CountDownLatch(1)
            val retryId = call(connector) { if (it.optBoolean("ok")) retried.countDown() }
            try { context.respond(context.request()); assertTrue(retried.await(5, TimeUnit.SECONDS)) }
            finally { connector.cancel(retryId) }
        } finally { connector.cancel(first); connector.cancel(second); context.disconnect() }
    }

    private fun call(connector: ProxyConnector, action: String = "snapshot", callback: (JSONObject) -> Unit): Int {
        var id = 0
        instrumentation.runOnMainSync { id = connector.call(action, JSONObject(), callback) }
        return id
    }

    private fun pending(connector: ProxyConnector): Map<*, *> = connector.javaClass.getDeclaredField("requests")
        .apply { isAccessible = true }.get(connector) as Map<*, *>

    private fun pendingTimeout(connector: ProxyConnector, id: Int): Runnable {
        val request = checkNotNull(pending(connector)[id])
        return request.javaClass.getDeclaredField("timeout").apply { isAccessible = true }.get(request) as Runnable
    }

    private fun handler(connector: ProxyConnector): Handler = connector.javaClass.getDeclaredField("main")
        .apply { isAccessible = true }.get(connector) as Handler

    private inner class FakeServiceContext(base: Context) : ContextWrapper(base) {
        val component = ComponentName(base, ProxyService::class.java)
        val incoming = LinkedBlockingQueue<Message>()
        val binds = AtomicInteger()
        val unbinds = AtomicInteger()
        val received = AtomicInteger()
        @Volatile var autoConnect = true
        @Volatile var autoRespond = false
        @Volatile var throwOnBind = false
        @Volatile var lastConnection: ServiceConnection? = null
        val endpoint = Messenger(Handler(Looper.getMainLooper()) { message ->
            val copy = Message.obtain(message)
            received.incrementAndGet()
            if (autoRespond) respond(copy) else incoming.offer(copy)
            true
        })

        override fun bindService(service: Intent, conn: ServiceConnection, flags: Int): Boolean {
            binds.incrementAndGet()
            if (throwOnBind) throw SecurityException("Synthetic binding failure")
            lastConnection = conn
            if (autoConnect) conn.onServiceConnected(component, endpoint.binder)
            return true
        }

        override fun unbindService(conn: ServiceConnection) {
            unbinds.incrementAndGet()
            if (lastConnection === conn) lastConnection = null
        }

        fun request(): Message = checkNotNull(incoming.poll(5, TimeUnit.SECONDS)) { "Local fake service did not receive a request" }

        fun respond(request: Message, body: String = "{\"ok\":true,\"result\":{}}") {
            request.replyTo.send(Message.obtain(null, 200, request.arg1, 0).apply {
                data = Bundle().apply { putString("response", body) }
            })
            request.recycle()
        }

        fun disconnect() {
            instrumentation.runOnMainSync { lastConnection?.onBindingDied(component) }
            while (true) (incoming.poll() ?: break).recycle()
        }
    }
}
