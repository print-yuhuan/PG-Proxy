/* Runs in a unique Firefox profile, never a shared context/store-id approximation. */
let running = false;
let selectedAuthUser = '0';
const required = ['SAPISID', '__Secure-1PAPISID', '__Secure-3PAPISID', '__Secure-1PSID'];
browser.webRequest.onBeforeSendHeaders.addListener(details => {
  const header = (details.requestHeaders || []).find(h => h.name.toLowerCase() === 'x-goog-authuser');
  if (header) selectedAuthUser = header.value;
}, {urls: ['https://alkalimakersuite-pa.clients6.google.com/*/ListModels']}, ['requestHeaders']);
browser.runtime.onMessage.addListener(async (message, sender) => {
  if (running || !message || message.op !== 'page_ready' || sender.frameId !== 0) return;
  try {
    const url = new URL(sender.url);
    if (url.origin !== 'https://aistudio.google.com' || !url.pathname.startsWith('/prompts/')) return;
    if (url.searchParams.has('authuser') && url.searchParams.get('authuser') !== '0') return;
  } catch (_) { return; }
  running = true;
  try {
    const response = await browser.runtime.sendNativeMessage('pgproxy_bridge', {op: 'status'});
    const status = typeof response === 'string' ? JSON.parse(response) : response;
    if (!status || !status.eligible) return;
    if (selectedAuthUser !== '0') {
      await browser.runtime.sendNativeMessage('pgproxy_bridge', {op: 'account_selection_error'});
      return;
    }
    const cookies = await browser.cookies.getAll({domain: 'google.com'});
    const names = new Set(cookies.filter(c => c.value).map(c => c.name));
    if (!required.every(name => names.has(name))) return;
    await browser.runtime.sendNativeMessage('pgproxy_bridge', {op: 'session', epoch: status.epoch,
      cookies, origins: [{origin: 'https://aistudio.google.com', localStorage: message.storage || []}],
      browserMetadata: message.browserMetadata});
  } catch (_) {
    // No Cookie values, messages or account names are logged.
  } finally { running = false; }
});
