package dev.pgproxy.app

import android.content.Context
import android.os.PowerManager
import android.os.SystemClock

internal interface CpuWakeLock {
    val isHeld: Boolean
    fun acquire(timeoutMillis: Long)
    fun release()
}

/** One pending deadline: frequent UI snapshots must not postpone background revalidation. */
internal class WakeProbeSchedule(
    private val post: (Runnable, Long) -> Boolean,
    private val remove: (Runnable) -> Unit,
    private val probe: () -> Unit,
) {
    private var queued = false
    private val task = Runnable {
        queued = false
        probe()
    }

    fun schedule() {
        if (!queued) queued = post(task, ServiceWakeLease.RENEW_MILLIS)
    }

    fun cancel() {
        remove(task)
        queued = false
    }
}

/** Main-thread state. Every acquisition has an OS timeout, including after an unexpected failure. */
internal class ServiceWakeLease(
    private val createLock: () -> CpuWakeLock,
    private val now: () -> Long = SystemClock::elapsedRealtime,
) {
    companion object {
        const val LEASE_MILLIS = 10 * 60 * 1000L
        const val RENEW_MILLIS = LEASE_MILLIS / 2
        private const val RETRY_MILLIS = 60 * 1000L

        fun forContext(context: Context) = ServiceWakeLease({
            val lock = context.getSystemService(PowerManager::class.java)
                .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "PGProxy:LocalApi")
                .apply { setReferenceCounted(false) }
            object : CpuWakeLock {
                override val isHeld: Boolean get() = lock.isHeld
                override fun acquire(timeoutMillis: Long) = lock.acquire(timeoutMillis)
                override fun release() = lock.release()
            }
        })
    }

    private var lock: CpuWakeLock? = null
    private var nextAttemptAt = 0L
    private var renewAt = 0L
    private var closed = false
    var requested = false
        private set
    var error = ""
        private set
    val isHeld: Boolean get() = try { lock?.isHeld == true } catch (_: Exception) { false }

    /** Caller must supply a current successful backend snapshot, never a requested or guessed state. */
    fun reconcile(required: Boolean, retry: Boolean = false) {
        if (closed) return
        val changed = required != requested
        requested = required
        if (!required) {
            nextAttemptAt = 0
            release(clearError = true)
            return
        }
        val time = now()
        if (isHeld && time < renewAt) return
        if (!changed && !retry && time < nextAttemptAt) return
        try {
            val current = lock ?: createLock().also { lock = it }
            current.acquire(LEASE_MILLIS)
            check(current.isHeld)
            renewAt = time + RENEW_MILLIS
            nextAttemptAt = 0
            error = ""
        } catch (_: Exception) {
            // A platform call may fail after taking the lock; still attempt bounded cleanup.
            error = "保持唤醒未生效，请关闭后重试；服务仍按普通后台模式运行。"
            nextAttemptAt = time + RETRY_MILLIS
            release(clearError = false)
        }
    }

    fun close() {
        closed = true
        requested = false
        release(clearError = true)
    }

    private fun release(clearError: Boolean) {
        try {
            lock?.let { if (it.isHeld) it.release() }
            lock = null
            renewAt = 0
            if (clearError) error = ""
        } catch (_: Exception) {
            // The last acquire still expires at the system-enforced lease deadline.
            error = "唤醒锁未能立即释放，将在租期结束时自动释放。"
        }
    }
}
