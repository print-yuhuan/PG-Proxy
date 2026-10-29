package dev.pgproxy.app

import androidx.test.ext.junit.runners.AndroidJUnit4
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith

/** Notification policy only: no service, account, browser or network is started. */
@RunWith(AndroidJUnit4::class)
class ForegroundStateInstrumentedTest {
    @Test fun aFailedRepeatedStartPreservesAnExistingForegroundService() {
        assertNull(foregroundTarget("start", true, true, false, null))
        assertNull("Failure cannot downgrade a previously running service", foregroundTarget("start", true, true, false, false))
        assertEquals(true, foregroundTarget("start", true, true, false, true))
        assertEquals("An unsuccessful first start can remove its provisional notification", false,
            foregroundTarget("start", false, true, false, null))
        assertEquals("A confirmed running server must retain protection", true,
            foregroundTarget("start", false, true, false, true))
    }

    @Test fun aStopNeedsASuccessfulCurrentSnapshotBeforeRemovingProtection() {
        assertNull(foregroundTarget("stop", true, true, false, null))
        assertNull(foregroundTarget("stop", true, true, false, false))
        assertNull("A success response without state is not a stop confirmation", foregroundTarget("stop", true, true, true, null))
        assertEquals(true, foregroundTarget("stop", true, true, true, true))
        assertEquals(false, foregroundTarget("stop", true, true, true, false))
    }

    @Test fun staleOrClosingOperationsCannotChangeTheNotification() {
        for (action in listOf("start", "stop", "configure")) {
            for (success in listOf(false, true)) {
                for (running in listOf(null, false, true)) {
                    assertNull(foregroundTarget(action, true, false, success, running))
                    assertNull(foregroundTarget(action, false, false, success, running))
                }
            }
        }
        assertNull(foregroundTarget("configure", true, true, false, null))
        assertEquals(false, foregroundTarget("configure", true, true, true, false))
    }
}
