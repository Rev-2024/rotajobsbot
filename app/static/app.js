// Rotary Jobs Connect: browser chat client.
// Security note: server and user text is only ever inserted with textContent / text nodes,
// never as HTML, so names and notes typed by other users cannot inject markup.
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const els = {
    auth: $("auth"), chat: $("chat"), log: $("log"),
    authForm: $("auth-form"), phone: $("phone"), password: $("password"),
    invite: $("invite"), inviteRow: $("invite-row"),
    authError: $("auth-error"), authSubmit: $("auth-submit"),
    tabIn: $("tab-signin"), tabUp: $("tab-signup"),
    composer: $("composer"), input: $("message"), sendBtn: $("send"),
    menuBtn: $("menu-btn"), logoutBtn: $("logout-btn"),
  };

  let mode = "signin";
  let busy = false;
  let signedIn = false;
  let inviteRequired = false;
  let typingRow = null;

  // ---------------------------------------------------------------- network
  async function api(path, body) {
    const options = { credentials: "same-origin" };
    if (body !== undefined) {
      options.method = "POST";
      options.headers = { "Content-Type": "application/json" };
      options.body = JSON.stringify(body);
    }
    const res = await fetch(path, options);
    let data = null;
    try { data = await res.json(); } catch (_) { /* empty body */ }
    if (!res.ok) {
      const detail = data && typeof data.detail === "string" ? data.detail : "Something went wrong. Please try again.";
      const err = new Error(detail);
      err.status = res.status;
      throw err;
    }
    return data;
  }

  // ---------------------------------------------------------------- rendering
  // Turns *bold* and http(s) links into nodes. Everything else stays plain text.
  function fmt(text) {
    const frag = document.createDocumentFragment();
    String(text).split(/(\*[^*\n]+\*|https?:\/\/\S+)/g).forEach((part) => {
      if (!part) return;
      if (part.length > 2 && part.startsWith("*") && part.endsWith("*")) {
        const b = document.createElement("strong");
        b.textContent = part.slice(1, -1);
        frag.append(b);
      } else if (/^https?:\/\//.test(part)) {
        const a = document.createElement("a");
        a.href = part;
        a.textContent = part;
        a.target = "_blank";
        a.rel = "noopener noreferrer";
        frag.append(a);
      } else {
        frag.append(document.createTextNode(part));
      }
    });
    return frag;
  }

  function scrollDown() {
    els.log.scrollTop = els.log.scrollHeight;
  }

  function addBubble(kind, text) {
    const row = document.createElement("div");
    row.className = "row " + (kind === "me" ? "me" : "bot");
    const bubble = document.createElement("div");
    bubble.className = "bubble" + (kind === "error" ? " error" : "");
    bubble.append(fmt(text));
    row.append(bubble);
    els.log.append(row);
    scrollDown();
    return row;
  }

  function renderBot(msg, opts) {
    const notice = !!(opts && opts.notice);
    const row = document.createElement("div");
    row.className = "row bot" + (notice ? " notice" : "");

    const bubble = document.createElement("div");
    bubble.className = "bubble";
    if (notice) {
      const tag = document.createElement("span");
      tag.className = "tag";
      tag.textContent = "🔔 New";
      bubble.append(tag);
    }
    bubble.append(fmt(msg.body));
    row.append(bubble);

    const isList = msg.kind === "list";
    const options = msg.kind === "buttons" ? msg.buttons : isList ? msg.rows : [];
    if (options.length) {
      const group = document.createElement("div");
      group.className = isList ? "cards" : "chips";
      options.forEach((opt) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = isList ? "card" : "chip";
        const title = document.createElement("span");
        title.className = "opt-title";
        title.textContent = opt.title;
        btn.append(title);
        if (opt.description) {
          const desc = document.createElement("span");
          desc.className = "opt-desc";
          desc.textContent = opt.description;
          btn.append(desc);
        }
        btn.addEventListener("click", () => choose(group, btn, opt));
        group.append(btn);
      });
      row.append(group);
    }
    els.log.append(row);
    scrollDown();
  }

  function showTyping() {
    if (typingRow) return;
    typingRow = document.createElement("div");
    typingRow.className = "row bot typing";
    typingRow.setAttribute("aria-hidden", "true");
    const bubble = document.createElement("div");
    bubble.className = "bubble";
    for (let i = 0; i < 3; i++) bubble.append(document.createElement("span"));
    typingRow.append(bubble);
    els.log.append(typingRow);
    scrollDown();
  }

  function hideTyping() {
    if (typingRow) typingRow.remove();
    typingRow = null;
  }

  // ---------------------------------------------------------------- chat actions
  function setBusy(on) {
    busy = on;
    els.sendBtn.disabled = on;
  }

  async function send(payload) {
    if (busy) return;
    setBusy(true);
    showTyping();
    try {
      const data = await api("/api/chat", payload);
      hideTyping();
      data.messages.forEach((m) => renderBot(m));
    } catch (err) {
      hideTyping();
      if (err.status === 401) { showAuth(); return; }
      addBubble("error", err.message);
    } finally {
      setBusy(false);
    }
  }

  function choose(group, btn, opt) {
    if (busy || group.dataset.used) return;
    group.dataset.used = "1";
    group.querySelectorAll("button").forEach((b) => { b.disabled = true; });
    btn.classList.add("picked");
    addBubble("me", opt.title);
    send({ text: opt.title, reply_id: opt.id });
  }

  async function pollInbox() {
    if (!signedIn || busy || document.hidden) return;
    try {
      const data = await api("/api/inbox");
      data.messages.forEach((m) => renderBot(m, { notice: true }));
    } catch (err) {
      if (err.status === 401) showAuth();
    }
  }

  // ---------------------------------------------------------------- screens
  function showAuth() {
    signedIn = false;
    els.chat.hidden = true;
    els.auth.hidden = false;
    els.phone.focus();
  }

  async function showChat() {
    signedIn = true;
    els.auth.hidden = true;
    els.chat.hidden = false;
    els.log.replaceChildren();
    await send({ text: "hi" }); // opens the welcome or menu
    await pollInbox(); // anything that arrived while the user was away
  }

  function setMode(next) {
    mode = next;
    const up = next === "signup";
    els.tabIn.setAttribute("aria-selected", String(!up));
    els.tabUp.setAttribute("aria-selected", String(up));
    els.authSubmit.textContent = up ? "Create account" : "Sign in";
    els.password.autocomplete = up ? "new-password" : "current-password";
    els.inviteRow.hidden = !(up && inviteRequired);
    els.authError.textContent = "";
  }

  async function submitAuth(event) {
    event.preventDefault();
    els.authError.textContent = "";
    const body = { phone: els.phone.value, password: els.password.value };
    if (mode === "signup") body.invite_code = els.invite.value;
    els.authSubmit.disabled = true;
    try {
      await api(mode === "signup" ? "/api/signup" : "/api/login", body);
      els.password.value = "";
      await showChat();
    } catch (err) {
      els.authError.textContent = err.message;
    } finally {
      els.authSubmit.disabled = false;
    }
  }

  // ---------------------------------------------------------------- wiring
  els.tabIn.addEventListener("click", () => setMode("signin"));
  els.tabUp.addEventListener("click", () => setMode("signup"));
  els.authForm.addEventListener("submit", submitAuth);

  els.composer.addEventListener("submit", (event) => {
    event.preventDefault();
    const text = els.input.value.trim();
    if (!text || busy) return;
    els.input.value = "";
    addBubble("me", text);
    send({ text });
  });

  els.menuBtn.addEventListener("click", () => {
    if (busy) return;
    addBubble("me", "Menu");
    send({ text: "menu" });
  });

  els.logoutBtn.addEventListener("click", async () => {
    try { await api("/api/logout", {}); } catch (_) { /* leave anyway */ }
    els.log.replaceChildren();
    showAuth();
  });

  setInterval(pollInbox, 8000);
  document.addEventListener("visibilitychange", pollInbox);

  (async function init() {
    try {
      const cfg = await api("/api/config");
      inviteRequired = !!cfg.invite_required;
    } catch (_) { /* default: not required */ }
    try {
      await api("/api/me");
      await showChat();
    } catch (_) {
      showAuth();
    }
  })();
})();
