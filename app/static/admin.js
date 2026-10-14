(() => {
  "use strict";
  const $ = (sel, root = document) => root.querySelector(sel);
  const CLONE = { albani: "الألباني", ibn_uthaymeen: "ابن عثيمين", ibn_baz: "ابن باز", maliki: "المالكية", comparative: "المقارن" };
  const STAGE = { answer: "إجابة", clarify: "أسئلة توضيحية", out_of_scope: "خارج النطاق", compare: "مقارنة" };
  const state = { me: null, users: [], activity: [], days: 30 };

  function el(tag, attrs = {}, children = []) {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === "text") n.textContent = v;
      else if (k === "html") n.innerHTML = v;
      else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
      else if (v !== null && v !== undefined && v !== false) n.setAttribute(k, v === true ? "" : v);
    }
    for (const c of [].concat(children)) if (c !== null && c !== undefined) n.append(c.nodeType ? c : document.createTextNode(String(c)));
    return n;
  }
  async function api(path, body, method) {
    const opts = { method: method || (body ? "POST" : "GET"), credentials: "same-origin", headers: {} };
    if (body) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
    const res = await fetch(path, opts);
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch (_) { /* ignore */ }
      const err = new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
      err.status = res.status;
      throw err;
    }
    return res.json();
  }
  const fmt = (iso) => {
    if (!iso) return "—";
    const d = new Date(iso);
    return isNaN(d) ? iso : d.toLocaleString("ar-LY", { dateStyle: "medium", timeStyle: "short" });
  };
  const table = (heads, rows, empty) => {
    if (!rows.length) return el("div", { class: "empty", text: empty || "لا بيانات بعد." });
    for (const tr of rows) [...tr.children].forEach((td, i) => { if (heads[i]) td.dataset.l = heads[i]; else td.classList.add("acts"); });
    return el("table", { class: "tbl" }, [
      el("thead", {}, el("tr", {}, heads.map((h) => el("th", { text: h })))),
      el("tbody", {}, rows),
    ]);
  };

  function renderKpis(a) {
    const box = $("#kpis");
    box.innerHTML = "";
    const items = [
      ["المستخدمون", a.users],
      ["نشطون (7 أيام)", a.active_users_7d],
      ["نشطون (30 يومًا)", a.active_users_30d],
      ["إجمالي الأسئلة", a.questions_total, "gold"],
      ["المحادثات", a.conversations],
      ["خارج النطاق", a.answers_by_stage.out_of_scope || 0],
    ];
    for (const [l, v, cls] of items) box.append(el("div", { class: "kpi " + (cls || "") }, [el("div", { class: "v", text: String(v ?? 0) }), el("div", { class: "l", text: l })]));
  }

  function renderDays(a) {
    const box = $("#chart-days");
    box.innerHTML = "";
    const days = [];
    const now = new Date();
    for (let i = a.days - 1; i >= 0; i--) {
      const d = new Date(now); d.setUTCDate(now.getUTCDate() - i);
      days.push(d.toISOString().slice(0, 10));
    }
    // more than ~90 bars never fit a card: group into weeks for long ranges
    const step = a.days > 90 ? 7 : 1;
    const buckets = [];
    for (let i = 0; i < days.length; i += step) {
      const slice = days.slice(i, i + step);
      buckets.push({ from: slice[0], to: slice[slice.length - 1], n: slice.reduce((t, d) => t + (a.questions_per_day[d] || 0), 0) });
    }
    const max = Math.max(1, ...buckets.map((b) => b.n));
    for (const bk of buckets) {
      const label = step === 1 ? bk.from : `${bk.from} → ${bk.to}`;
      const b = el("div", { class: "b", title: `${label}: ${bk.n}`, style: `height:${Math.max(2, (bk.n / max) * 140)}px` });
      if (bk.n && buckets.length <= 60) b.append(el("span", { text: String(bk.n) }));
      box.append(b);
    }
    const x = box.nextElementSibling;
    if (x && x.classList.contains("bars-x")) x.remove();
    box.after(el("div", { class: "bars-x" }, [el("span", { text: days[0] }), step > 1 ? el("span", { class: "hint", text: "مجموع كل أسبوع" }) : null, el("span", { text: days[days.length - 1] })]));
  }

  function hbars(target, data, labels, cls) {
    const box = $(target);
    box.innerHTML = "";
    const entries = Object.entries(data).sort((x, y) => y[1] - x[1]);
    if (!entries.length) { box.append(el("div", { class: "empty", text: "لا بيانات بعد." })); return; }
    const max = Math.max(1, ...entries.map((e) => e[1]));
    for (const [k, v] of entries) {
      box.append(el("div", { class: "hbar " + (cls || "") }, [
        el("div", { text: labels[k] || k }),
        el("div", { class: "track" }, el("div", { class: "fill", style: `width:${(v / max) * 100}%` })),
        el("div", { class: "n", text: String(v) }),
      ]));
    }
  }

  function renderOffices(a) {
    $("#offices").innerHTML = "";
    $("#offices").append(table(["الجهة", "المستخدمون", "الأسئلة"], a.by_office.map((o) => el("tr", {}, [el("td", { text: o.office }), el("td", { text: String(o.users) }), el("td", { text: String(o.questions) })]))));
  }
  function renderTop(a) {
    $("#top-users").innerHTML = "";
    $("#top-users").append(table(["الاسم", "البريد", "الجهة", "الأسئلة", "آخر دخول"], a.top_users.map((u) => el("tr", {}, [el("td", { text: u.name }), el("td", { text: u.email }), el("td", { text: u.office || "—" }), el("td", { text: String(u.questions) }), el("td", { text: fmt(u.last_login_at) })]))));
  }

  function renderUsers() {
    const q = ($("#users-search").value || "").trim().toLowerCase();
    const rows = state.users.filter((u) => !q || u.name.toLowerCase().includes(q) || u.email.toLowerCase().includes(q) || (u.office || "").toLowerCase().includes(q)).map((u) => {
      const me = u.id === state.me.id;
      const actions = el("td");
      if (!me) {
        actions.append(el("button", { type: "button", class: "ghost small", text: u.role === "admin" ? "إلغاء الإشراف" : "جعله مشرفًا", onclick: async () => { await api(`/api/admin/users/${u.id}`, { role: u.role === "admin" ? "user" : "admin" }, "PATCH"); load(); } }));
        actions.append(el("button", { type: "button", class: "ghost small danger", text: "حذف", onclick: async () => { if (!confirm(`حذف حساب ${u.name} وكل سجله؟`)) return; await api(`/api/admin/users/${u.id}`, null, "DELETE"); load(); } }));
      } else actions.append(el("span", { class: "hint", text: "(أنت)" }));
      actions.append(el("button", { type: "button", class: "ghost small", text: "أسئلته", onclick: () => { $("#activity-search").value = u.email; loadActivity(); $("#activity").scrollIntoView({ behavior: "smooth" }); } }));
      return el("tr", {}, [el("td", { text: u.name }), el("td", { text: u.email }), el("td", { text: u.office || "—" }), el("td", {}, el("span", { class: `badge role-${u.role}`, text: u.role === "admin" ? "مشرف" : "مستخدم" })), el("td", { text: fmt(u.created_at) }), el("td", { text: fmt(u.last_login_at) }), el("td", { text: String(u.conversations) }), actions]);
    });
    $("#users").innerHTML = "";
    $("#users").append(table(["الاسم", "البريد", "الجهة", "الدور", "تاريخ التسجيل", "آخر دخول", "محادثات", ""], rows, q ? "لا نتائج." : "لا مستخدمين بعد."));
  }

  async function loadActivity() {
    const q = ($("#activity-search").value || "").trim();
    try {
      state.activity = await api(`/api/admin/activity?limit=200${q ? "&q=" + encodeURIComponent(q) : ""}`);
    } catch (err) { $("#activity").textContent = err.message; return; }
    const rows = state.activity.map((r) => el("tr", {}, [
      el("td", { text: fmt(r.created_at) }),
      el("td", {}, [r.name, el("div", { class: "hint", text: r.email })]),
      el("td", { text: r.office || "—" }),
      el("td", { text: CLONE[r.clone] || r.clone }),
      el("td", { class: "q", text: r.text || "—" }),
      el("td", {}, el("span", { class: `badge stage-${r.stage || "answer"}`, text: STAGE[r.stage || "answer"] || r.stage })),
    ]));
    $("#activity").innerHTML = "";
    $("#activity").append(table(["الوقت", "المستخدم", "الجهة", "المنهج", "السؤال", "نوع الرد"], rows, q ? "لا نتائج." : "لا أسئلة مسجلة بعد."));
  }

  async function load() {
    try {
      const [a, users] = await Promise.all([api(`/api/admin/analytics?days=${state.days}`), api("/api/admin/users")]);
      state.users = users;
      renderKpis(a); renderDays(a);
      hbars("#chart-clone", a.questions_by_clone, CLONE);
      hbars("#chart-stage", a.answers_by_stage, STAGE, "stage");
      renderOffices(a); renderTop(a); renderUsers();
      const sys = $("#system"); sys.innerHTML = "";
      for (const [k, v] of Object.entries(a.kb || {})) sys.append(el("div", { class: "sys-item" }, [el("b", { text: String(typeof v === "object" ? (v.records ?? "—") : v) }), el("span", { text: `سجل · ${CLONE[k] || k}` })]));
      sys.append(el("div", { class: "sys-item" }, [el("b", { text: a.llm_enabled ? "مفعّلة" : "غير مفعّلة" }), el("span", { text: "طبقة الصياغة (Gemini)" })]));
      sys.append(el("div", { class: "sys-item" }, [el("b", { text: String(a.active_sessions) }), el("span", { text: "جلسات دخول نشطة" })]));
      await loadActivity();
    } catch (err) {
      $("#system").textContent = err.message;
    }
  }

  async function init() {
    try {
      const me = await api("/api/auth/me");
      state.me = me.user;
    } catch (_) { state.me = null; }
    if (!state.me || state.me.role !== "admin") { $("#gate").hidden = false; return; }
    $("#dash").hidden = false;
    $("#subnav").hidden = false;
    const links = [...document.querySelectorAll("#subnav a")];
    const secs = links.map((l) => document.querySelector(l.getAttribute("href"))).filter(Boolean);
    const mark = () => {
      const y = window.scrollY + 140;
      let cur = links[0];
      for (let i = 0; i < secs.length; i++) if (secs[i].offsetTop <= y) cur = links[i];
      links.forEach((l) => l.classList.toggle("on", l === cur));
    };
    window.addEventListener("scroll", mark, { passive: true });
    $("#days").addEventListener("change", (e) => { state.days = parseInt(e.target.value, 10); load(); });
    $("#btn-refresh").addEventListener("click", load);
    $("#btn-logout").addEventListener("click", async () => { await api("/api/auth/logout", {}); location.href = "/"; });
    $("#users-search").addEventListener("input", renderUsers);
    let t; $("#activity-search").addEventListener("input", () => { clearTimeout(t); t = setTimeout(loadActivity, 300); });
    load();
  }
  init();
})();
