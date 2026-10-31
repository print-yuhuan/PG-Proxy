package dev.pgproxy.app

import androidx.test.runner.AndroidJUnitRunner

/** Only the test APK exposes this runner. Gecko's activity owns a disposable process. */
class LoginRecoveryTestRunner : AndroidJUnitRunner() {
    // Finishing the activity would shut down Gecko and kill this process before the
    // instrumentation result is delivered. The test detaches its session; the host
    // stops the test process and removes only the reported synthetic profile later.
    override fun shouldWaitForActivitiesToComplete(): Boolean = false
}
