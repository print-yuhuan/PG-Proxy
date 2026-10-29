package dev.pgproxy.app

import android.content.ActivityNotFoundException
import android.content.Intent
import androidx.test.ext.junit.runners.AndroidJUnit4
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class BackgroundSettingsInstrumentedTest {
    @Test fun unavailableAndVendorRestrictedScreensFallBackToAWorkingDestination() {
        val attempts = mutableListOf<String?>()
        val opened = BackgroundSettings.launchFirstAvailable(listOf(Intent("missing"), Intent("blocked"), Intent("working"), Intent("unused"))) {
            attempts += it.action
            when (it.action) {
                "missing" -> throw ActivityNotFoundException()
                "blocked" -> throw SecurityException()
            }
        }
        assertTrue(opened)
        assertEquals(listOf("missing", "blocked", "working"), attempts)
    }

    @Test fun noAvailableScreenReportsFailureInsteadOfClaimingSettingsOpened() {
        assertFalse(BackgroundSettings.launchFirstAvailable(listOf(Intent("missing"))) { throw ActivityNotFoundException() })
        assertFalse(BackgroundSettings.launchFirstAvailable(emptyList()) { fail("No intent should be launched") })
    }
}
