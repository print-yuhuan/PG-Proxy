package dev.pgproxy.app

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class LoginRecoveryPolicyTest {
    private val home = "https://aistudio.google.com/prompts/new_chat"
    private val signIn = "https://accounts.google.com/v3/signin/challenge/dp"
    private val handoff = "https://accounts.youtube.com/accounts/SetSID?ssdc=1"

    @Test
    fun trustedAccountNavigationAllowsOneConnectionResetRecovery() {
        listOf(signIn, handoff, "https://ACCOUNTS.GOOGLE.COM:443/", "https://ACCOUNTS.YOUTUBE.COM:443/")
            .forEach { navigation ->
                val policy = LoginRecoveryPolicy()
                policy.observeNavigation(navigation)

                assertTrue(navigation, policy.tryStartRecovery(home, isConnectionReset = true))
                assertTrue(policy.isRecovering)
            }
    }

    @Test
    fun initialHomeFailureDoesNotCountAsASignInFlowOrConsumeRecoveryBudget() {
        val policy = LoginRecoveryPolicy()
        policy.observeNavigation(home)

        assertFalse(policy.tryStartRecovery(home, isConnectionReset = true))
        assertFalse(policy.isRecovering)

        policy.observeNavigation(signIn)
        assertTrue(policy.tryStartRecovery(home, isConnectionReset = true))
    }

    @Test
    fun allowedGooglePagesOutsideExactAccountHostsDoNotEstablishLoginEvidence() {
        listOf(home, "https://google.com/", "https://mail.google.com/", "https://evil.accounts.google.com/")
            .forEach { navigation ->
                val policy = LoginRecoveryPolicy()
                policy.observeNavigation(navigation)

                assertFalse(navigation, policy.tryStartRecovery(home, isConnectionReset = true))
            }
    }

    @Test
    fun unsafeAccountNavigationCannotEstablishLoginEvidence() {
        listOf(
            "http://accounts.google.com/", "http://accounts.youtube.com/",
            "https://accounts.google.com:8443/", "https://accounts.youtube.com:80/",
            "https://user:password@accounts.google.com/", "https://user@accounts.youtube.com/",
            "https://accounts.google.com.evil.test/", "https://accounts.youtube.com.evil.test/",
            "https://evil.accounts.youtube.com/", "https://accounts.youtube.com./",
            "https://accounts.google.com@evil.test/", "https://accounts%2egoogle.com/",
            "https://example.test/?next=https://accounts.google.com/",
            "https://accounts.youtube.com\\@evil.test/", "https://accounts.google.com/\n",
            "intent://accounts.google.com/", "javascript:alert(1)", "about:blank", "not a URI", "", null,
        ).forEach { navigation ->
            val policy = LoginRecoveryPolicy()
            policy.observeNavigation(navigation)

            assertFalse(navigation, policy.tryStartRecovery(home, isConnectionReset = true))
        }
    }

    @Test
    fun recoveryRequiresAllowedFailureUriEvenAfterTrustedSignIn() {
        listOf(
            "https://evil.test/", "https://youtube.com/", "https://evil.accounts.youtube.com/",
            "https://aistudio.google.com.evil.test/", "http://aistudio.google.com/",
            "https://aistudio.google.com:8443/", "https://user@aistudio.google.com/",
            "https://accounts.youtube.com@evil.test/", "https://accounts.youtube.com./",
            "https://accounts.google.com/\n", "javascript:alert(1)", "about:blank", "not a URI", "", null,
        ).forEach { failedUri ->
            val policy = LoginRecoveryPolicy()
            policy.observeNavigation(signIn)

            assertFalse(failedUri, policy.tryStartRecovery(failedUri, isConnectionReset = true))
            assertFalse(policy.isRecovering)
            assertTrue(policy.tryStartRecovery(home, isConnectionReset = true))
        }
    }

    @Test
    fun allowedGoogleAndExactYoutubeHandoffFailuresCanRecover() {
        listOf(home, signIn, handoff, "https://google.com/", "https://ACCOUNTS.YOUTUBE.COM:443/")
            .forEach { failedUri ->
                val policy = LoginRecoveryPolicy()
                policy.observeNavigation(signIn)

                assertTrue(failedUri, policy.tryStartRecovery(failedUri, isConnectionReset = true))
            }
    }

    @Test
    fun certificateAndOtherErrorsCannotStartRecoveryOrConsumeBudget() {
        val policy = LoginRecoveryPolicy()
        policy.observeNavigation(signIn)

        assertFalse(policy.tryStartRecovery(home, isConnectionReset = false))
        assertFalse(policy.isRecovering)
        assertTrue(policy.tryStartRecovery(home, isConnectionReset = true))
    }

    @Test
    fun repeatedErrorCallbacksCannotScheduleMoreThanOneRecovery() {
        val policy = LoginRecoveryPolicy()
        policy.observeNavigation(handoff)

        assertTrue(policy.tryStartRecovery(handoff, isConnectionReset = true))
        repeat(3) {
            assertFalse(policy.tryStartRecovery(handoff, isConnectionReset = true))
            assertFalse(policy.tryStartRecovery(home, isConnectionReset = true))
        }
        assertTrue(policy.isRecovering)
    }

    @Test
    fun successfulRecoveryAndLaterLoginNavigationDoNotRestoreBudget() {
        val policy = LoginRecoveryPolicy()
        policy.observeNavigation(signIn)
        assertTrue(policy.tryStartRecovery(handoff, isConnectionReset = true))

        policy.observeNavigation(home)
        policy.markRecoveryFinished()
        assertFalse(policy.isRecovering)

        policy.observeNavigation(signIn)
        assertFalse(policy.tryStartRecovery(handoff, isConnectionReset = true))
    }

    @Test
    fun failedRecoveryAndManualRetryDoNotRestoreBudget() {
        val policy = LoginRecoveryPolicy()
        policy.observeNavigation(signIn)
        assertTrue(policy.tryStartRecovery(handoff, isConnectionReset = true))

        assertFalse(policy.tryStartRecovery(home, isConnectionReset = true))
        policy.markRecoveryFinished()
        policy.observeNavigation(home)
        policy.markRecoveryFinished()

        assertFalse(policy.isRecovering)
        assertFalse(policy.tryStartRecovery(home, isConnectionReset = true))
    }

    @Test
    fun importOrClosingBeforeRecoveryPermanentlyRejectsLateCallbacks() {
        listOf(false, true).forEach { alreadyObservedSignIn ->
            val policy = LoginRecoveryPolicy()
            if (alreadyObservedSignIn) policy.observeNavigation(signIn)
            policy.disable()

            policy.observeNavigation(signIn)
            policy.markRecoveryFinished()

            assertFalse(policy.isRecovering)
            assertFalse(policy.tryStartRecovery(home, isConnectionReset = true))
        }
    }

    @Test
    fun importOrClosingDuringRecoveryClearsRecoveryWithoutReenablingIt() {
        val policy = LoginRecoveryPolicy()
        policy.observeNavigation(signIn)
        assertTrue(policy.tryStartRecovery(handoff, isConnectionReset = true))

        policy.disable()
        assertFalse(policy.isRecovering)
        policy.markRecoveryFinished()
        policy.observeNavigation(signIn)

        assertFalse(policy.tryStartRecovery(home, isConnectionReset = true))
    }

    @Test
    fun aNewActivityPolicyGetsItsOwnBudgetAndNeedsItsOwnLoginEvidence() {
        val previous = LoginRecoveryPolicy()
        previous.observeNavigation(signIn)
        assertTrue(previous.tryStartRecovery(home, isConnectionReset = true))
        previous.disable()

        val next = LoginRecoveryPolicy()
        assertFalse(next.tryStartRecovery(home, isConnectionReset = true))
        next.observeNavigation(signIn)
        assertTrue(next.tryStartRecovery(home, isConnectionReset = true))
    }
}
