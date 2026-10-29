package dev.pgproxy.app

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class LoginNavigationPolicyTest {
    @Test
    fun googleSignInCanCompleteItsExactCrossDomainAccountHandoff() {
        listOf(
            "https://aistudio.google.com/prompts/new_chat",
            "https://accounts.google.com/v3/signin/challenge/dp",
            "https://google.com/",
            "https://accounts.youtube.com/accounts/SetSID?ssdc=1",
            "https://accounts.youtube.com:443/accounts/SetSID",
            "https://ACCOUNTS.YOUTUBE.COM/accounts/SetSID",
        ).forEach { assertTrue(it, LoginNavigationPolicy.allows(it)) }
    }

    @Test
    fun youtubeHandoffDoesNotPermitTheWiderSiteOrLookalikeHosts() {
        listOf(
            "https://youtube.com/", "https://www.youtube.com/", "https://m.youtube.com/",
            "https://evil.accounts.youtube.com/", "https://accounts.youtube.com.evil.test/",
            "https://accounts-youtube.com/", "https://accounts.youtube.com./",
            "https://evilgoogle.com/", "https://google.com.evil.test/", "https://g.co/",
            "https://example.test/accounts.youtube.com", "https://example.test/?next=https://accounts.youtube.com/",
        ).forEach { assertFalse(it, LoginNavigationPolicy.allows(it)) }
    }

    @Test
    fun redirectsStillRequireHttpsNoUserInfoAndTheDefaultTlsPort() {
        listOf(
            "http://accounts.youtube.com/", "http://accounts.google.com/",
            "https://accounts.youtube.com:80/", "https://accounts.youtube.com:444/",
            "https://accounts.google.com:8443/", "https://user@accounts.youtube.com/",
            "https://accounts.youtube.com@evil.test/", "https://user:password@accounts.google.com/",
            "intent://accounts.youtube.com/#Intent;scheme=https;end", "javascript:alert(1)",
            "file:///accounts.youtube.com", "data:text/html,accounts.youtube.com", "about:blank",
            "https://accounts%2eyoutube.com/", "https://accounts.youtube.com\\@evil.test/",
            "https://accounts.youtube.com/\n", "not a URI", "",
        ).forEach { assertFalse(it, LoginNavigationPolicy.allows(it)) }
    }
}
