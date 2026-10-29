package dev.pgproxy.app

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class WakeLockInstrumentedTest {
    private class FakeLock : CpuWakeLock {
        override var isHeld = false
        var failAcquire = false
        var failRelease = false
        val timeouts = mutableListOf<Long>()
        var releases = 0
        override fun acquire(timeoutMillis: Long) {
            timeouts.add(timeoutMillis)
            isHeld = true
            if (failAcquire) throw SecurityException("private platform details")
        }
        override fun release() {
            releases++
            if (failRelease) throw IllegalStateException("private platform details")
            isHeld = false
        }
    }

    @Test fun disabledByDefaultAndStoppingReleasesTheTimedLock() {
        val lock = FakeLock()
        var creations = 0
        val lease = ServiceWakeLease({ creations++; lock }, { 0 })
        lease.reconcile(false)
        assertEquals(0, creations)
        assertFalse(lease.requested)
        lease.reconcile(true)
        assertTrue(lease.isHeld)
        assertTrue(lease.requested)
        assertEquals(listOf(ServiceWakeLease.LEASE_MILLIS), lock.timeouts)
        lease.reconcile(false)
        assertFalse(lease.isHeld)
        assertFalse(lease.requested)
        assertEquals(1, lock.releases)
        lease.reconcile(false)
        assertEquals("Repeated stop cannot over-release", 1, lock.releases)
    }

    @Test fun healthySnapshotsRenewOnlyAtHalfOfTheLease() {
        val lock = FakeLock()
        var clock = 0L
        val lease = ServiceWakeLease({ lock }, { clock })
        lease.reconcile(true)
        clock = ServiceWakeLease.RENEW_MILLIS - 1
        lease.reconcile(true)
        assertEquals(1, lock.timeouts.size)
        clock++
        lease.reconcile(true)
        assertEquals(2, lock.timeouts.size)
        assertTrue(lock.timeouts.all { it == ServiceWakeLease.LEASE_MILLIS })
        lease.close()
        assertFalse(lease.isHeld)
    }

    @Test fun failedAcquisitionCleansUpAndDoesNotRetryOnEveryPoll() {
        val lock = FakeLock().apply { failAcquire = true }
        var clock = 0L
        val lease = ServiceWakeLease({ lock }, { clock })
        lease.reconcile(true)
        assertFalse("A call can acquire before it throws; cleanup must still run", lease.isHeld)
        assertEquals(1, lock.releases)
        assertTrue(lease.error.isNotBlank())
        assertFalse(lease.error.contains("private"))
        clock = 5_000
        lease.reconcile(true)
        assertEquals(1, lock.timeouts.size)
        lock.failAcquire = false
        clock = 60_000
        lease.reconcile(true)
        assertTrue(lease.isHeld)
        assertTrue(lease.error.isBlank())
        lease.close()
    }

    @Test fun aRenewalFailureReleasesThePreviousLease() {
        val lock = FakeLock()
        var clock = 0L
        val lease = ServiceWakeLease({ lock }, { clock })
        lease.reconcile(true)
        lock.failAcquire = true
        clock = ServiceWakeLease.RENEW_MILLIS
        lease.reconcile(true)
        assertFalse(lease.isHeld)
        assertTrue(lease.error.isNotBlank())
        lease.close()
    }

    @Test fun releaseFailureKeepsItsBoundedLockAndDestroyRejectsLateSnapshots() {
        val lock = FakeLock()
        val lease = ServiceWakeLease({ lock }, { 0 })
        lease.reconcile(true)
        lock.failRelease = true
        lease.close()
        assertTrue(lease.isHeld)
        assertTrue(lease.error.contains("租期"))
        assertFalse(lease.requested)
        lease.reconcile(true, retry = true)
        assertEquals(1, lock.timeouts.size)
        lock.failRelease = false
        lease.close()
        assertFalse(lease.isHeld)
    }

    @Test fun aDeniedFactoryCanBeRetriedAfterAnExplicitUserAction() {
        val lock = FakeLock()
        var denied = true
        val lease = ServiceWakeLease({ if (denied) throw SecurityException("private platform details") else lock }, { 0 })
        lease.reconcile(true)
        assertFalse(lease.isHeld)
        assertTrue(lease.error.isNotBlank())
        denied = false
        lease.reconcile(true, retry = true)
        assertTrue(lease.isHeld)
        assertTrue(lease.error.isBlank())
        lease.close()
        lease.reconcile(true)
        assertFalse("A destroyed owner cannot reacquire from a late callback", lease.isHeld)
    }

    @Test fun applicationUidCanAcquireAndReleaseThePlatformPartialLock() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val lease = ServiceWakeLease.forContext(context)
        try {
            lease.reconcile(true)
            assertTrue("Manifest permission and platform acquisition must both work", lease.isHeld)
            assertTrue(lease.error.isBlank())
            lease.reconcile(false)
            assertFalse(lease.isHeld)
        } finally { lease.close() }
    }

    @Test fun frequentSnapshotsCannotPostponeTheFiveMinuteProbe() {
        var clock = 0L
        val pending = linkedMapOf<Runnable, Long>()
        var probes = 0
        val schedule = WakeProbeSchedule(
            { task, delay -> pending[task] = clock + delay; true },
            { pending.remove(it) },
            { probes++ },
        )
        schedule.schedule()
        assertEquals(listOf(300_000L), pending.values.toList())
        clock = 299_000
        repeat(10) { schedule.schedule() }
        assertEquals("Leaving the UI just before renewal must retain the original deadline",
            listOf(300_000L), pending.values.toList())
        val first = pending.keys.single()
        pending.remove(first)
        clock = 300_000
        first.run()
        assertEquals(1, probes)
        schedule.schedule()
        assertEquals(listOf(600_000L), pending.values.toList())
        schedule.cancel()
        assertTrue(pending.isEmpty())
    }
}
