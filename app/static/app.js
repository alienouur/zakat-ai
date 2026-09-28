/* RTL front-end for the zakat multi-clone API. Vanilla JS; no build step. */
(() => {
  const $ = (sel, root = document) => root.querySelector(sel);
  const el = (tag, attrs = {}, children = []) => {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else if (k === "html") node.innerHTML = v;
      else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v);
    }
    for (const c of [].concat(children)) if (c != null) node.append(c);
    return node;
  };

  const state = {
    clones: [],
    clone: "ibn_uthaymeen",
    topics: [],
    // current clarification thread: original message + accumulated answers
    message: "",
    topic: "",
    answers: {},
  };

  const thread = $("#thread");
  const scrollDown = () => thread.lastElementChild?.scrollIntoView({ behavior: "smooth", block: "end" });

  async function api(path, body) {
    const res = await fetch(path, body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : undefined);
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch (_) { /* ignore */ }
      throw new Error(`${res.status}: ${detail}`);
    }
    return res.json();
  }

  // ---------------------------------------------------------------- clones / topics
  function renderClones() {
    const box = $("#clones");
    box.innerHTML = "";
    for (const c of state.clones) {
      const btn = el("button", { type: "button", class: "clone-btn" + (c.id === state.clone ? " active" : ""), role: "tab", onclick: () => selectClone(c.id) }, [
        el("span", { class: "n", text: c.name_ar }),
        el("span", { class: "s", text: c.id === "comparative" ? c.school : `${c.school} — ${c.records} سجلًا موثقًا` }),
      ]);
      box.append(btn);
    }
    renderCloneInfo();
  }

  function renderCloneInfo() {
    const c = state.clones.find((x) => x.id === state.clone);
    const box = $("#clone-info");
    box.innerHTML = "";
    if (!c) return;
    box.append(el("p", { text: c.description_ar }));
    box.append(el("p", { class: "disc", text: c.disclaimer_ar }));
    if (c.methodology_ar?.length) {
      box.append(el("details", {}, [el("summary", { text: "المنهج" }), el("ul", {}, c.methodology_ar.map((m) => el("li", { text: m })))]));
    }
    if (c.source_priority_ar?.length) {
      box.append(el("details", {}, [el("summary", { text: "ترتيب المصادر" }), el("ol", {}, c.source_priority_ar.map((m) => el("li", { text: m })))]));
    }
    $("#btn-sources").disabled = c.id === "comparative";
  }

  function selectClone(id) {
    state.clone = id;
    state.answers = {};
    renderClones();
    $("#message").placeholder = id === "comparative" ? "المسألة التي تريد مقارنة المناهج فيها…" : "سؤالك…";
  }

  function renderTopics() {
    const sel = $("#topic");
    const groups = {};
    for (const t of state.topics.topics) (groups[t.group] ||= []).push(t);
    for (const [g, list] of Object.entries(groups)) {
      const og = el("optgroup", { label: state.topics.groups[g] || g });
      for (const t of list) og.append(el("option", { value: t.id, text: t.label_ar }));
      sel.append(og);
    }
  }

  // ---------------------------------------------------------------- rendering helpers
  const V_LABEL = { verified: "متحقق منه", partially_verified: "متحقق منه جزئيًّا", unverified: "غير متحقق منه" };
  const badge = (status) => el("span", { class: `badge ${status}`, text: V_LABEL[status] || status });

  function renderCitations(citations) {
    if (!citations?.length) return null;
    const box = el("div", { class: "cites" }, [el("h4", { text: `التوثيق (${citations.length})` })]);
    for (const c of citations) {
      const item = el("div", { class: "cite" }, [
        el("div", { class: "meta" }, [badge(c.verification_status), el("span", { class: "badge", text: c.source_type }), el("span", { class: "rec", text: c.record_id })]),
        el("div", { class: "lines" }, c.lines.map((l) => el("div", { text: l }))),
      ]);
      if (c.url) item.append(el("div", {}, [el("a", { href: c.url, target: "_blank", rel: "noopener", text: "فتح المصدر" })]));
      if (c.quote) item.append(el("blockquote", { text: c.quote }));
      if (c.verification_notes) item.append(el("div", { class: "vnote", text: c.verification_notes }));
      box.append(item);
    }
    return box;
  }

  function renderCalculation(calc) {
    if (!calc) return null;
    const box = el("div", { class: "calc" }, [el("h4", { text: "الحساب الرياضي (منفصل عن الحكم الشرعي)" })]);
    if (calc.steps?.length) box.append(el("ol", {}, calc.steps.map((s) => el("li", { text: s }))));
    if (typeof calc.zakat_amount === "number") {
      box.append(el("div", { class: "result", text: calc.due === false ? "النتيجة: لا يبلغ النصاب وفق هذا المنهج" : `النتيجة الحسابية: ${fmt(calc.zakat_amount)} ${calc.currency || ""}` }));
    }
    if (calc.total_saa) box.append(el("div", { class: "result", text: `المجموع: ${calc.total_saa} صاع` }));
    if (calc.market) {
      const m = calc.market;
      const parts = [];
      if (m.gold) parts.push(`الذهب: ${fmt(m.gold_per_gram)} ${m.currency}/جم — المصدر: ${m.gold.source}، بتاريخ: ${m.gold.as_of}`);
      if (m.silver) parts.push(`الفضة: ${fmt(m.silver_per_gram)} ${m.currency}/جم — المصدر: ${m.silver.source}، بتاريخ: ${m.silver.as_of}`);
      if (m.fx) parts.push(`سعر الصرف: 1 USD = ${fmt(m.fx.usd_to_currency)} ${m.currency} — ${m.fx.source} (${m.fx.as_of})`);
      if (m.note) parts.push(m.note);
      box.append(el("div", { class: "market" }, parts.map((p) => el("div", { text: p }))));
    }
    return box;
  }

  const fmt = (x) => (typeof x === "number" ? x.toLocaleString("en-US", { maximumFractionDigits: 2 }) : x);

  function renderNarrative(n) {
    if (!n?.used || !n.text) return null;
    return el("div", { class: "section narrative" }, [
      el("h4", {}, ["صياغة مساعدة ", el("span", { class: "badge llm", text: n.model || "LLM" })]),
      el("p", { text: n.text }),
      el("div", { class: "hint", text: "هذه إعادة صياغة آلية مقيّدة بالسجلات أدناه ولا تُضيف عليها؛ المعتمد هو الأقسام والمصادر التالية." }),
    ]);
  }

  function renderSections(sections) {
    return sections.map((s) => el("div", { class: `section ${s.kind}` }, [
      el("h4", { text: s.title }),
      el("p", { text: s.text }),
      s.record_id ? el("div", { class: "rec", text: `السجل: ${s.record_id}` }) : null,
    ]));
  }

  // ---------------------------------------------------------------- clarifying questions
  function renderQuestions(data, container) {
    const form = el("form", { class: "questions" });
    const inputs = [];
    for (const q of data.questions) {
      const node = $("#tpl-question").content.firstElementChild.cloneNode(true);
      $(".q-text", node).textContent = q.question;
      $(".q-why", node).textContent = q.why ? `لماذا؟ ${q.why}` : "";
      const inp = $(".q-input", node);
      if (q.kind === "choice") {
        for (const o of q.options) {
          inp.append(el("label", { class: "opt" }, [el("input", { type: "radio", name: q.id, value: o.value }), o.label]));
        }
      } else {
        const input = el("input", { type: q.kind === "number" ? "number" : "text", name: q.id, step: "any", inputmode: q.kind === "number" ? "decimal" : "text", placeholder: q.unit || "" });
        inp.append(input);
        if (q.unit) inp.append(el("span", { class: "hint", text: q.unit }));
      }
      inputs.push(q);
      form.append(node);
    }
    const skip = el("button", { type: "button", class: "ghost small", text: "لا أعرف / تخطَّ ما لم أُجب عنه" });
    const submit = el("button", { type: "submit", text: "إرسال الإجابات" });
    form.append(el("div", { class: "q-actions" }, [submit, skip, el("span", { class: "hint", text: "لا يُفترض شيء لم تُجب عنه؛ إن تُرك سؤال ضروري فلن يُحسب أو يُحكم به." })]));

    const collect = () => {
      const fd = new FormData(form);
      const out = {};
      for (const q of inputs) {
        const v = fd.get(q.id);
        if (v == null || v === "") continue;
        out[q.id] = q.kind === "number" ? Number(v) : v;
      }
      return out;
    };
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      const got = collect();
      if (!Object.keys(got).length) return;
      form.querySelectorAll("input,button").forEach((n) => (n.disabled = true));
      Object.assign(state.answers, got);
      ask(true);
    });
    skip.addEventListener("click", () => {
      const got = collect();
      for (const q of inputs) if (!(q.id in got)) got[q.id] = "__skip__";
      form.querySelectorAll("input,button").forEach((n) => (n.disabled = true));
      Object.assign(state.answers, got);
      ask(true);
    });
    container.append(form);
  }

  // ---------------------------------------------------------------- answer rendering
  function renderAnswer(data) {
    const msg = el("div", { class: "msg bot" });
    const meta = el("div", { class: "meta" }, [el("strong", { text: data.clone.name_ar })]);
    if (data.topic) meta.append(el("span", { class: "badge topic", text: data.topic.label_ar }));
    meta.append(el("span", { class: "badge", text: data.stage === "clarify" ? "أسئلة توضيحية" : "إجابة موثقة" }));
    msg.append(meta);
    if (data.clone.disclaimer_ar) msg.append(el("div", { class: "disclaimer", text: data.clone.disclaimer_ar }));
    if (data.notes?.length) msg.append(el("ul", { class: "notes" }, data.notes.map((n) => el("li", { text: n }))));

    if (data.stage === "clarify") {
      if (data.questions?.length) {
        msg.append(el("p", { text: "قبل الحكم أو الحساب، أحتاج إلى المعلومات التالية لأنها تؤثر في النتيجة:" }));
        renderQuestions(data, msg);
      }
      if (!data.topic) {
        msg.append(el("p", { class: "hint", text: "يمكنك اختيار الموضوع من قائمة «الموضوع» ثم إعادة الإرسال." }));
      }
    } else {
      const narrative = renderNarrative(data.narrative);
      if (narrative) msg.append(narrative);
      msg.append(...renderSections(data.sections || []));
      const calc = renderCalculation(data.calculation);
      if (calc) msg.append(calc);
      const cites = renderCitations(data.citations);
      if (cites) msg.append(cites);
    }
    return msg;
  }

  function renderCompare(data) {
    const msg = el("div", { class: "msg bot" });
    const meta = el("div", { class: "meta" }, [el("strong", { text: "المقارن في فقه الزكاة" })]);
    if (data.topic) meta.append(el("span", { class: "badge topic", text: data.topic.label_ar }));
    msg.append(meta);
    if (data.disclaimer_ar) msg.append(el("div", { class: "disclaimer", text: data.disclaimer_ar }));
    const narrative = renderNarrative(data.narrative);
    if (narrative) msg.append(narrative);

    const table = el("table", { class: "cmp-table" }, [
      el("thead", {}, [el("tr", {}, [el("th", { text: "المنهج" }), el("th", { text: "الحكم / الرأي كما ورد في مصادره" }), el("th", { text: "المصدر والتحقق" })])]),
    ]);
    const tbody = el("tbody");
    for (const f of data.findings) {
      const ruling = el("td", {}, [el("div", { text: f.ruling || "—" })]);
      if (f.statement) ruling.append(el("div", { class: "stmt", text: `قول: ${f.statement}` }));
      if (f.is_mashhur === true) ruling.append(el("div", { class: "hint", text: "هذا هو المشهور في المذهب." }));
      if (f.other_opinions?.length) ruling.append(el("div", { class: "hint" }, [el("strong", { text: "أقوال أخرى: " }), f.other_opinions.join(" | ")]));
      if (f.reason_for_difference) ruling.append(el("div", { class: "hint", text: `سبب الاختلاف (كما في السجل): ${f.reason_for_difference}` }));
      const src = el("td");
      if (f.found) {
        src.append(badge(f.verification_status || "unverified"));
        if (f.citation?.lines) src.append(el("div", { class: "src" }, f.citation.lines.map((l) => el("div", { text: l }))));
        if (f.citation?.url) src.append(el("a", { href: f.citation.url, target: "_blank", rel: "noopener", text: "فتح المصدر" }));
        if (f.citation?.quote) src.append(el("blockquote", { text: f.citation.quote }));
      } else {
        src.append(el("span", { class: "hint", text: "لا سجل موثق كافٍ في مصادر هذا المنهج." }));
      }
      tbody.append(el("tr", {}, [el("td", {}, [el("strong", { text: f.label })]), ruling, src]));
    }
    table.append(tbody);
    msg.append(table);

    if (data.parameters?.length) {
      msg.append(el("h4", { text: "المواقف المقننة في المسألة" }));
      for (const p of data.parameters) {
        const box = el("div", { class: `param ${p.agreement === true ? "agree" : p.agreement === false ? "differ" : ""}` }, [el("strong", { text: p.label })]);
        for (const [cid, pos] of Object.entries(p.positions)) {
          const label = data.findings.find((f) => f.clone === cid)?.label || cid;
          box.append(el("div", { class: "pos", text: `${label}: ${pos.label || pos.value}${pos.note ? ` — ${pos.note}` : ""}` }));
        }
        msg.append(box);
      }
    }
    const list = (title, items) => items?.length ? [el("h4", { text: title }), el("ul", { class: "cmp-list" }, items.map((x) => el("li", { text: x })))] : [];
    msg.append(...list("نقاط الاتفاق", data.agreement_points), ...list("نقاط الاختلاف", data.difference_points), ...list("سبب الاختلاف (كما نُصّ عليه في السجلات)", data.reasons));
    return msg;
  }

  // ---------------------------------------------------------------- send
  function manualPrices() {
    const g = parseFloat($("#gold-price").value);
    const s = parseFloat($("#silver-price").value);
    return g > 0 && s > 0 ? { gold_per_gram: g, silver_per_gram: s } : null;
  }

  async function ask(followUp = false) {
    const spinner = el("div", { class: "msg spinner", text: "جارٍ البحث في المصادر الموثقة…" });
    thread.append(spinner);
    scrollDown();
    $("#btn-send").disabled = true;
    try {
      let data;
      if (state.clone === "comparative") {
        data = await api("/api/compare", { message: state.message, topic: state.topic || null });
        spinner.replaceWith(renderCompare(data));
      } else {
        data = await api("/api/ask", { clone: state.clone, message: state.message, topic: state.topic || null, answers: state.answers, manual_prices: manualPrices() });
        state.answers = { ...state.answers, ...(data.answers || {}) };
        if (data.topic && !state.topic) state.topic = data.topic.id;
        spinner.replaceWith(renderAnswer(data));
      }
    } catch (err) {
      spinner.replaceWith(el("div", { class: "msg error", text: `تعذّر الحصول على الإجابة: ${err.message}` }));
    } finally {
      $("#btn-send").disabled = false;
      scrollDown();
    }
  }

  $("#ask-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const text = $("#message").value.trim();
    if (!text) return;
    state.message = text;
    state.topic = $("#topic").value;
    state.answers = {};
    thread.append(el("div", { class: "msg user", text }));
    $("#message").value = "";
    ask(false);
  });
  $("#message").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#ask-form").requestSubmit(); }
  });

  $("#btn-reset").addEventListener("click", () => {
    state.answers = {}; state.message = ""; state.topic = "";
    $("#topic").value = "";
    thread.querySelectorAll(".msg:not(.system)").forEach((n) => n.remove());
  });

  $("#btn-market").addEventListener("click", async () => {
    const info = $("#market-info");
    info.textContent = "جارٍ جلب السعر…";
    try {
      const m = await api("/api/market?currency=USD");
      info.textContent = `الذهب ${fmt(m.gold_per_gram)} USD/جم (${m.gold.source}، ${m.gold.as_of}) — الفضة ${fmt(m.silver_per_gram)} USD/جم (${m.silver.source}، ${m.silver.as_of})`;
    } catch (err) {
      info.textContent = `السعر المباشر غير متاح (${err.message}). أدخل السعر يدويًّا.`;
    }
  });

  // ---------------------------------------------------------------- bibliography dialog
  $("#btn-sources").addEventListener("click", async () => {
    const c = state.clones.find((x) => x.id === state.clone);
    const dlg = $("#sources-dialog");
    $("#sources-title").textContent = `مصادر: ${c?.name_ar || state.clone}`;
    const body = $("#sources-body");
    body.textContent = "…";
    dlg.showModal();
    try {
      const bib = await api(`/api/bibliography/${state.clone}`);
      body.innerHTML = "";
      if (!bib.length) body.append(el("p", { class: "hint", text: "لا توجد قائمة مصادر لهذا المنهج." }));
      for (const s of bib) {
        body.append(el("div", { class: "bib" }, [
          el("div", { class: "t", text: `${s.title} — ${s.author}` }),
          el("div", { class: "m", text: [s.source_type, s.edition, s.publisher, s.year, s.digital_source].filter(Boolean).join(" · ") }),
          s.url ? el("a", { href: s.url, target: "_blank", rel: "noopener", text: s.url }) : null,
          s.notes ? el("div", { class: "m", text: s.notes }) : null,
        ]));
      }
    } catch (err) {
      body.append(el("div", { class: "error", text: err.message }));
    }
  });
  $("#btn-close-sources").addEventListener("click", () => $("#sources-dialog").close());

  // ---------------------------------------------------------------- boot
  (async () => {
    try {
      [state.clones, state.topics] = await Promise.all([api("/api/clones"), api("/api/topics")]);
      renderClones();
      renderTopics();
    } catch (err) {
      thread.append(el("div", { class: "msg error", text: `تعذّر تحميل الإعدادات: ${err.message}` }));
    }
  })();
})();
