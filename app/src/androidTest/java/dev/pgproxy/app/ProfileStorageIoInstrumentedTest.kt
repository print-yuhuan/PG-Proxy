package dev.pgproxy.app

import android.os.Handler
import android.os.Looper
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

/** Uses the production IO gate with synthetic work; no service, browser or network is started. */
@RunWith(AndroidJUnit4::class)
class ProfileStorageIoInstrumentedTest {
    @Test fun aPendingProfileOperationLeavesTheUiResponsiveAndReturnsOnMain() {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
        val entered = CountDownLatch(1)
        val release = CountDownLatch(1)
        val finished = CountDownLatch(1)
        val workerIsBackground = AtomicBoolean(false)
        val callbackIsMain = AtomicBoolean(false)
        try {
            scope.launch {
                val value = ProfileStorageIo.run {
                    workerIsBackground.set(Looper.myLooper() != Looper.getMainLooper())
                    entered.countDown()
                    check(release.await(5, TimeUnit.SECONDS))
                    "synthetic-profile"
                }
                callbackIsMain.set(value == "synthetic-profile" && Looper.myLooper() == Looper.getMainLooper())
                finished.countDown()
            }
            assertTrue(entered.await(5, TimeUnit.SECONDS))
            val uiPulse = CountDownLatch(1)
            Handler(Looper.getMainLooper()).post { uiPulse.countDown() }
            assertTrue("The UI must handle input while profile work waits", uiPulse.await(2, TimeUnit.SECONDS))
            assertEquals("A result cannot be published before IO completes", 1L, finished.count)
            release.countDown()
            assertTrue(finished.await(5, TimeUnit.SECONDS))
            assertTrue(workerIsBackground.get())
            assertTrue(callbackIsMain.get())
        } finally { release.countDown(); scope.cancel() }
    }

    @Test fun aDestroyedScreensLateWorkCannotPublishOrOverlapTheNextScreensWork() {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
        val firstEntered = CountDownLatch(1)
        val releaseFirst = CountDownLatch(1)
        val secondEntered = CountDownLatch(1)
        val secondFinished = CountDownLatch(1)
        val stalePublished = AtomicBoolean(false)
        try {
            val oldScreen = scope.launch {
                ProfileStorageIo.run {
                    firstEntered.countDown()
                    check(releaseFirst.await(5, TimeUnit.SECONDS))
                }
                stalePublished.set(true)
            }
            assertTrue(firstEntered.await(5, TimeUnit.SECONDS))
            oldScreen.cancel()
            scope.launch {
                ProfileStorageIo.run { secondEntered.countDown() }
                secondFinished.countDown()
            }
            InstrumentationRegistry.getInstrumentation().waitForIdleSync()
            assertFalse("A cancelled but running filesystem call still owns the gate",
                secondEntered.await(150, TimeUnit.MILLISECONDS))
            releaseFirst.countDown()
            assertTrue(secondFinished.await(5, TimeUnit.SECONDS))
            assertFalse("The destroyed screen must not receive a late result", stalePublished.get())
        } finally { releaseFirst.countDown(); scope.cancel() }
    }
}
