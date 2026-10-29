package dev.pgproxy.app

import android.app.*
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.*
import com.chaquo.python.PyObject
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import org.json.JSONObject

/** Null preserves the current notification until the active operation proves a new state. */
internal fun foregroundTarget(action: String, wasForeground: Boolean, currentEpoch: Boolean,
                              succeeded: Boolean, running: Boolean?): Boolean? {
    if (!currentEpoch) return null
    if (running == true) return true
    if (!succeeded) return if (action == "start" && !wasForeground) false else null
    return if (running == false) false else null
}

class ProxyService : Service() {
    private val executor = java.util.concurrent.ThreadPoolExecutor(2, 4, 30, java.util.concurrent.TimeUnit.SECONDS,
        java.util.concurrent.ArrayBlockingQueue(32))
    private val lifecycle = java.util.concurrent.ThreadPoolExecutor(1, 1, 0, java.util.concurrent.TimeUnit.SECONDS,
        java.util.concurrent.ArrayBlockingQueue(32))
    private val initialization = Any()
    private var controller: PyObject? = null
    private lateinit var bridge: NativeBridge
    private val main = Handler(Looper.getMainLooper())
    private var foreground = false
    private var lifecycleEpoch = 0
    private var lifecyclePending = 0
    private lateinit var wakeLease: ServiceWakeLease
    private var wakeProbeInFlight = false
    private val wakeProbeSchedule = WakeProbeSchedule(
        { task, delay -> main.postDelayed(task, delay) }, { main.removeCallbacks(it) }, { probeWakeLease() })
    @Volatile private var closing = false
    private val messenger = Messenger(Handler(Looper.getMainLooper()) { message ->
        if (message.what != 100 || message.replyTo == null) return@Handler true
        val id = message.arg1
        val reply = message.replyTo
        val action = message.data.getString("action")
        if (action.isNullOrBlank()) {
            respond(reply, id, failure("invalid_request", "操作参数无效"))
            return@Handler true
        }
        if (closing) {
            respond(reply, id, failure("service_closed", "服务已关闭，请重试"))
            return@Handler true
        }
        val payload = message.data.getString("payload", "{}")
        val changesLifecycle = action in listOf("start", "stop", "configure")
        val previousEpoch = lifecycleEpoch
        val wasForeground = foreground
        val epoch = if (changesLifecycle) ++lifecycleEpoch else lifecycleEpoch
        try {
            if (action == "start") showNotification()
        } catch (_: Exception) {
            if (changesLifecycle && lifecycleEpoch == epoch) lifecycleEpoch = previousEpoch
            if (action == "start" && !wasForeground) hideNotification()
            respond(reply, id, failure("foreground_unavailable", "无法启动前台服务，请返回应用后重试"))
            return@Handler true
        }
        val work = Runnable {
            val result = try {
                if (closing) failure("service_closed", "服务已关闭，请重试")
                else engine().callAttr("call", action, payload).toString()
            } catch (error: Exception) {
                // Do not include Python exception text: it can carry sensitive upstream context.
                android.util.Log.e("PGProxy", "Backend action failed: ${error.javaClass.simpleName}")
                failure("backend_unavailable", "服务运行异常，请查看诊断并重试")
            }
            main.post {
                if (changesLifecycle) lifecyclePending--
                respond(reply, id, reconcileState(action, epoch, wasForeground, result))
            }
        }
        if (changesLifecycle) lifecyclePending++
        // Snapshots share the mutation queue so they cannot return pre-configure state after it commits.
        try { if (changesLifecycle || action == "snapshot") lifecycle.execute(work) else executor.execute(work) }
        catch (_: java.util.concurrent.RejectedExecutionException) {
            if (changesLifecycle) lifecyclePending--
            if (changesLifecycle && lifecycleEpoch == epoch) lifecycleEpoch = previousEpoch
            if (action == "start" && !wasForeground) hideNotification()
            respond(reply, id, failure("service_busy", "服务正在忙，请稍后重试"))
        }
        true
    })

    override fun onCreate() {
        super.onCreate()
        bridge = NativeBridge(this)
        wakeLease = ServiceWakeLease.forContext(this)
        getSystemService(NotificationManager::class.java).createNotificationChannel(
            NotificationChannel("proxy", "本机 API 服务", NotificationManager.IMPORTANCE_LOW))
    }

    private fun engine(): PyObject = synchronized(initialization) {
        check(!closing) { "Service is closing" }
        controller ?: run {
            if (!Python.isStarted()) Python.start(AndroidPlatform(this))
            Python.getInstance().getModule("pgproxy.android_host").callAttr("Controller", bridge).also { controller = it }
        }
    }

    override fun onBind(intent: Intent): IBinder = messenger.binder

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == "stop" && !closing) {
            val previousEpoch = lifecycleEpoch
            val epoch = ++lifecycleEpoch
            val wasForeground = foreground
            lifecyclePending++
            try { lifecycle.execute {
                val result = try { engine().callAttr("call", "stop", "{}").toString() }
                catch (error: Exception) {
                    android.util.Log.e("PGProxy", "Notification stop failed: ${error.javaClass.simpleName}")
                    failure("backend_unavailable", "停止服务尚未完成，请返回应用重试")
                }
                main.post {
                    lifecyclePending--
                    reconcileState("stop", epoch, wasForeground, result)
                }
            } } catch (_: java.util.concurrent.RejectedExecutionException) {
                lifecyclePending--
                if (lifecycleEpoch == epoch) lifecycleEpoch = previousEpoch
            }
        }
        return START_NOT_STICKY
    }

    private fun reconcileState(action: String, epoch: Int, wasForeground: Boolean, result: String): String {
        try {
            val response = JSONObject(result)
            val snapshot = response.optJSONObject("result")
            val running = snapshot?.opt("running") as? Boolean
            val current = epoch == lifecycleEpoch && !closing
            val succeeded = response.opt("ok") == true
            val lifecycleAction = action in listOf("start", "stop", "configure")
            if (lifecycleAction) {
                when (foregroundTarget(action, wasForeground, current, succeeded, running)) {
                    true -> showNotification()
                    false -> hideNotification()
                    null -> Unit
                }
            }
            val keepAwake = snapshot?.opt("keepAwake") as? Boolean
            if (current && succeeded && lifecyclePending == 0 && running != null && keepAwake != null &&
                (lifecycleAction || action == "snapshot")) {
                val previous = Pair(wakeLease.isHeld, wakeLease.error)
                wakeLease.reconcile(running && keepAwake && foreground, retry = lifecycleAction)
                // The backend can stop unexpectedly while no activity is polling it.
                if (!running && foreground) hideNotification()
                else if (foreground && previous != Pair(wakeLease.isHeld, wakeLease.error)) showNotification()
                scheduleWakeProbe()
            }
            if (running != null) {
                snapshot.put("wakeLockHeld", wakeLease.isHeld).put("wakeLockError", wakeLease.error)
            }
            return response.toString()
        } catch (_: Exception) {
            // Unknown state must not remove foreground protection or extend a wake lease.
            return result
        }
    }

    private fun scheduleWakeProbe() {
        if (closing || !foreground || !wakeLease.requested) wakeProbeSchedule.cancel()
        else if (!wakeProbeInFlight) wakeProbeSchedule.schedule()
    }

    /** Revalidate off the UI thread before renewing; a hung backend cannot renew the lock forever. */
    private fun probeWakeLease() {
        if (closing || !foreground || !wakeLease.requested || wakeProbeInFlight) return
        if (lifecyclePending != 0) { scheduleWakeProbe(); return }
        wakeProbeInFlight = true
        val epoch = lifecycleEpoch
        try { lifecycle.execute {
            val result = try { engine().callAttr("call", "snapshot", "{}").toString() }
            catch (_: Exception) { failure("backend_unavailable", "无法确认服务状态") }
            main.post {
                wakeProbeInFlight = false
                reconcileState("snapshot", epoch, foreground, result)
                scheduleWakeProbe()
            }
        } } catch (_: java.util.concurrent.RejectedExecutionException) {
            wakeProbeInFlight = false
            scheduleWakeProbe()
        }
    }

    private fun showNotification() {
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        val stop = PendingIntent.getService(this, 1, Intent(this, ProxyService::class.java).setAction("stop"), PendingIntent.FLAG_IMMUTABLE)
        val notification = Notification.Builder(this, "proxy")
            .setSmallIcon(R.drawable.ic_pgproxy_notification).setContentTitle("PG Proxy 正在运行")
            .setContentText(when {
                wakeLease.isHeld -> "保持 CPU 唤醒 · 点击管理服务"
                wakeLease.error.isNotBlank() -> "保持唤醒未生效 · 点击查看设置"
                else -> "点击管理服务与凭证"
            }).setContentIntent(open).setOngoing(true)
            .addAction(Notification.Action.Builder(null, "停止服务", stop).build()).build()
        if (Build.VERSION.SDK_INT >= 34) startForeground(1, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        else startForeground(1, notification)
        // Retain the started service after the last UI binding is removed.
        if (!foreground) startService(Intent(this, ProxyService::class.java))
        foreground = true
    }

    private fun hideNotification() {
        wakeLease.reconcile(false)
        wakeProbeSchedule.cancel()
        stopForeground(STOP_FOREGROUND_REMOVE)
        foreground = false
        stopSelf()
    }

    override fun onDestroy() {
        closing = true
        wakeProbeSchedule.cancel()
        wakeLease.close()
        // Serializes cleanup against in-flight initialization without doing blocking work on the UI thread.
        Thread({
            try { synchronized(initialization) { controller }?.callAttr("close") }
            catch (_: Exception) { }
            finally { bridge.stopAll() }
        }, "pg-service-cleanup").start()
        lifecycle.shutdown()
        executor.shutdown()
        super.onDestroy()
    }

    private fun respond(reply: Messenger, id: Int, result: String) {
        try { reply.send(Message.obtain(null, 200, id, 0).apply { data = Bundle().apply { putString("response", result) } }) }
        catch (_: Exception) { }
    }

    private fun failure(code: String, message: String): String = JSONObject().put("ok", false)
        .put("error", JSONObject().put("code", code).put("message", message).put("status", 503)).toString()
}
