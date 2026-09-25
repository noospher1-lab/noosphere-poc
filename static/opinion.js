// Карта мнений (vault: decisions/2026-09-25-opinion-map).
// Экран обсуждения (opinion.html?root=N) и экран позиции (position.html?id=N).
// Числа приходят готовыми из /api/opinion/*: здесь только показ и поток ответа.
// Любое число и любой довод ведут к тексту узла (/n/ID) — рассуждение в одно касание.
"use strict";

const RU = new Intl.NumberFormat("ru-RU");
const fmt = (n) => RU.format(n || 0);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const $ = (sel, root = document) => root.querySelector(sel);
const qs = new URLSearchParams(location.search);

async function api(path, opts = {}) {
  const r = await fetch(path, {
    credentials: "same-origin",
    headers: opts.body ? { "Content-Type": "application/json" } : {},
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error(data.detail || `ошибка ${r.status}`), { status: r.status });
  return data;
}

let ME = null;
async function whoami() {
  try { ME = await api("/api/auth/me"); } catch { ME = null; }
  return ME;
}

function people(n) {
  const m10 = n % 10, m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return "человек";
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return "человека";
  return "человек";
}

function tag(card) {
  if (!card) return "";
  const rel = card.rel || card.kind;
  return card.label ? `<span class="tag ${esc(rel)}">${esc(card.label)}</span>` : "";
}

function nodeLink(card, cls = "") {
  if (!card) return "";
  return `<a class="${cls}" href="/n/${card.id}">${esc(card.text)}</a>`;
}

function short(text, n = 90) {
  const t = String(text || "");
  return t.length > n ? t.slice(0, n - 1).trimEnd() + "…" : t;
}

function bar3(x, cls = "") {
  const total = (x.stood || 0) + (x.unchecked || 0) + (x.converted || 0) || 1;
  const pct = (v) => `${(100 * (v || 0) / total).toFixed(2)}%`;
  return `<div class="bar3 ${cls}" role="img" aria-label="устояли ${fmt(x.stood)}, не проверены ${fmt(x.unchecked)}, переубеждены ${fmt(x.converted)}">
    <span class="stood" style="width:${pct(x.stood)}"></span>
    <span class="unchecked" style="width:${pct(x.unchecked)}"></span>
    <span class="converted" style="width:${pct(x.converted)}"></span></div>`;
}

function periodTabs(active, onPick) {
  const wrap = document.createElement("div");
  wrap.className = "tabs";
  wrap.setAttribute("role", "group");
  wrap.setAttribute("aria-label", "Период");
  for (const [id, label] of [["7", "7 дней"], ["30", "30 дней"], ["all", "всё время"]]) {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = label;
    b.setAttribute("aria-pressed", String(id === active));
    b.onclick = () => onPick(id);
    wrap.appendChild(b);
  }
  return wrap;
}

const PERIOD_WORD = { "7": "за 7 дней", "30": "за 30 дней", all: "за всё время" };

// ------------------------------------------------------------ поток ответа
// Нажал на элемент → выбрал действие → написал → маршрутизатор предлагает:
// «это уже сказано — присоединиться?» или место (к чему, какой тип, какая
// позиция). Решает человек: ИИ и система ничего не публикуют сами.
const ACTIONS = [
  ["support", "согласен"], ["qualify", "уточняю"], ["refute", "возражаю"], ["question", "спрашиваю"],
];

function composer({ topic, target, action = "refute", anchor = null, note = "" }) {
  if (!ME) { location.href = "/?login=1"; return; }
  let dlg = $("#composer");
  if (!dlg) {
    dlg = document.createElement("dialog");
    dlg.id = "composer";
    document.body.appendChild(dlg);
  }
  const quote = anchor ? `<q>${esc(anchor.quote)}</q>` : "";
  dlg.innerHTML = `<form class="dlg" method="dialog">
    <h3>${target ? "Ответ" : "Моей позиции здесь нет"}</h3>
    ${target ? `<div class="target">${tag(target)} ${quote || esc(short(target.text, 220))}</div>` : ""}
    ${note ? `<p class="msg">${esc(note)}</p>` : ""}
    ${target ? `<div class="choices" role="radiogroup" aria-label="Что вы делаете">
      ${ACTIONS.map(([v, l]) => `<label><input type="radio" name="act" value="${v}" ${v === action ? "checked" : ""}> ${l}</label>`).join("")}
    </div>` : `<p class="msg">Напишите свою позицию своими словами. Она появится в дереве обсуждения; позицией на карте она станет, когда вокруг неё соберутся люди.</p>`}
    <label for="cmp-text" class="muted">Текст</label>
    <textarea id="cmp-text" required maxlength="4000"></textarea>
    <div id="cmp-sugg"></div>
    <div class="btns">
      <button type="button" class="btn primary" id="cmp-next">Дальше</button>
      <button type="button" class="btn" id="cmp-cancel">Отмена</button>
    </div>
    <p class="msg" id="cmp-msg" role="status"></p>
  </form>`;
  dlg.showModal();
  $("#cmp-cancel", dlg).onclick = () => dlg.close();
  const sugg = $("#cmp-sugg", dlg);
  const msg = $("#cmp-msg", dlg);
  $("#cmp-next", dlg).onclick = async () => {
    const text = $("#cmp-text", dlg).value.trim();
    if (!text) { msg.textContent = "Напишите текст."; return; }
    const act = target ? dlg.querySelector('input[name="act"]:checked').value : "support";
    msg.textContent = "ищу, не сказано ли это уже…";
    let route;
    try {
      route = await api("/api/opinion/route", { method: "POST", body: {
        topic_root_id: topic, target_id: target ? target.id : null, action: act, text } });
    } catch (e) { msg.textContent = e.message; return; }
    msg.textContent = route.embed ? "" : "Поиск похожего сейчас недоступен — можно публиковать.";
    const dup = route.duplicates || [];
    const place = route.placement;
    sugg.innerHTML = `
      ${dup.map((d) => `<div class="sugg"><div class="why">Это уже сказано (сходство ${Math.round(d.sim * 100)}%):</div>
        <div>${tag(d)} ${nodeLink(d)}</div>
        <div class="btns"><button type="button" class="btn" data-join="${d.id}">Присоединиться — вместо нового текста</button></div></div>`).join("")}
      <div class="sugg"><div class="why">${dup.length ? "Или опубликовать своё — " : ""}Место в обсуждении:</div>
        <div>${esc(place.edge_label)} → ${place.target ? nodeLink(place.target) : "обсуждение целиком"}</div>
        ${place.position ? `<div class="why">Относится к позиции «${esc(place.position.title)}»</div>` : ""}
        ${place.alternatives && place.alternatives.length ? `<label class="why" for="cmp-where">Другое место:</label>
          <select class="select" id="cmp-where"><option value="">${esc(short(place.target ? place.target.text : "обсуждение", 70))}</option>
          ${place.alternatives.map((a) => `<option value="${a.id}">${esc(short(a.text, 70))}</option>`).join("")}</select>` : ""}
        <div class="btns"><button type="button" class="btn primary" id="cmp-publish">Опубликовать</button></div></div>`;
    for (const b of sugg.querySelectorAll("[data-join]")) {
      b.onclick = async () => {
        try {
          const r = await api("/api/opinion/join", { method: "POST", body: { node_id: Number(b.dataset.join) } });
          msg.textContent = `Вы присоединились. Теперь это сказали ${fmt(r.joins + 1)} ${people(r.joins + 1)}.`;
          setTimeout(() => { dlg.close(); refresh(); }, 1200);
        } catch (e) { msg.textContent = e.message; }
      };
    }
    $("#cmp-publish", dlg).onclick = async () => {
      const alt = $("#cmp-where", dlg);
      const connect = alt && alt.value ? Number(alt.value) : (place.target ? place.target.id : topic);
      const body = { text, connect_to: connect, edge_type: place.edge_type };
      if (act === "question") { body.kind = "question"; body.edge_type = "question"; }
      if (anchor && connect === (target && target.id)) body.anchor = anchor;
      try {
        const r = await api("/api/argument", { method: "POST", body });
        msg.innerHTML = `Опубликовано: <a href="/n/${r.id}">открыть в дереве</a>. В течение часа, пока нет ответов, его можно снять.`;
        $("#cmp-publish", dlg).disabled = true;
        setTimeout(refresh, 800);
      } catch (e) { msg.textContent = e.message; }
    };
  };
}

// выделение фрагмента в тексте возражения → ответ на фрагмент
function fragmentable(el, card, topic) {
  el.addEventListener("mouseup", () => {
    const sel = window.getSelection();
    const quote = sel ? sel.toString().trim() : "";
    if (!quote || quote.length < 3) return;
    const start = card.text.indexOf(quote);
    if (start < 0) return;
    let pop = $("#frag-pop");
    if (!pop) {
      pop = document.createElement("div");
      pop.id = "frag-pop";
      pop.className = "btns";
      el.after(pop);
    }
    pop.innerHTML = `<button type="button" class="btn">Ответить на выделенное</button>`;
    pop.firstChild.onclick = () => {
      pop.remove();
      composer({ topic, target: card, action: "qualify",
                 anchor: { start, end: start + quote.length, quote } });
    };
  });
}

// ---------------------------------------------------------- экран позиции
let STATE = { period: "30", scrub: null };
let refresh = () => {};

async function renderPosition() {
  const pid = Number(qs.get("id"));
  const main = $("main");
  if (!pid) { main.innerHTML = `<p class="note">Не указана позиция.</p>`; return; }
  await whoami();
  refresh = async () => {
    let d;
    try { d = await api(`/api/opinion/position/${pid}?period=${STATE.period}`); }
    catch (e) { main.innerHTML = `<p class="note">${esc(e.message)}</p>`; return; }
    drawPosition(d);
  };
  await refresh();
}

function drawPosition(d) {
  const p = d.position;
  const topic = p.topic.id;
  document.title = `${p.title} — Noosphere`;
  const main = $("main");
  const since = new Date(p.created_at).toLocaleDateString("ru-RU", { day: "numeric", month: "long" });
  let status = "";
  if (p.status === "forming") status = `<p class="note">Позиция складывается: на карту она выйдет, когда в ней встанут трое.</p>`;
  if (p.status === "empty") status = `<p class="note">Сейчас в этой позиции никого нет. Она остаётся — любой может в неё вернуться.</p>`;
  if (p.status === "merged" && p.merged_into) status = `<p class="note">Позиция слита с «<a href="position.html?id=${p.merged_into.id}">${esc(p.merged_into.title)}</a>».</p>`;
  if (p.status === "split") status = `<p class="note">Позиция раскололась на: ${p.parts.map((x) => `<a href="position.html?id=${x.id}">${esc(x.title)}</a>`).join(" · ")}. Кто ещё не выбрал часть, считается здесь с пометкой «не уточнил».</p>`;
  const head = `
    <div class="crumbs"><a href="opinion.html?root=${topic}">${esc(p.topic.title)}</a><span class="sep">›</span><span>позиция</span></div>
    <h1>${esc(p.title)}</h1>
    <div class="meta">Позиция собрана из ${fmt(p.members)} доводов · существует с ${since} · держится, пока в ней есть хотя бы один человек</div>
    ${status}`;
  if (d.blind) {
    main.innerHTML = head + `<p class="note">Сначала напишите свой ответ в обсуждении — после этого откроются числа.
      Так ваш ответ не подстраивается под большинство. <a href="/n/${topic}">К обсуждению</a></p>
      <p class="caption">${esc(d.caption)}</p>`;
    return;
  }
  const n = d.numbers;
  const delta = d.delta;
  main.innerHTML = head + `
    <section class="card" style="margin-top:18px" aria-label="Числа позиции">
      <div class="nums">
        <div class="num"><div class="k">Сейчас в позиции</div><div class="v">${fmt(n.in_now)}</div>
          <div class="d ${delta > 0 ? "up" : delta < 0 ? "down" : ""}">${delta > 0 ? "+" : ""}${fmt(delta)} ${PERIOD_WORD[d.period]}</div></div>
        <div class="num"><div class="k"><span class="dot stood"></span>Устояли</div><div class="v">${fmt(n.stood)}</div>
          <div class="d">видели главные возражения и ответили «не убедило» или «частично»</div></div>
        <div class="num"><div class="k"><span class="dot unchecked"></span>Не проверены</div><div class="v">${fmt(n.unchecked)}</div>
          <div class="d">на главные возражения ещё не ответили</div></div>
        <div class="num"><div class="k"><span class="dot converted"></span>Переубеждены</div><div class="v">${fmt(n.converted)}</div>
          <div class="d">были здесь и ушли в другие позиции</div></div>
      </div>
      ${bar3(n)}
      <div class="msg">Все, кто когда-либо держался этой позиции: ${fmt(n.ever)}</div>
    </section>
    <section id="personal"></section>
    <div class="grid2">
      <section class="card" aria-labelledby="mv-h"><div class="row-head"><h2 id="mv-h">Движение</h2><span id="tabs"></span></div>
        <div id="movement"></div></section>
      <section class="card" aria-labelledby="ch-h"><div class="row-head"><h2 id="ch-h">Состав во времени</h2></div>
        <div id="chart" class="chart"></div></section>
    </div>
    <div class="grid2">
      <section class="card" aria-labelledby="cx-h"><h2 id="cx-h">Точки расхождения</h2>
        <p class="sub" style="margin-top:4px">Вопросы и подрывы, на которых сторонники делятся или уходят</p><div id="cruxes"></div></section>
      <section class="card" aria-labelledby="mo-h"><h2 id="mo-h">Что сдвигало людей</h2>
        <p class="sub" style="margin-top:4px">Доводы по числу вызванных переходов, а не по реакциям</p><div id="movers"></div></section>
    </div>
    <section class="card" style="margin-top:18px" aria-labelledby="er-h" id="errors-card"><h2 id="er-h">Признанные ошибки</h2><div id="errors" style="margin-top:10px"></div></section>
    <p class="caption">${esc(d.caption)}</p>`;
  $("#tabs").appendChild(periodTabs(d.period, (id) => { STATE.period = id; refresh(); }));
  drawPersonal(d);
  drawMovement(d);
  drawChart(d);
  drawCruxes(d);
  drawMovers(d);
  drawErrors(d);
}

function drawPersonal(d) {
  const box = $("#personal");
  const p = d.position;
  const me = d.personal;
  const open = ["forming", "active", "empty"].includes(p.status);
  if (!ME) {
    box.innerHTML = open ? `<div class="card personal"><span class="lbl">Войдите, чтобы встать в эту позицию или ответить на её возражения.</span></div>` : "";
    return;
  }
  if (!me) {
    if (!open) { box.innerHTML = ""; return; }
    box.innerHTML = `<div class="card personal"><div class="lbl">Вы не в этой позиции.</div>
      <div class="btns"><button type="button" class="btn primary" id="join-pos">Это моя позиция</button></div>
      <div class="msg" id="pmsg" role="status"></div></div>`;
    $("#join-pos").onclick = async () => {
      try {
        await api("/api/opinion/stance", { method: "POST", body: { topic_root_id: p.topic.id, position_id: p.id } });
        refresh();
      } catch (e) { $("#pmsg").textContent = e.message; }
    };
    return;
  }
  if (me.unclarified) {
    box.innerHTML = `<div class="card personal"><div class="lbl">Позиция раскололась — уточните, какая часть ваша:</div>
      <div class="btns">${me.choices.map((c) => `<button type="button" class="btn" data-part="${c.id}">${esc(c.title)}</button>`).join("")}</div>
      <div class="msg" id="pmsg" role="status"></div></div>`;
    for (const b of box.querySelectorAll("[data-part]")) {
      b.onclick = async () => {
        await api("/api/opinion/stance", { method: "POST", body: { topic_root_id: p.topic.id, position_id: Number(b.dataset.part) } });
        location.href = `position.html?id=${b.dataset.part}`;
      };
    }
    return;
  }
  const o = me.objection;
  if (!o) {
    box.innerHTML = `<div class="card personal"><div class="lbl">Вы в этой позиции · все главные возражения вы уже видели и ответили на них.</div></div>`;
    return;
  }
  box.innerHTML = `<div class="card personal">
    <div class="lbl">Вы в этой позиции · главное возражение, которое вы ещё не видели</div>
    <div class="objection">${tag(o)}<div class="text" id="obj-text">${esc(o.text)}</div></div>
    <div class="msg">Этот довод увёл отсюда ${fmt(o.led_away)} ${people(o.led_away)}. ${fmt(o.stayed)} прочитали его и остались. <a href="/n/${o.id}">Открыть в графе</a></div>
    <div class="btns" id="obj-btns">
      <button type="button" class="btn" data-r="not_convinced">Не убедило</button>
      <button type="button" class="btn" data-r="partial">Частично — уточню</button>
      <button type="button" class="btn warn" data-r="convinced">Убедило</button>
    </div>
    <div class="msg" id="pmsg" role="status"></div></div>`;
  fragmentable($("#obj-text"), o, p.topic.id);
  api("/api/opinion/exposure", { method: "POST", body: { node_id: o.id, response: "shown" } }).catch(() => {});
  for (const b of box.querySelectorAll("[data-r]")) {
    b.onclick = () => answerObjection(d, o, b.dataset.r);
  }
}

async function answerObjection(d, o, response) {
  const p = d.position;
  const msg = $("#pmsg");
  try { await api("/api/opinion/exposure", { method: "POST", body: { node_id: o.id, response } }); }
  catch (e) { msg.textContent = e.message; return; }
  const undo = `<button type="button" class="btn link" id="undo">Отменить</button>`;
  const btns = $("#obj-btns");
  if (response === "not_convinced") {
    btns.innerHTML = `<button type="button" class="btn" id="why">Объяснить почему</button>${undo}`;
    msg.textContent = "Учтено: вы среди устоявших. Объяснение станет доводом в обсуждении.";
    $("#why").onclick = () => composer({ topic: p.topic.id, target: o, action: "refute" });
  } else if (response === "partial") {
    btns.innerHTML = `<button type="button" class="btn" id="why">Написать уточнение</button>${undo}`;
    msg.textContent = "Выделите фрагмент возражения, с которым согласны не полностью, или ответьте на него целиком.";
    $("#why").onclick = () => composer({ topic: p.topic.id, target: o, action: "qualify" });
  } else {
    const me = await api(`/api/opinion/me/${p.topic.id}`);
    const opts = me.open_positions.filter((x) => x.id !== p.id);
    btns.innerHTML = undo;
    msg.innerHTML = `Выберите позицию, в которую переходите. Переход запишется с этим доводом как причиной.
      <div class="poslist">${opts.map((x) => `<button type="button" class="btn" data-to="${x.id}">${esc(x.title)}</button>`).join("")}
      <button type="button" class="btn" data-to="">Моей позиции здесь нет — выйти без позиции</button></div>`;
    for (const b of msg.querySelectorAll("[data-to]")) {
      b.onclick = async () => {
        const to = b.dataset.to ? Number(b.dataset.to) : null;
        await api("/api/opinion/stance", { method: "POST", body: {
          topic_root_id: p.topic.id, position_id: to, cause_node_id: o.id } });
        if (to) location.href = `position.html?id=${to}`;
        else composer({ topic: p.topic.id, target: null });
      };
    }
  }
  $("#undo").onclick = async () => {
    await api("/api/opinion/undo", { method: "POST", body: { node_id: o.id } });
    refresh();
  };
}

function flowRows(side, max, dir) {
  if (!side.groups.length && !side.rest) return `<div class="muted">никого ${PERIOD_WORD[STATE.period]}</div>`;
  const rows = side.groups.map((g) => {
    const name = g.position_id ? `<a href="position.html?id=${g.position_id}">${esc(g.title)}</a>`
      : (dir === "in" ? "Новые участники без прежней позиции" : "Ушли без позиции");
    return `<div class="flow ${dir}"><div class="top"><span>${name}</span><span class="n">${fmt(g.n)}</span></div>
      <div class="track"><span style="width:${Math.max(2, Math.round(100 * g.n / max))}%"></span></div>
      ${g.cause ? `<div class="via">главный довод: «${nodeLink({ id: g.cause.id, text: short(g.cause.text, 110) })}»</div>` : ""}</div>`;
  }).join("");
  return rows + (side.rest ? `<div class="rest">ещё ${fmt(side.rest)} — небольшими группами</div>` : "");
}

function drawMovement(d) {
  const m = d.movement;
  const max = Math.max(1, ...m.in.groups.map((g) => g.n), ...m.out.groups.map((g) => g.n));
  $("#movement").innerHTML = `
    <div class="flow-h"><span>Пришли</span><span>${fmt(m.in.total)}</span></div>${flowRows(m.in, max, "in")}
    <div class="sep-line"></div>
    <div class="flow-h"><span>Ушли</span><span>${fmt(m.out.total)}</span></div>${flowRows(m.out, max, "out")}`;
}

function drawChart(d) {
  const s = d.series;
  const box = $("#chart");
  if (s.length < 2) { box.innerHTML = `<p class="muted">История появится через день.</p>`; return; }
  const W = 560, H = 230, L = 44, B = 22;
  const maxY = Math.max(1, ...s.map((x) => x.stood + x.unchecked + x.converted));
  const x = (i) => L + (W - L - 6) * i / (s.length - 1);
  const y = (v) => H - B - (H - B - 8) * v / maxY;
  const area = (lo, hi) => {
    const top = s.map((p, i) => `${x(i)},${y(hi(p))}`).join(" ");
    const bot = s.map((p, i) => `${x(i)},${y(lo(p))}`).reverse().join(" ");
    return `${top} ${bot}`;
  };
  const ticks = [0, Math.round(maxY / 2), maxY];
  const lbl = (i) => new Date(s[i].day).toLocaleDateString("ru-RU", { day: "numeric", month: "short" });
  // прокрутка — по неделям от конца (сегодня) назад
  const weeks = [];
  for (let i = s.length - 1; i >= 0; i -= 7) weeks.unshift(i);
  box.innerHTML = `
    <div class="legend"><span><span class="dot stood"></span>устояли</span><span><span class="dot unchecked"></span>не проверены</span><span><span class="dot converted"></span>ушли, накопительно</span></div>
    <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Состав позиции по дням: устояли, не проверены, ушли">
      ${ticks.map((t) => `<line x1="${L}" x2="${W}" y1="${y(t)}" y2="${y(t)}" stroke="#272c39"/><text x="${L - 6}" y="${y(t) + 4}" fill="#7d8496" font-size="11" text-anchor="end">${fmt(t)}</text>`).join("")}
      <polygon points="${area((p) => p.stood + p.unchecked, (p) => p.stood + p.unchecked + p.converted)}" fill="var(--converted)" opacity=".85"/>
      <polygon points="${area((p) => p.stood, (p) => p.stood + p.unchecked)}" fill="var(--unchecked)"/>
      <polygon points="${area(() => 0, (p) => p.stood)}" fill="var(--stood)"/>
      <line id="cursor" x1="${x(s.length - 1)}" x2="${x(s.length - 1)}" y1="6" y2="${H - B}" stroke="#edeef2" stroke-dasharray="3 3"/>
      <text x="${L}" y="${H - 5}" fill="#7d8496" font-size="11">${lbl(0)}</text>
      <text x="${W - 4}" y="${H - 5}" fill="#7d8496" font-size="11" text-anchor="end">${lbl(s.length - 1)}</text>
    </svg>
    <div class="scrub"><label for="scrub">Прокрутить назад по неделям</label>
      <input id="scrub" type="range" min="0" max="${weeks.length - 1}" value="${weeks.length - 1}">
      <div class="scrub-out" id="scrub-out" aria-live="polite"></div></div>`;
  const out = $("#scrub-out");
  const show = (k) => {
    const i = weeks[k];
    const p = s[i];
    $("#cursor").setAttribute("x1", x(i));
    $("#cursor").setAttribute("x2", x(i));
    out.textContent = `${lbl(i)}: в позиции ${fmt(p.in_now)} · устояли ${fmt(p.stood)} · не проверены ${fmt(p.unchecked)} · ушли ${fmt(p.converted)}`;
  };
  $("#scrub").oninput = (e) => show(Number(e.target.value));
  show(weeks.length - 1);
}

function cruxStat(c) {
  if (c.kind === "question" && c.question) {
    const q = c.question;
    const base = q.status === "found"
      ? `ответ принят ${fmt(q.accepted)} из ${fmt(q.askers)} спросивших · ${fmt(q.not_satisfied)} ответ не устроил`
      : `спросили ${fmt(q.askers)} · ни один ответ не принят большинством`;
    const led = c.led_to ? ` · отсюда ушло ${fmt(c.led_to.n)} в «${esc(c.led_to.title)}»` : "";
    return base + led;
  }
  return `не убедило ${fmt(c.not_convinced)} · частично ${fmt(c.partial)} · убедило ${fmt(c.convinced)}`;
}

function cruxBadge(c) {
  if (c.kind !== "question" || !c.question) return "";
  return c.question.status === "found"
    ? `<span class="tag ok">ответ найден</span>`
    : `<span class="tag open">открыт ${fmt(c.question.open_days || 0)} дн.</span>`;
}

function cruxBar(c) {
  if (c.kind === "question" && c.question) {
    const q = c.question;
    const pct = q.askers ? Math.round(100 * q.accepted / q.askers) : 0;
    return `<div class="bar3 thin"><span class="stood" style="width:${pct}%"></span><span class="unchecked" style="width:${100 - pct}%"></span></div>`;
  }
  return bar3({ stood: c.not_convinced + c.partial, unchecked: 0, converted: c.convinced }, "thin");
}

function drawCruxes(d, sel = "#cruxes", topic = d.position && d.position.topic.id) {
  const box = $(sel);
  if (!d.cruxes.length) { box.innerHTML = `<p class="muted">Пока нет вопросов и подрывов.</p>`; return; }
  box.innerHTML = d.cruxes.map((c) => `<div class="item">
    <div class="head">${tag(c)}${cruxBadge(c)}</div>
    <div class="text">${nodeLink(c)}</div>
    ${cruxBar(c)}
    <div class="stat">${cruxStat(c)}</div>
    <div class="btns" style="margin-top:6px">
      ${c.kind === "question" ? `<button type="button" class="act" data-same="${c.id}">у меня тот же вопрос</button>` : ""}
      <button type="button" class="act" data-reply="${c.id}">ответить</button></div>
  </div>`).join("");
  const byId = Object.fromEntries(d.cruxes.map((c) => [c.id, c]));
  for (const b of box.querySelectorAll("[data-reply]")) {
    b.onclick = () => composer({ topic, target: byId[b.dataset.reply],
      action: byId[b.dataset.reply].kind === "question" ? "support" : "refute" });
  }
  for (const b of box.querySelectorAll("[data-same]")) {
    b.onclick = async () => {
      if (!ME) { location.href = "/?login=1"; return; }
      try {
        await api("/api/opinion/join", { method: "POST", body: { node_id: Number(b.dataset.same) } });
        refresh();
      } catch (e) { b.textContent = e.message; }
    };
  }
}

function drawMovers(d) {
  const box = $("#movers");
  if (!d.movers.length) { box.innerHTML = `<p class="muted">Пока никто не переходил, назвав причину.</p>`; return; }
  box.innerHTML = d.movers.map((m) => `<div class="item mover ${m.direction}">
    <div><div class="big">${fmt(m.n)}</div><div class="dir">${m.direction === "in" ? "привёл сюда" : "увёл отсюда"}</div></div>
    <div><div class="head">${tag(m)}${m.poi != null ? `<span class="tag">PoI ${m.poi}</span>` : ""}</div>
      <div class="text">${nodeLink(m)}</div></div></div>`).join("");
}

function drawErrors(d) {
  if (!d.errors.length) { $("#errors-card").hidden = true; return; }
  $("#errors").innerHTML = d.errors.map((e) => `<div class="item err">
    <div><div class="text">${nodeLink(e)}</div>
      <div class="stat"><b>Примечание автора:</b> ${esc(e.note)} Довод остаётся в графе, но больше не держит позицию.</div></div>
    <div><div class="big" style="font-size:22px;font-weight:650">${fmt(e.moved_after)}</div>
      <div class="stat">изменили мнение после примечания</div></div></div>`).join("");
}

// -------------------------------------------------------- экран обсуждения
let TSTATE = { period: "30", sort: null };

async function renderTopic() {
  const root = Number(qs.get("root"));
  const main = $("main");
  if (!root) { main.innerHTML = `<p class="note">Не указано обсуждение.</p>`; return; }
  await whoami();
  refresh = async () => {
    let d;
    try {
      const sort = TSTATE.sort ? `&sort=${TSTATE.sort}` : "";
      d = await api(`/api/opinion/topic/${root}?period=${TSTATE.period}${sort}`);
    } catch (e) { main.innerHTML = `<p class="note">${esc(e.message)}</p>`; return; }
    drawTopic(d);
  };
  await refresh();
}

function drawTopic(d) {
  const main = $("main");
  const t = d.topic;
  document.title = `${t.title} — карта мнений`;
  TSTATE.sort = d.sort;
  const head = `<div class="crumbs"><a href="/n/${t.id}">Дерево обсуждения</a><span class="sep">›</span><span>карта мнений</span></div>
    <h1>${esc(t.title)}</h1>`;
  if (d.cold_start) {
    main.innerHTML = head + `<p class="note">Позиции ещё складываются: на карте ${fmt(d.active_positions)} из ${fmt(d.min_positions)} нужных,
      ещё ${fmt(d.forming_positions)} собирают людей. Пока главное здесь — <a href="/n/${t.id}">дерево обсуждения</a>.</p>
      ${d.positions.length ? `<div class="plist" id="plist"></div>` : ""}
      <div class="btns"><button type="button" class="btn" id="no-pos">Моей позиции здесь нет</button></div>
      <p class="caption">${esc(d.caption)}</p>`;
    if (d.positions.length) drawPositionCards(d);
    $("#no-pos").onclick = () => composer({ topic: t.id, target: null });
    return;
  }
  if (d.blind) {
    main.innerHTML = head + `<p class="note">В этом обсуждении сначала пишут свой ответ, а потом видят, кто где стоит, —
      чтобы ответ не подстраивался под большинство.</p>
      <div class="btns"><button type="button" class="btn primary" id="first">Написать свой ответ</button></div>
      <div class="plist" id="plist"></div><p class="caption">${esc(d.caption)}</p>`;
    $("#first").onclick = () => composer({ topic: t.id, target: null });
    drawPositionCards(d);
    return;
  }
  main.innerHTML = head + `
    <div class="toolbar"><span id="tabs"></span>
      <label class="meta">Порядок <select class="select" id="sort">
        <option value="size">по размеру</option><option value="movement">по движению</option><option value="random">случайный</option>
      </select></label></div>
    <div class="plist" id="plist"></div>
    <div class="btns"><button type="button" class="btn" id="no-pos">Моей позиции здесь нет</button></div>
    <section class="card" style="margin-top:18px"><h2>Точки расхождения</h2>
      <p class="sub" style="margin-top:4px">Вопросы и подрывы, на которых люди чаще всего уходят или стоят на своём</p>
      <div id="tcruxes"></div></section>
    <p class="caption">${esc(d.caption)}</p>`;
  $("#tabs").appendChild(periodTabs(d.period, (id) => { TSTATE.period = id; refresh(); }));
  $("#sort").value = d.sort;
  $("#sort").onchange = (e) => { TSTATE.sort = e.target.value; refresh(); };
  $("#no-pos").onclick = () => composer({ topic: t.id, target: null });
  drawPositionCards(d);
  drawCruxes(d, "#tcruxes", t.id);
}

function drawPositionCards(d) {
  const box = $("#plist");
  box.innerHTML = d.positions.map((p) => {
    if (d.blind) return `<div class="card pcard"><div class="title">${esc(p.title)}</div></div>`;
    const fl = p.flows;
    const line = (g, dir) => `<div>${dir === "in" ? "← " : "→ "}${fmt(g.n)} ${dir === "in" ? "из" : "в"} ${g.position_id
      ? `«${esc(short(g.title, 50))}»` : (dir === "in" ? "новых" : "без позиции")}${g.cause ? `: <a href="/n/${g.cause.id}">${esc(short(g.cause.text, 60))}</a>` : ""}</div>`;
    return `<article class="card pcard ${p.mine ? "mine" : ""}">
      <div><a class="title" href="position.html?id=${p.id}">${esc(p.title)}</a>${p.mine ? ` <span class="tag ok">вы здесь</span>` : ""}
        ${p.status === "split" ? ` <span class="tag">раскалывается · не уточнили ${fmt(p.unclarified)}</span>` : ""}
        <div class="counts"><span>сейчас <b>${fmt(p.in_now)}</b></span>
          <span class="${p.delta >= 0 ? "" : ""}">${p.delta > 0 ? "+" : ""}${fmt(p.delta)} ${PERIOD_WORD[d.period]}</span>
          <span><span class="dot stood"></span> устояли <b>${fmt(p.stood)}</b></span>
          <span><span class="dot unchecked"></span> не проверены <b>${fmt(p.unchecked)}</b></span>
          <span><span class="dot converted"></span> переубеждены <b>${fmt(p.converted)}</b></span></div>
        ${bar3(p, "thin")}</div>
      <div class="flows">${fl.in.groups.slice(0, 2).map((g) => line(g, "in")).join("")}${fl.out.groups.slice(0, 2).map((g) => line(g, "out")).join("")}
        ${!fl.in.groups.length && !fl.out.groups.length ? `<span class="muted">без заметного движения ${PERIOD_WORD[d.period]}</span>` : ""}</div>
    </article>`;
  }).join("");
}

document.addEventListener("DOMContentLoaded", () => {
  if (document.body.dataset.page === "position") renderPosition();
  else renderTopic();
});
