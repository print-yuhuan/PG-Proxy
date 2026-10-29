package dev.pgproxy.app

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.AtomicFile
import java.io.File
import java.io.FileNotFoundException
import java.io.ByteArrayOutputStream
import java.nio.charset.StandardCharsets.UTF_8
import java.security.KeyStore
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/** Only the private :proxy service owns this bridge. Never exposed to a web page. */
class NativeBridge(private val context: Context) {
    private val directory = File(context.noBackupFilesDir, "vault").apply { mkdirs() }
    private val workers = ConcurrentHashMap<String, Worker>()
    private val registryLock = Any()
    private class Reservation(var starting: Boolean = false)
    private val reservations = mutableMapOf<String, Reservation>()
    @Volatile private var closed = false
    private val alias = "pgproxy.vault.v1"

    @Synchronized private fun key(): SecretKey {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (store.getKey(alias, null) as? SecretKey)?.let { return it }
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore").run {
            init(KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build())
            generateKey()
        }
    }

    private fun accountFile(id: String): String {
        require(id.matches(Regex("[a-f0-9]{32}"))) { "Invalid account identifier" }
        return "account-$id"
    }

    @Synchronized private fun read(name: String, empty: String): String {
        val file = AtomicFile(File(directory, "$name.enc"))
        // openRead recovers a committed .bak left by AtomicFile on older Android.
        val bytes = try { file.openRead().use { input ->
            val limit = 2_097_152
            val output = ByteArrayOutputStream()
            val buffer = ByteArray(8192)
            while (output.size() <= limit) {
                val read = input.read(buffer, 0, minOf(buffer.size, limit + 1 - output.size()))
                if (read < 0) break
                output.write(buffer, 0, read)
            }
            output.toByteArray()
        } }
        catch (error: FileNotFoundException) {
            if (file.baseFile.exists() || File(file.baseFile.path + ".bak").exists()) throw error
            return empty
        }
        require(bytes.size in 29..2_097_152 && bytes[0].toInt() == 1) { "Invalid encrypted record" }
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, bytes.copyOfRange(1, 13)))
        cipher.updateAAD(name.toByteArray(UTF_8))
        return String(cipher.doFinal(bytes, 13, bytes.size - 13), UTF_8)
    }

    @Synchronized private fun write(name: String, value: String) {
        require(value.toByteArray(UTF_8).size < 2_000_000) { "Record too large" }
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, key())
        cipher.updateAAD(name.toByteArray(UTF_8))
        val bytes = byteArrayOf(1) + cipher.iv + cipher.doFinal(value.toByteArray(UTF_8))
        val file = AtomicFile(File(directory, "$name.enc"))
        val stream = file.startWrite()
        try { stream.write(bytes); file.finishWrite(stream) }
        catch (error: Exception) { file.failWrite(stream); throw error }
    }

    fun loadState(): String = read("state", "")
    fun saveState(value: String) = write("state", value)
    fun readAccount(id: String): String = read(accountFile(id), "{}")
    fun writeAccount(id: String, value: String) = write(accountFile(id), value)
    @Synchronized fun deleteAccount(id: String) {
        val file = File(directory, "${accountFile(id)}.enc")
        AtomicFile(file).delete()
        check(listOf(file, File(file.path + ".bak"), File(file.path + ".new")).none { it.exists() }) {
            "Encrypted account deletion was not completed"
        }
    }

    /** Reserve before dispatching ProcessBuilder to a cancellable background task. No I/O. */
    fun reserveWorker(id: String) {
        require(id.matches(Regex("[a-f0-9-]{36}"))) { "Invalid worker identifier" }
        synchronized(registryLock) {
            check(!closed && !reservations.containsKey(id)) { "WAA bridge is closed or identifier reused" }
            reservations[id] = Reservation()
        }
    }

    fun startWorker(id: String): String {
        val reservation = synchronized(registryLock) {
            check(!closed) { "WAA bridge is closed" }
            checkNotNull(reservations[id]) { "WAA startup was not reserved or was cancelled" }.also {
                check(!it.starting) { "WAA startup already dispatched" }
                it.starting = true
            }
        }
        var process: Process? = null
        var worker: Worker? = null
        var registered = false
        try {
            val binary = File(context.applicationInfo.nativeLibraryDir, "libpgwaa.so")
            check(binary.isFile && binary.canExecute()) { "Packaged WAA worker is not executable" }
            val builder = ProcessBuilder(binary.absolutePath)
            builder.directory(context.cacheDir)
            builder.environment()["HOME"] = context.filesDir.absolutePath
            builder.environment()["TMPDIR"] = context.cacheDir.absolutePath
            val launched = builder.start()
            process = launched
            val started = Worker(launched)
            worker = started
            registered = synchronized(registryLock) {
                if (closed || reservations[id] !== reservation) false
                else { workers[id] = started; true }
            }
            check(registered) { "WAA startup cancelled" }
            return id
        } finally {
            if (!registered) {
                synchronized(registryLock) { if (reservations[id] === reservation) reservations.remove(id) }
                if (worker != null) stopWorker(worker)
                else process?.let {
                    runCatching { it.destroyForcibly() }
                    runCatching { it.inputStream.close() }
                    runCatching { it.outputStream.close() }
                    runCatching { it.errorStream.close() }
                }
            }
        }
    }

    fun workerWrite(id: String, line: String) {
        require(line.toByteArray(UTF_8).size <= 4 * 1024 * 1024) { "WAA command too large" }
        val worker = workers[id] ?: error("WAA worker stopped")
        check(!worker.ended) { "WAA worker exited" }
        synchronized(worker) {
            worker.input.write(line)
            if (!line.endsWith("\n")) worker.input.newLine()
            worker.input.flush()
        }
    }

    fun workerRead(id: String, timeoutMs: Long): String {
        val worker = workers[id] ?: error("WAA worker stopped")
        check(!worker.ended || worker.output.isNotEmpty()) { "WAA worker exited" }
        val line = worker.output.poll(timeoutMs.coerceIn(1, 180_000), TimeUnit.MILLISECONDS)
            ?: throw java.util.concurrent.TimeoutException("WAA response timeout")
        check(line.isNotEmpty()) { "WAA worker exited" }
        return line
    }

    fun workerStop(id: String) {
        val worker = synchronized(registryLock) {
            reservations.remove(id)
            workers.remove(id)
        } ?: return
        stopWorker(worker)
    }

    private fun stopWorker(worker: Worker) {
        var interrupted = false
        try {
            runCatching { worker.process.destroy() }
            try { worker.process.waitFor(700, TimeUnit.MILLISECONDS) }
            catch (_: InterruptedException) { interrupted = true }
            if (worker.process.isAlive) {
                runCatching { worker.process.destroyForcibly() }
                try { worker.process.waitFor(2, TimeUnit.SECONDS) }
                catch (_: InterruptedException) { interrupted = true }
            }
        } finally {
            if (worker.process.isAlive) runCatching { worker.process.destroyForcibly() }
            runCatching { worker.input.close() }
            runCatching { worker.process.inputStream.close() }
            runCatching { worker.process.errorStream.close() }
            worker.output.clear()
            worker.output.offer("")
            for (thread in listOf(worker.reader, worker.errorReader)) {
                try { thread.join(1000) } catch (_: InterruptedException) { interrupted = true }
            }
            if (interrupted) Thread.currentThread().interrupt()
        }
    }

    fun stopAll() {
        synchronized(registryLock) { closed = true; reservations.clear() }
        workers.keys.toList().forEach { workerStop(it) }
    }
    fun workerCount(): Int = workers.size

    private class Worker(val process: Process) {
        val input = process.outputStream.bufferedWriter(UTF_8)
        val output = LinkedBlockingQueue<String>(16)
        @Volatile var ended = false
        val reader = Thread({
            try {
                val source = process.inputStream.bufferedReader(UTF_8)
                val line = StringBuilder()
                while (true) {
                    val char = source.read()
                    if (char == -1) break
                    if (char == 10) {
                        if (!output.offer(line.toString(), 30, TimeUnit.SECONDS)) break
                        line.setLength(0)
                    } else {
                        check(line.length < 4 * 1024 * 1024) { "WAA output too large" }
                        line.append(char.toChar())
                    }
                }
            } catch (_: Exception) { }
            finally { ended = true; output.offer(""); process.destroy() }
        }, "pg-waa-output").apply { isDaemon = true; start() }
        // Drain without retaining or logging interpreter errors, which may contain session data.
        val errorReader = Thread({
            try { process.errorStream.use { input -> val buffer = ByteArray(4096); while (input.read(buffer) != -1) { } } }
            catch (_: Exception) { }
        }, "pg-waa-errors").apply { isDaemon = true; start() }
    }
}
