package dev.pgproxy.app

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext

/** Shared across activities: cancelled screens cannot overlap a still-running disk operation. */
internal object ProfileStorageIo {
    private val gate = Mutex()

    suspend fun <T> run(operation: () -> T): T = withContext(Dispatchers.IO) {
        gate.withLock { operation() }
    }
}
