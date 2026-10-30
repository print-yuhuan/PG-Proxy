package dev.pgproxy.app

import android.content.ContextWrapper
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File
import java.util.UUID

/** Checks persistent isolation/leases using synthetic files, not a claimed Google login test. */
@RunWith(AndroidJUnit4::class)
class GeckoProfilesTest {
    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private fun id() = UUID.randomUUID().toString().replace("-", "")

    @Test
    fun repeatedConfirmationAndSnapshotsDoNotRewriteTheRegisteredMarker() {
        val accountId = id()
        try {
            val profile = GeckoProfiles.acquire(context, accountId).use { it.profile }
            assertTrue(GeckoProfiles.markImportPending(context, accountId))
            assertTrue(GeckoProfiles.confirmImport(context, accountId))
            val marker = File(profile.parentFile, "registered")
            assertTrue(marker.setLastModified(1_600_000_000_000L))
            val before = android.system.Os.stat(marker.absolutePath)
            val timestamp = marker.lastModified()
            repeat(4) {
                assertTrue(GeckoProfiles.confirmImport(context, accountId))
                GeckoProfiles.reconcileImports(context, listOf(accountId))
            }
            assertEquals("Acknowledging unchanged state must not replace the file", before.st_ino,
                android.system.Os.stat(marker.absolutePath).st_ino)
            assertEquals("Acknowledging unchanged state must not write its contents", timestamp, marker.lastModified())
            // A later real import still clears its journal without replacing the registered marker.
            assertTrue(GeckoProfiles.markImportPending(context, accountId))
            assertTrue(GeckoProfiles.confirmImport(context, accountId))
            assertFalse(GeckoProfiles.isImportPending(context, accountId))
            assertEquals(timestamp, marker.lastModified())
            assertTrue(GeckoProfiles.markForDeletion(context, accountId))
            assertFalse("The fast path must never undo a deletion", GeckoProfiles.confirmImport(context, accountId))
        } finally { GeckoProfiles.delete(context, accountId) }
    }

    @Test
    fun independentDirectoriesAndLeasesPreventDeletingAnActiveProfile() {
        val firstId = id()
        val secondId = id()
        var first: GeckoProfiles.Lease? = null
        var second: GeckoProfiles.Lease? = null
        try {
            first = GeckoProfiles.acquire(context, firstId)
            second = GeckoProfiles.acquire(context, secondId)
            assertNotEquals(first.profile.canonicalPath, second.profile.canonicalPath)
            assertEquals(firstId, first.profile.parentFile!!.name)
            assertEquals(secondId, second.profile.parentFile!!.name)
            File(first.profile, "synthetic-session").writeText("first")
            File(second.profile, "synthetic-session").writeText("second")
            assertTrue(GeckoProfiles.markForDeletion(context, firstId))
            assertFalse("Active process lease prevents premature erasure", GeckoProfiles.delete(context, firstId))
            assertTrue(GeckoProfiles.pendingCleanupIds(context).contains(firstId))
            assertTrue(runCatching { GeckoProfiles.acquire(context, firstId) }.isFailure)
            val firstPath = first.profile
            first.close(); first = null
            assertTrue(GeckoProfiles.delete(context, firstId))
            assertFalse(firstPath.exists())
            assertEquals("second", File(second.profile, "synthetic-session").readText())
            assertFalse(GeckoProfiles.pendingCleanupIds(context).contains(firstId))
            assertTrue(GeckoProfiles.isDeleted(context, firstId))
        } finally {
            first?.close(); second?.close()
            GeckoProfiles.delete(context, firstId)
            GeckoProfiles.delete(context, secondId)
        }
    }

    @Test
    fun importJournalIsFileBasedAndLateConfirmationCannotUndoDeletion() {
        val accountId = id()
        val freshContext = ContextWrapper(context)
        assertTrue(GeckoProfiles.markImportPending(context, accountId))
        assertTrue(GeckoProfiles.isImportPending(freshContext, accountId))
        GeckoProfiles.reconcileImports(freshContext, emptyList())
        assertTrue(GeckoProfiles.isImportPending(context, accountId))
        GeckoProfiles.reconcileImports(freshContext, listOf(accountId))
        assertTrue(GeckoProfiles.isImported(context, accountId))
        assertFalse(GeckoProfiles.isImportPending(context, accountId))
        assertTrue(GeckoProfiles.markImportPending(context, accountId))
        assertTrue(GeckoProfiles.markForDeletion(freshContext, accountId))
        assertFalse(GeckoProfiles.confirmImport(context, accountId))
        GeckoProfiles.reconcileImports(freshContext, listOf(accountId))
        assertFalse(GeckoProfiles.isImported(context, accountId))
        assertFalse(GeckoProfiles.isImportPending(context, accountId))
        assertTrue(GeckoProfiles.delete(context, accountId))
    }

    @Test
    fun invalidAccountPathsAreRejected() {
        for (invalid in listOf("../", "../../shared_prefs", "/data/local/tmp", "a".repeat(33))) {
            assertFalse(GeckoProfiles.markForDeletion(context, invalid))
            assertTrue(runCatching { GeckoProfiles.acquire(context, invalid) }.isFailure)
        }
    }

    @Test
    fun unfinishedLoginCanResumeWhenMetadataIsMissing() {
        val accountId = id()
        try {
            GeckoProfiles.acquire(context, accountId).close()
            val incomplete = GeckoProfiles.pendingLogins(context).single { it.getString("id") == accountId }
            assertEquals("未完成的 Google 登录", incomplete.getString("label"))
            assertEquals(setOf("id", "label"), incomplete.keys().asSequence().toSet())
            assertTrue(GeckoProfiles.saveLoginMetadata(context, accountId, "恢复测试"))
            val resumed = GeckoProfiles.pendingLogins(context).single { it.getString("id") == accountId }
            assertEquals("恢复测试", resumed.getString("label"))
            assertEquals(setOf("id", "label"), resumed.keys().asSequence().toSet())
            assertTrue(GeckoProfiles.confirmImport(context, accountId))
            assertFalse(GeckoProfiles.pendingLogins(context).any { it.getString("id") == accountId })
        } finally { GeckoProfiles.delete(context, accountId) }
    }

    @Test
    fun corruptMetadataCannotEraseTheProfileOrImportJournal() {
        val accountId = id()
        try {
            val profile = GeckoProfiles.acquire(context, accountId).use { it.profile }
            val sentinel = File(profile, "synthetic-session").apply { writeText("preserve-profile") }
            val metadata = File(profile.parentFile, "login.json")
            assertTrue(GeckoProfiles.markImportPending(context, accountId))
            for (invalid in listOf("{", "{\"label\":null}", "{\"label\":42}", "{\"label\":\"\\n\"}")) {
                metadata.writeText(invalid)
                val pending = GeckoProfiles.pendingLogins(context).single { it.getString("id") == accountId }
                assertEquals("未完成的 Google 登录", pending.getString("label"))
                assertEquals(setOf("id", "label"), pending.keys().asSequence().toSet())
            }
            assertNull("Login always uses the engine's native user agent",
                GeckoLoginActivity.loginSessionSettings().userAgentOverride)

            GeckoProfiles.acquire(context, accountId, "恢复测试").close()
            val restored = JSONObject(metadata.readText())
            assertEquals(setOf("label"), restored.keys().asSequence().toSet())
            assertEquals("恢复测试", restored.getString("label"))
            assertEquals("preserve-profile", sentinel.readText())
            assertTrue("Unresolved import journal must survive metadata repair", GeckoProfiles.isImportPending(context, accountId))
            assertFalse(GeckoProfiles.isDeleted(context, accountId))

            assertTrue(GeckoProfiles.confirmImport(context, accountId))
            assertTrue(GeckoProfiles.isImported(context, accountId))
            assertTrue(GeckoProfiles.markForDeletion(context, accountId))
            assertFalse("Metadata repair cannot reopen a deleted account", GeckoProfiles.saveLoginMetadata(context, accountId, "恢复测试"))
        } finally { GeckoProfiles.delete(context, accountId) }
    }

    @Test
    fun committedMarkerBackupsCannotLoseImportOrDeletionIntent() {
        val accountId = id()
        try {
            val profile = GeckoProfiles.acquire(context, accountId).use { it.profile }
            assertTrue(GeckoProfiles.markImportPending(context, accountId))
            val pending = File(profile.parentFile, "import-pending")
            assertTrue(pending.renameTo(File(pending.path + ".bak")))
            assertTrue(GeckoProfiles.isImportPending(context, accountId))
            assertTrue(pending.exists())
            assertTrue(GeckoProfiles.markForDeletion(context, accountId))
            val deleted = File(profile.parentFile, "deleted")
            assertTrue(deleted.renameTo(File(deleted.path + ".bak")))
            assertTrue("A committed backup must keep the deletion tombstone authoritative", GeckoProfiles.isDeleted(context, accountId))
            assertFalse(GeckoProfiles.confirmImport(context, accountId))
            assertTrue(runCatching { GeckoProfiles.acquire(context, accountId) }.isFailure)
            assertTrue(GeckoProfiles.delete(context, accountId))
            assertFalse(profile.exists())
        } finally { GeckoProfiles.delete(context, accountId) }
    }
}
