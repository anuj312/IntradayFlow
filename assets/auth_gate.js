(function () {
  function qs(id) { return document.getElementById(id); }

  async function authStatus() {
    const r = await fetch("/auth/status", { credentials: "include" });
    if (!r.ok) return { authed: false };
    return await r.json();
  }

  function lockUI() {
    const shell = qs("app-shell");
    const overlay = qs("login-overlay");
    if (shell) shell.classList.add("blurred");
    if (overlay) overlay.style.display = "flex";
  }

  function unlockUI() {
    const shell = qs("app-shell");
    const overlay = qs("login-overlay");
    if (shell) shell.classList.remove("blurred");
    if (overlay) overlay.style.display = "none";
  }

  async function refreshLockState() {
    try {
      const s = await authStatus();
      if (s.authed) unlockUI();
      else lockUI();
    } catch (e) {
      lockUI();
    }
  }

  async function doLogin() {
    const userEl = qs("login-user");
    const passEl = qs("login-pass");
    const msgEl  = qs("login-msg");

    const user = userEl ? userEl.value : "";
    const password = passEl ? passEl.value : "";

    if (msgEl) msgEl.textContent = "";

    const r = await fetch("/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ user, password })
    });

    if (r.ok) {
      // After cookie is set, reload so Dash callbacks start working normally
      window.location.reload();
      return;
    }

    let msg = "Incorrect user id and password";
    try {
      const j = await r.json();
      if (j && j.detail) msg = j.detail;
    } catch (_) {}
    if (msgEl) msgEl.textContent = msg;
  }

  function tryBind() {
    const btn = qs("login-submit");
    const pass = qs("login-pass");

    if (!btn || !pass) return false;

    // Avoid double-binding
    if (btn.__bound) return true;
    btn.__bound = true;

    btn.addEventListener("click", (e) => { e.preventDefault(); doLogin(); });
    pass.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); doLogin(); }
    });

    return true;
  }

  // Boot
  lockUI();

  // Keep trying until Dash renders the overlay inputs
  const bindTimer = setInterval(() => {
    if (tryBind()) clearInterval(bindTimer);
  }, 250);

  // Check auth quickly on load, then occasionally
  refreshLockState();
  setInterval(refreshLockState, 5000);
})();