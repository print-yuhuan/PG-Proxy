package dev.pgproxy.app

import android.content.Intent
import androidx.test.ext.junit.runners.AndroidJUnit4
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class VerificationLinkTest {
    private fun assertHandoff(url: String, expectedData: String) {
        val intent = requireNotNull(VerificationLink.playServicesIntent(url))
        assertEquals(Intent.ACTION_VIEW, intent.action)
        assertEquals(setOf(Intent.CATEGORY_BROWSABLE), intent.categories)
        assertEquals("com.google.android.gms", intent.`package`)
        assertEquals(expectedData, intent.dataString)
        assertNull(intent.extras)
        assertNull(intent.component)
        assertNull(intent.selector)
        assertNull(intent.type)
        assertEquals(0, intent.flags)
    }

    @Test fun officialHttpsLinksKeepTheirOriginalData() {
        for (url in listOf("https://recaptcha.net/qr", "https://recaptcha.google.com/qr/demo?case=a%2Fb")) {
            assertHandoff(url, url)
        }
    }

    @Test fun androidLinksCanOmitThePackageAndNeverForwardWebExtras() {
        val data = "recaptcha.net/qr/demo?case=a%2Fb"
        assertHandoff("intent://$data#Intent;scheme=https;end", "https://$data")
        val link = "intent://$data#Intent;scheme=https;package=com.google.android.gms;" +
            "action=android.intent.action.VIEW;S.browser_fallback_url=https%3A%2F%2Fexample.invalid;" +
            "S.example=test;end"
        assertHandoff(link, "https://$data")
    }

    @Test fun otherOriginsAndUnrelatedPathsHaveNoHandoff() {
        for (url in listOf(
            "http://recaptcha.net/qr", "https://example.invalid/qr",
            "https://recaptcha.net.example.invalid/qr", "https://guest@recaptcha.net/qr",
            "https://recaptcha.net:8080/qr", "https://recaptcha.net/", "https://recaptcha.net/qr-other",
            "intent://recaptcha.net/qr#Intent;scheme=http;end"
        )) assertNull(url, VerificationLink.playServicesIntent(url))
    }

    @Test fun webPageCannotSelectAnotherPackageComponentActionOrSelector() {
        for (field in listOf(
            "package=invalid.example;", "component=invalid.example/.ExampleActivity;",
            "action=android.intent.action.EDIT;", "SEL;action=android.intent.action.VIEW;"
        )) {
            val uri = "intent://recaptcha.net/qr#Intent;scheme=https;${field}end"
            assertNull(field, VerificationLink.playServicesIntent(uri))
        }
    }
}
