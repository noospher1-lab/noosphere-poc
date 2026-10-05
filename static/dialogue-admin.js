// Вынесено из dialogue-admin.html: CSP script-src 'self' не допускает встроенных скриптов.
const $ = (s, r=document) => r.querySelector(s);
const $$ = (s, r=document) => [...r.querySelectorAll(s)];

function getToken() {
  let t = localStorage.getItem("admin_token");
  if (!t) {
    t = prompt("X-Admin-Token (из .env сервера, ADMIN_TOKEN):") || "";
    if (t) localStorage.setItem("admin_token", t);
  }
  return t;
}

async function api(path) {
  const r = await fetch(path, { headers: { "X-Admin-Token": getToken() } });
  if (r.status === 403) {
    localStorage.removeItem("admin_token");   // stale/wrong token — ask again next call
  }
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || String(r.status));
  return r.json();
}

const CRIT_LABELS = {
  problem_framing: ["Постановка проблемы", 20],
  information_integration: ["Интеграция информации", 20],
  reasoning_under_revision: ["Мышление под давлением", 20],
  handling_disagreement: ["Работа с несогласием", 15],
  cognitive_patterns: ["Когнитивные паттерны", 15],
  decision_quality: ["Качество решения", 10],
};

let selectedId = null;

function fmtDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleString("ru-RU", { day:"2-digit", month:"2-digit", year:"numeric",
    hour:"2-digit", minute:"2-digit" });
}

async function loadList() {
  const box = $("#sidebar");
  try {
    const items = await api("/api/dev/dialogues");
    box.innerHTML = "";
    if (!items.length) { box.innerHTML = '<div class="empty">диалогов пока нет</div>'; return; }
    for (const it of items) {
      const row = document.createElement("div");
      row.className = "drow";
      row.dataset.id = it.author_id;
      row.innerHTML = `<div class="u"></div>
        <div class="meta"><span class="phase-badge phase-${escHtml(it.phase)}"></span>
        · ход ${it.n_turns ?? 0} · <span class="upd"></span></div>`;
      row.querySelector(".u").textContent = it.name || it.username || ("#" + it.author_id);
      row.querySelector(".phase-badge").textContent = it.phase;
      row.querySelector(".upd").textContent = fmtDate(it.updated_at);
      row.onclick = () => selectDialogue(it.author_id);
      box.appendChild(row);
    }
  } catch (e) {
    box.innerHTML = `<div class="empty err">ошибка: ${escHtml(e.message)}
      <div class="muted" style="margin-top:10px">
        <a href="#" id="retryToken">ввести токен заново</a>
      </div></div>`;
    const retry = $("#retryToken");
    if (retry) retry.onclick = (ev) => { ev.preventDefault(); loadList(); };
  }
}

async function selectDialogue(authorId) {
  selectedId = authorId;
  $$(".drow").forEach(r => r.classList.toggle("sel", +r.dataset.id === authorId));
  const main = $("#main");
  main.innerHTML = '<div class="empty">загрузка…</div>';
  try {
    const d = await api(`/api/dev/dialogues/${authorId}`);
    renderDialogue(d);
  } catch (e) {
    main.innerHTML = `<div class="empty err">${escHtml(e.message)}</div>`;
  }
}

function voteLabel(v) { return v === "yes" ? "ДА — поддерживаю" : "НЕТ — против"; }

function renderDialogue(d) {
  const main = $("#main");
  main.innerHTML = "";

  const head = document.createElement("div");
  head.className = "card";
  head.innerHTML = `<div class="section-title">тема</div><div>${escHtml(d.topic)}</div>
    <div class="muted" style="margin-top:6px">
      фаза: <span class="phase-badge phase-${escHtml(d.phase)}">${escHtml(d.phase)}</span>
      · ход ${escHtml(d.user_turns)} из ${escHtml(d.max_turns)} (итог доступен с ${escHtml(d.min_turns)})
    </div>`;
  main.appendChild(head);

  if (d.pre) {
    const pre = document.createElement("div");
    pre.className = "card";
    pre.innerHTML = `<div class="section-title">стартовая позиция</div>
      <div>${voteLabel(d.pre.vote)} · <span class="muted">${escHtml(d.pre.confidence)}</span></div>
      <div class="muted" style="margin-top:6px; white-space:pre-wrap"></div>`;
    pre.querySelector("div:last-child").textContent = d.pre.reasoning;
    main.appendChild(pre);
  }

  const chatWrap = document.createElement("div");
  chatWrap.innerHTML = '<div class="section-title">диалог</div>';
  const chat = document.createElement("div");
  chat.id = "chat";
  for (const t of (d.turns || [])) {
    if (t.content.startsWith("[")) continue;
    if (t.meta === "inform_offer_pending") {
      const o = document.createElement("div");
      o.className = "offer";
      o.innerHTML = '<div class="who">💡 предложение информации (не отвечено)</div><div class="tx"></div>';
      o.querySelector(".tx").textContent = t.content;
      chat.appendChild(o);
      continue;
    }
    const el = document.createElement("div");
    el.className = "turn " + (t.role === "ai" ? "ai" : "user");
    el.innerHTML = '<div class="who"></div><div class="tx"></div>';
    el.querySelector(".who").textContent = t.role === "ai" ? "собеседник" : "пользователь";
    el.querySelector(".tx").textContent = t.content;
    chat.appendChild(el);
  }
  chatWrap.appendChild(chat);
  main.appendChild(chatWrap);

  if (d.post) {
    const post = document.createElement("div");
    post.className = "card";
    post.innerHTML = `<div class="section-title">финальная позиция</div>
      <div>${voteLabel(d.post.vote)} · <span class="muted">${escHtml(d.post.confidence)}</span></div>
      <div class="muted reasoning" style="margin-top:6px; white-space:pre-wrap"></div>
      <div class="section-title" style="margin-top:10px">рефлексия</div>
      <div class="reflection" style="white-space:pre-wrap"></div>`;
    post.querySelector(".reasoning").textContent = d.post.reasoning;
    post.querySelector(".reflection").textContent = d.post.reflection;
    main.appendChild(post);
  }

  if (d.scores) {
    const s = d.scores;
    const res = document.createElement("div");
    res.innerHTML = '<div class="section-title" style="margin-top:16px">результаты</div>';
    const totalCard = document.createElement("div");
    totalCard.className = "card";
    totalCard.style.cssText = "display:flex;align-items:center;gap:20px";
    totalCard.innerHTML = `<div class="total">${escHtml(s.total ?? "—")}</div>
      <div class="muted"></div>`;
    totalCard.querySelector(".muted").textContent = s.overall_summary || "";
    res.appendChild(totalCard);

    for (const [key, [label, max]] of Object.entries(CRIT_LABELS)) {
      const c = (s.criteria || {})[key];
      if (!c) continue;
      const cd = document.createElement("div");
      cd.className = "crit card";
      cd.innerHTML = `<b>${label}</b> <span class="muted">${escHtml(c.score)} / ${max}</span>
        <div class="bar"><span style="width:${+((c.score / max) * 100) || 0}%"></span></div>
        <div class="why"></div>`;
      cd.querySelector(".why").textContent = c.why || "";
      for (const q of c.evidence || []) {
        const ev = document.createElement("div");
        ev.className = "ev"; ev.textContent = "«" + q + "»";
        cd.appendChild(ev);
      }
      if (c.what_would_raise_score) {
        const w = document.createElement("div");
        w.className = "muted"; w.style.marginTop = "6px";
        w.textContent = "↑ что подняло бы балл: " + c.what_would_raise_score;
        cd.appendChild(w);
      }
      res.appendChild(cd);
    }

    const meta = s.meta_patterns || [];
    if (meta.length) {
      const mbox = document.createElement("div");
      mbox.className = "card";
      mbox.innerHTML = '<div class="section-title">мета-паттерны</div>';
      for (const m of meta) {
        const p = document.createElement("div");
        p.className = "muted"; p.textContent = "· " + m;
        mbox.appendChild(p);
      }
      res.appendChild(mbox);
    }
    main.appendChild(res);
  } else if (d.phase !== "results") {
    const note = document.createElement("div");
    note.className = "card muted";
    note.textContent = "Итог ещё не подведён — пользователь не завершил finalize.";
    main.appendChild(note);
  }
}

loadList();
