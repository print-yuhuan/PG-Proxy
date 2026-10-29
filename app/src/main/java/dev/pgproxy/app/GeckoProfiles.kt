package dev.pgproxy.app

import android.content.Context
import android.util.AtomicFile
import java.io.Closeable
import java.io.File
import java.io.RandomAccessFile
import java.nio.channels.FileLock
import java.nio.file.Files
import java.util.Comparator
import org.json.JSONObject

/** Independent Firefox profiles and cross-process journals; never uses SharedPreferences. */
object GeckoProfiles {
    fun validAccountId(id: String): Boolean = id.matches(Regex("[a-f0-9]{32}"))

    private fun root(context: Context) = File(context.noBackupFilesDir, "gecko-accounts").apply { mkdirs() }
    private fun account(context: Context, id: String): File {
        require(validAccountId(id))
        val base = root(context).canonicalFile
        val folder = File(base, id).canonicalFile
        require(folder.parentFile == base)
        return folder.apply { mkdirs() }
    }
    private fun marker(context: Context, id: String, name: String) = AtomicFile(File(account(context, id), name))
    private fun exists(context: Context, id: String, name: String): Boolean {
        val target = marker(context, id, name)
        val backup = File(target.baseFile.path + ".bak")
        // A committed backup remains authoritative after an interrupted AtomicFile write.
        if (backup.exists()) runCatching { target.openRead().use { } }
        return target.baseFile.exists() || backup.exists()
    }
    private fun write(context: Context, id: String, name: String): Boolean {
        val target = marker(context, id, name)
        var output: java.io.FileOutputStream? = null
        return try {
            output = target.startWrite()
            output.write(byteArrayOf(1))
            target.finishWrite(output)
            true
        } catch (_: Exception) {
            output?.let { target.failWrite(it) }
            false
        }
    }
    private fun ids(context: Context): List<String> = root(context).listFiles().orEmpty()
        .filter { it.isDirectory && validAccountId(it.name) }.map { it.name }
    private fun loginLabel(value: String?): String = value?.take(80)
        ?.takeIf { it.isNotBlank() && it.none { character -> character.code < 32 } } ?: "未完成的 Google 登录"
    fun recordStage(context: Context, stage: String) {
        val data = diagnostics(context).put("engine", "gecko").put("stage", stage)
            .put("updatedAt", System.currentTimeMillis())
        writeDiagnostics(context, data)
    }
    private fun writeDiagnostics(context: Context, data: JSONObject) {
        val target = AtomicFile(File(root(context), "login-stage.json"))
        var output: java.io.FileOutputStream? = null
        try {
            output = target.startWrite()
            output.write(data.toString().toByteArray(Charsets.UTF_8))
            target.finishWrite(output)
        } catch (_: Exception) { output?.let { target.failWrite(it) } }
    }
    fun diagnostics(context: Context): JSONObject {
        val saved = try {
            JSONObject(String(AtomicFile(File(root(context), "login-stage.json")).readFully(), Charsets.UTF_8))
        } catch (_: Exception) { JSONObject() }
        // Export only current diagnostics; obsolete browser-mode settings are ignored.
        return JSONObject().put("engine", "gecko").put("stage", saved.optString("stage", "not_started")).apply {
            for (key in listOf("updatedAt", "lastLoadError", "lastDeniedNavigation")) {
                if (saved.has(key)) put(key, saved.get(key))
            }
        }
    }
    fun recordLoadError(context: Context, category: Int, code: Int) {
        writeDiagnostics(context, diagnostics(context).put("lastLoadError", JSONObject()
            .put("category", category).put("code", code).put("at", System.currentTimeMillis())))
    }
    fun recordDeniedNavigation(context: Context, host: String, reason: String, googleHost: Boolean) {
        val safeHost = host.takeIf { it.matches(Regex("[a-z0-9.-]{1,253}")) } ?: "invalid-host"
        writeDiagnostics(context, diagnostics(context).put("lastDeniedNavigation", JSONObject()
            .put("host", safeHost).put("reason", reason).put("googleHost", googleHost)
            .put("at", System.currentTimeMillis())))
    }
    fun saveLoginMetadata(context: Context, id: String, label: String): Boolean {
        if (isDeleted(context, id)) return false
        val target = marker(context, id, "login.json")
        var output: java.io.FileOutputStream? = null
        return try {
            output = target.startWrite()
            output.write(JSONObject().put("label", loginLabel(label))
                .toString().toByteArray(Charsets.UTF_8))
            target.finishWrite(output)
            true
        } catch (_: Exception) { output?.let { target.failWrite(it) }; false }
    }
    fun pendingLogins(context: Context): List<JSONObject> = ids(context).sorted().filter {
        !isDeleted(context, it) && !isImported(context, it) && File(account(context, it), "profile").isDirectory
    }.map { id ->
        val saved = try { JSONObject(String(marker(context, id, "login.json").readFully(), Charsets.UTF_8)) }
            catch (_: Exception) { JSONObject() }
        JSONObject().put("id", id).put("label", loginLabel(saved.opt("label") as? String))
    }
    fun isDeleted(context: Context, id: String): Boolean = exists(context, id, "deleted")
    fun deletedAccountIds(context: Context): List<String> = ids(context).filter { isDeleted(context, it) }.sorted()
    fun pendingCleanupIds(context: Context): List<String> = ids(context).filter { exists(context, it, "cleanup-pending") }
    fun isImported(context: Context, id: String): Boolean = exists(context, id, "registered") && !isDeleted(context, id)
    fun isImportPending(context: Context, id: String): Boolean = exists(context, id, "import-pending") && !isDeleted(context, id)
    fun markImportPending(context: Context, id: String): Boolean = !isDeleted(context, id) && write(context, id, "import-pending")
    fun confirmImport(context: Context, id: String): Boolean {
        if (isDeleted(context, id) || !write(context, id, "registered")) return false
        marker(context, id, "import-pending").delete()
        return true
    }
    fun reconcileImports(context: Context, existingIds: Collection<String>) {
        val local = ids(context).toSet()
        existingIds.filter { it in local && !isDeleted(context, it) }.forEach { confirmImport(context, it) }
    }
    fun markForDeletion(context: Context, id: String): Boolean {
        if (!validAccountId(id) || !write(context, id, "deleted")) return false
        // The durable tombstone itself also causes startup cleanup if this second write fails.
        write(context, id, "cleanup-pending")
        marker(context, id, "registered").delete()
        marker(context, id, "import-pending").delete()
        return true
    }
    class Lease internal constructor(val profile: File, private val file: RandomAccessFile, private val lock: FileLock) : Closeable {
        @Synchronized override fun close() { try { if (lock.isValid) lock.release() } finally { file.close() } }
    }
    fun acquire(context: Context, id: String, label: String? = null): Lease {
        check(!isDeleted(context, id)) { "Deleted account profile" }
        val folder = account(context, id)
        val file = RandomAccessFile(File(folder, "active.lock"), "rw")
        try {
            val lock = file.channel.tryLock() ?: error("Profile already in use")
            if (isDeleted(context, id)) { lock.release(); error("Deleted account profile") }
            val profile = File(folder, "profile").apply { mkdirs() }
            if (label != null && !saveLoginMetadata(context, id, label)) {
                lock.release()
                error("Cannot persist browser login metadata")
            }
            return Lease(profile, file, lock)
        } catch (error: Exception) { file.close(); throw error }
    }
    /** False means active or failed; deletion remains queued for the next retry. */
    fun delete(context: Context, id: String): Boolean {
        if (!markForDeletion(context, id)) return false
        val folder = account(context, id)
        return try {
            RandomAccessFile(File(folder, "active.lock"), "rw").use { file ->
                val lock = file.channel.tryLock() ?: return false
                try {
                    val profile = File(folder, "profile")
                    require(profile.canonicalFile.parentFile == folder.canonicalFile)
                    if (profile.exists()) {
                        // Files.walk does not follow symlinks; only this UUID profile is removed.
                        Files.walk(profile.toPath()).use { paths ->
                            paths.sorted(Comparator.reverseOrder()).forEach { Files.delete(it) }
                        }
                    }
                    marker(context, id, "cleanup-pending").delete()
                    true
                } finally { lock.release() }
            }
        } catch (_: Exception) { false }
    }
    fun retryPending(context: Context): Boolean {
        var successful = true
        ids(context).filter { isDeleted(context, it) &&
            (exists(context, it, "cleanup-pending") || File(account(context, it), "profile").exists()) }
            .forEach { if (!delete(context, it)) successful = false }
        return successful
    }
}
