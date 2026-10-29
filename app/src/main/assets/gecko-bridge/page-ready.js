/* Isolated extension world: reads readiness only, never passwords or form values. */
(() => {
  const selector = 'ms-prompt-input textarea, ms-autosize-textarea textarea, textarea[placeholder*="prompt" i], textarea[aria-label*="prompt" i], textarea[placeholder*="提示"], textarea[aria-label*="提示"], [contenteditable="true"][aria-label*="prompt" i], [contenteditable="true"][aria-label*="提示"]';
  let running = false;
  async function poll() {
    if (running || location.origin !== 'https://aistudio.google.com' || !location.pathname.startsWith('/prompts/')) return;
    running = true;
    try {
      const visible = Array.from(document.querySelectorAll(selector)).some(e => e.getClientRects().length && !e.disabled && getComputedStyle(e).visibility !== 'hidden');
      const dialog = Array.from(document.querySelectorAll('[role="dialog"], mat-dialog-container, dialog[open]')).some(e => e.getClientRects().length);
      if (!visible || dialog) return;
      const authuser = new URL(location.href).searchParams.get('authuser');
      if (authuser && authuser !== '0') return;
      const storage = [];
      let size = 0;
      for (let i = 0; i < localStorage.length; i++) {
        const name = localStorage.key(i), value = localStorage.getItem(name);
        size += name.length + value.length;
        if (size > 65536) break;
        storage.push({name, value});
      }
      await browser.runtime.sendMessage({op: 'page_ready', storage,
        browserMetadata: {language: navigator.language}});
    } catch (_) {
      // Never log web content, credentials or account identifiers.
    } finally { running = false; }
  }
  setInterval(poll, 1500);
  poll();
})();
