// Fixproblem front-end: theme, command palette, shortcuts, toasts, inline labelling, drag-and-drop upload.
(function () {
  // CSRF: every same-origin POST from JavaScript carries the session token
  const csrf = () => document.querySelector('meta[name="csrf-token"]')?.content || "";
  const _fetch = window.fetch.bind(window);
  window.fetch = (url, opts = {}) => {
    if ((opts.method || "GET").toUpperCase() !== "GET") opts.headers = { ...(opts.headers || {}), "X-CSRF-Token": csrf() };
    return _fetch(url, opts);
  };
  window.fpCsrf = csrf;
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];

  // ----- theme -----
  const root = document.documentElement;
  const saved = localStorage.getItem("fp-theme");
  if (saved) root.dataset.theme = saved;
  else if (matchMedia("(prefers-color-scheme: dark)").matches) root.dataset.theme = "dark";
  window.toggleSidebar = () => {
    const a = document.getElementById("app"); if (!a) return;
    a.classList.toggle("collapsed"); localStorage.setItem("fp-collapsed", a.classList.contains("collapsed") ? "1" : "");
  };
  document.addEventListener("DOMContentLoaded", () => { if (localStorage.getItem("fp-collapsed")) document.getElementById("app")?.classList.add("collapsed"); });
  window.toggleTheme = () => {
    root.dataset.theme = root.dataset.theme === "dark" ? "light" : "dark";
    localStorage.setItem("fp-theme", root.dataset.theme);
  };

  // ----- toasts -----
  window.toast = (msg, kind = "ok") => {
    let box = $(".toasts");
    if (!box) { box = document.createElement("div"); box.className = "toasts"; document.body.append(box); }
    const t = document.createElement("div");
    t.className = "toast " + kind;
    t.textContent = msg;
    box.append(t);
    setTimeout(() => t.remove(), 4200);
  };
  document.addEventListener("DOMContentLoaded", () => {
    $$("[data-flash]").forEach(f => toast(f.dataset.flash, f.dataset.kind));

    // clickable rows
    $$("tr[data-href]").forEach(tr => tr.addEventListener("click", e => {
      if (!e.target.closest("a,button,select,input,form")) location.href = tr.dataset.href;
    }));

    // inline labelling in the review queue
    $$("form.label-form").forEach(f => f.addEventListener("submit", async e => {
      e.preventDefault();
      const btn = e.submitter;
      const body = new FormData(f);
      body.set("label", btn.value);
      const r = await fetch(f.action, { method: "POST", body, headers: { "X-Requested-With": "fetch" } });
      if (!r.ok) return toast("Could not save that label", "error");
      const row = f.closest("tr");
      row.classList.add("fade-out");
      setTimeout(() => row.remove(), 300);
      const c = $("#review-left"); if (c) c.textContent = Math.max(0, +c.textContent - 1);
      toast(`${body.get("ticket")} labelled as ${btn.value.replace("_", " ")}`);
    }));

    // drag-and-drop upload
    const drop = $(".drop");
    if (drop) {
      const input = $("input[type=file]", drop);
      ["dragenter", "dragover"].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add("over"); }));
      ["dragleave", "drop"].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove("over"); }));
      drop.addEventListener("drop", e => { input.files = e.dataTransfer.files; $("#file-name").textContent = input.files[0]?.name || ""; });
      input.addEventListener("change", () => { $("#file-name").textContent = input.files[0]?.name || ""; });
    }
    setupPalette();
    setupTour();
    setupAssistant();
  });

  // ----- assistant -----
  function setupAssistant() {
    const fab = $("#ai-fab"), panel = $("#ai-panel"); if (!fab) return;
    const msgs = $("#ai-msgs"), form = $("#ai-form"), input = $("#ai-q"), chips = $("#ai-chips");
    const open = (q) => { panel.classList.add("open"); fab.setAttribute("aria-expanded", "true"); input.focus(); if (q) ask(q); };
    const close = () => { panel.classList.remove("open"); fab.setAttribute("aria-expanded", "false"); fab.focus(); };
    window.openAssistant = open;
    fab.onclick = () => (panel.classList.contains("open") ? close() : open());
    $("#ai-close").onclick = close;
    panel.addEventListener("keydown", e => { if (e.key === "Escape") close(); });
    const add = (cls, text) => { const d = document.createElement("div"); d.className = "ai-msg " + cls; d.textContent = text; msgs.append(d); msgs.scrollTop = msgs.scrollHeight; return d; };
    function renderChips(list) { chips.innerHTML = ""; (list || []).slice(0, 4).forEach(t => { const b = document.createElement("button"); b.type = "button"; b.className = "ai-chip"; b.textContent = t; b.onclick = () => ask(t); chips.append(b); }); }
    $$(".ai-chip", chips).forEach(b => b.onclick = () => ask(b.textContent));
    async function ask(q) {
      add("user", q); input.value = "";
      const wait = add("bot", ""); wait.innerHTML = '<span class="typing"><i></i><i></i><i></i></span>';
      try {
        const r = await fetch("/assistant/ask", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ q }) });
        const d = await r.json();
        wait.textContent = d.text;
        const meta = document.createElement("div"); meta.className = "ai-meta";
        (d.actions || []).forEach(a => {
          if (a.method === "post") {
            const f = document.createElement("form"); f.method = "post"; f.action = a.url; f.style.display = "inline";
            const t = document.createElement("input"); t.type = "hidden"; t.name = "csrf_token"; t.value = csrf(); f.append(t);
            Object.entries(a.fields || {}).forEach(([k, v]) => { const i = document.createElement("input"); i.type = "hidden"; i.name = k; i.value = v; f.append(i); });
            const b = document.createElement("button"); b.className = "ai-act"; b.textContent = a.label; f.append(b); meta.append(f);
          } else { const l = document.createElement("a"); l.className = "ai-act"; l.style.display = "inline-flex"; l.style.alignItems = "center"; l.href = a.url; l.textContent = a.label; meta.append(l); }
        });
        (d.sources || []).forEach(s => { const l = document.createElement("a"); l.className = "ai-src"; l.href = s.url; l.textContent = "Source: " + s.title; meta.append(l); });
        if (d.escalate && !(d.actions || []).length) { const l = document.createElement("a"); l.className = "ai-src"; l.href = d.escalate.url; l.textContent = d.escalate.label; meta.append(l); }
        if (meta.children.length) wait.append(meta);
        renderChips(d.suggestions);
      } catch (e) { wait.textContent = "Sorry, I couldn’t answer just now. Try again or talk to a person."; }
      msgs.scrollTop = msgs.scrollHeight;
    }
    form.addEventListener("submit", e => { e.preventDefault(); const q = input.value.trim(); if (q) ask(q); });
    $$("[data-ask]").forEach(el => el.addEventListener("submit", e => { e.preventDefault(); const q = new FormData(el).get("q"); open(q || undefined); }));
  }

  // ----- onboarding tour -----
  function setupTour() {
    const back = $(".tour-back"); if (!back) return;
    const slides = $$(".slide", back), dots = $$(".dots i", back);
    let i = 0;
    const show = n => {
      i = Math.max(0, Math.min(n, slides.length - 1));
      slides.forEach((s, k) => s.classList.toggle("on", k === i));
      dots.forEach((d, k) => d.classList.toggle("on", k === i));
      $("#tour-prev").style.visibility = i ? "visible" : "hidden";
      $("#tour-next").textContent = i === slides.length - 1 ? "Start using Fixproblem" : "Next";
    };
    const done = () => { back.remove(); fetch("/tour/done", { method: "POST" }); };
    $("#tour-next").onclick = () => (i === slides.length - 1 ? done() : show(i + 1));
    $("#tour-prev").onclick = () => show(i - 1);
    $("#tour-skip").onclick = done;
    document.addEventListener("keydown", e => {
      if (!document.body.contains(back)) return;
      if (e.key === "ArrowRight") $("#tour-next").click();
      if (e.key === "ArrowLeft") show(i - 1);
      if (e.key === "Escape") done();
    });
    show(0);
  }

  // ----- command palette (Cmd/Ctrl + K) -----
  const NAV = [
    ["Go to overview", "/dashboard", "G D"], ["Go to tickets", "/inbox", "G T"], ["Go to issues", "/issues", "G I"],
    ["Open review queue", "/review", "G R"], ["Trends", "/trends", ""], ["Reports", "/reports", ""], ["Import tickets", "/import", ""], ["Team", "/team", ""], ["Help & tutorials", "/help", ""], ["Give feedback", "/feedback", ""], ["Settings", "/settings", ""],
    ["Toggle dark mode", "theme", ""], ["Sign out", "/logout", ""],
  ];
  function setupPalette() {
    const back = $(".cmdk-back"); if (!back) return;
    const input = $("input", back), list = $(".list", back);
    let items = [], active = 0, timer;
    const open = () => { back.classList.add("open"); input.value = ""; render(NAV.map(n => ({ title: n[0], url: n[1], sub: n[2], grp: "Navigation" }))); setTimeout(() => input.focus(), 10); };
    const close = () => back.classList.remove("open");
    window.openPalette = open;
    function render(rows) {
      items = rows; active = 0; list.innerHTML = "";
      let grp = "";
      rows.forEach((r, i) => {
        if (r.grp !== grp) { grp = r.grp; const g = document.createElement("div"); g.className = "grp"; g.textContent = grp; list.append(g); }
        const d = document.createElement("div");
        d.className = "item" + (i === 0 ? " on" : "");
        d.innerHTML = `<span></span><small></small>`;
        d.firstChild.textContent = r.title; d.lastChild.textContent = r.sub || "";
        d.onclick = () => go(r); d.onmousemove = () => setActive(i);
        list.append(d);
      });
      if (!rows.length) list.innerHTML = '<div class="grp">No results</div>';
    }
    function setActive(i) { const els = $$(".item", list); els.forEach(e => e.classList.remove("on")); if (els[i]) { els[i].classList.add("on"); els[i].scrollIntoView({ block: "nearest" }); } active = i; }
    function go(r) { if (r.url === "theme") { toggleTheme(); close(); } else location.href = r.url; }
    input.addEventListener("input", () => {
      clearTimeout(timer);
      const q = input.value.trim().toLowerCase();
      const nav = NAV.filter(n => n[0].toLowerCase().includes(q)).map(n => ({ title: n[0], url: n[1], sub: n[2], grp: "Navigation" }));
      if (q.length < 2) return render(nav);
      timer = setTimeout(async () => {
        const r = await fetch("/api/search?q=" + encodeURIComponent(q)); const d = await r.json();
        render([...nav, ...d.issues.map(x => ({ ...x, grp: "Issues" })), ...d.tickets.map(x => ({ ...x, grp: "Tickets" }))]);
      }, 140);
    });
    input.addEventListener("keydown", e => {
      if (e.key === "ArrowDown") { e.preventDefault(); setActive(Math.min(active + 1, items.length - 1)); }
      if (e.key === "ArrowUp") { e.preventDefault(); setActive(Math.max(active - 1, 0)); }
      if (e.key === "Enter" && items[active]) go(items[active]);
    });
    back.addEventListener("click", e => { if (e.target === back) close(); });
    let leader = false;
    document.addEventListener("keydown", e => {
      const typing = /INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName);
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); back.classList.contains("open") ? close() : open(); return; }
      if (e.key === "Escape") close();
      if (typing) return;
      if (e.key === "/") { const s = $("input[type=search]"); if (s) { e.preventDefault(); s.focus(); } }
      if (leader) { const map = { d: "/dashboard", t: "/inbox", i: "/issues", r: "/review" }; if (map[e.key]) location.href = map[e.key]; leader = false; return; }
      if (e.key === "g") { leader = true; setTimeout(() => leader = false, 800); }
    });
  }
})();
