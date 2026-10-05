// Вынесено из vote.html: CSP script-src 'self' не допускает встроенных скриптов.
const $ = (s) => document.querySelector(s);
const qs = new URLSearchParams(location.search);
let ME = null, D = null, DLG = null;

async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} : {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.status);
  return r.json();
}
function err(m) {
  const e = $("#err"); e.textContent = m; e.classList.remove("hidden");
  window.scrollTo(0, 0); setTimeout(() => e.classList.add("hidden"), 7000);
}
function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function topicName(t) {
  const n = (t.topic_title && t.topic_title.trim()) || (t.topic_text || "").trim();
  return n.length > 90 ? n.slice(0, 90) + "…" : n;
}

// ---------- СПИСОК ----------
function voteCard(d) {
  const isOpen = d.status === "open";
  const badge = isOpen ? '<span class="badge open">открыто</span>'
                       : '<span class="badge">завершено</span>';
  return `
    <a class="card vote-card" href="/vote.html?id=${+d.id}">
      <div class="vc-q">${esc(d.question)}</div>
      <div class="vc-topic">Обсуждение: ${esc(topicName(d))}</div>
      <div class="vc-meta">${badge}
        &nbsp;· голосов: ${esc(d.voters)} · вариантов: ${esc(d.options)}
      </div>
    </a>`;
}

async function renderList() {
  $("#list").classList.remove("hidden");
  let rows = [];
  try { rows = await api("/api/decisions?status=all"); }
  catch (e) { err("Список не загрузился: " + e.message); }
  const open = rows.filter((d) => d.status === "open");
  const closed = rows.filter((d) => d.status === "closed");

  $("#list-body").innerHTML = open.length
    ? open.map(voteCard).join("")
    : '<div class="note">Пока нет открытых голосований. '
      + '<a href="/create-vote.html">Создай первое</a>.</div>';

  $("#list-closed").innerHTML = closed.length
    ? '<div class="sec-h" style="margin-top:26px">Завершённые</div>'
      + closed.map(voteCard).join("")
    : "";
}

// ---------- ДЕТАЛЬ ----------
async function loadDetail(id) {
  $("#detail").classList.remove("hidden");
  const view = await api(`/api/decisions/${id}`);
  D = view;
  $("#dq").textContent = view.decision.question;
  renderControls(view);
  renderTally(view.tally);
  renderVote(view);
  renderPrepare();
}

function renderControls(view) {
  const d = view.decision;
  const ctl = $("#dctl");
  const st = { open: "открыто", closed: "закрыто", draft: "черновик" }[d.status] || d.status;
  const isCreator = ME && d.created_by === ME.id;
  const canManage = isCreator || (ME && ME.is_admin);
  const empty = (view.tally.voters || 0) === 0;
  ctl.innerHTML = `<span class="badge ${d.status === "open" ? "open" : ""}">${esc(st)}</span>`;

  if (isCreator && d.status === "open") {
    const b = document.createElement("button");
    b.className = "btn ghost"; b.textContent = "Закрыть голосование";
    b.onclick = async () => {
      b.disabled = true;
      try { await api(`/api/decisions/${d.id}/close`, {}); location.reload(); }
      catch (e) { err("Не удалось закрыть: " + e.message); b.disabled = false; }
    };
    ctl.appendChild(b);
  }

  // Удалить можно только пустое (0 голосов) — автору или админу.
  if (canManage && empty) {
    const del = document.createElement("button");
    del.className = "btn ghost"; del.textContent = "Удалить";
    del.title = "Удалить пустое голосование";
    del.onclick = async () => {
      if (!confirm("Удалить это голосование? Оно пустое, отменить нельзя.")) return;
      del.disabled = true;
      try {
        const r = await fetch(`/api/decisions/${d.id}`, { method: "DELETE" });
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.status);
        location.href = "/vote.html";
      } catch (e) { err("Не удалось удалить: " + e.message); del.disabled = false; }
    };
    ctl.appendChild(del);
  }
}

function renderTally(t) {
  const maxH = Math.max(1, ...t.answers.map((a) => a.heads));
  const maxW = Math.max(1, ...t.answers.map((a) => Number(a.weighted)));
  $("#tally").innerHTML = `
    <div class="muted" style="font-size:12.5px;margin-bottom:8px">
      участников: ${esc(t.voters)} · средний вес: ${esc(Number(t.avg_weight).toFixed(2))}</div>
    ${t.answers.map((a) => `
      <div class="opt">
        <div class="opt-top"><span class="opt-label">${esc(a.label || a.headline || "—")}</span></div>
        <div class="bar-track"><div class="bar-fill bar-heads" style="width:${Math.round(a.heads / maxH * 100)}%"></div></div>
        <div class="bars">по головам: ${esc(a.heads)}
          &nbsp;·&nbsp; по весу: ${Number(a.weighted).toFixed(1)}</div>
        <div class="bar-track"><div class="bar-fill bar-weight" style="width:${Math.round(Number(a.weighted) / maxW * 100)}%"></div></div>
      </div>`).join("")}`;
}

function renderVote(view) {
  const box = $("#vote");
  if (view.decision.status !== "open") {
    box.innerHTML = `<div class="note">${view.decision.status === "draft"
      ? "Черновик — голосование ещё не открыто."
      : "Голосование закрыто — приём голосов завершён. Итог выше."}</div>`;
    return;
  }
  if (!ME) { box.innerHTML = '<div class="note">Чтобы проголосовать, <a href="/">войди</a> и подтверди адрес.</div>'; return; }
  const w = (DLG && DLG.finished) ? DLG.weight : 1;
  // reframe — не ответ на вопрос, а его отклонение; за него не голосуют.
  const votable = view.options.filter((o) => o.origin !== "reframe");
  box.innerHTML = `
    <div class="muted" style="font-size:13px;margin-bottom:8px">
      Твой вес: <span class="weight-badge">${esc(w)}</span>
      ${(DLG && DLG.finished) ? "" : "— пройди подготовку с ИИ, чтобы поднять вес (без неё голос весит 1)"}</div>
    ${votable.map((o) => `
      <label class="opt-top" style="margin:6px 0">
        <input type="checkbox" name="opt" value="${+o.id}" />
        <span class="opt-label">${esc(o.label || o.headline || "—")}</span>
        ${o.origin === "proposed"
          ? `<span class="muted" style="font-size:12px"> — свой вариант${
               o.proposed_by_name ? ", " + esc(o.proposed_by_name) : ""}</span>`
          : ""}
      </label>`).join("")}
    <details style="margin-top:10px">
      <summary class="muted" style="font-size:13px;cursor:pointer">
        ни один вариант не выражает твою позицию? предложи свой</summary>
      <div style="margin-top:8px">
        <input id="own-opt" type="text" maxlength="300" style="width:100%"
               placeholder="свой ответ на вопрос — одной формулировкой" />
        <div class="muted" style="font-size:12px;margin:6px 0">
          Он встанет в бюллетень рядом с остальными, помеченный твоим именем,
          и за него смогут голосовать другие. Не больше двух с человека.</div>
        <button class="btn ghost" id="add-own">Добавить в бюллетень</button>
      </div>
    </details>
    <button class="btn primary" id="cast" style="margin-top:12px">Проголосовать</button>
    <div id="cast-done" class="note hidden" style="margin-top:10px"></div>`;
  $("#cast").onclick = castVote;
  $("#add-own").onclick = addOwnOption;
}

// Свой вариант: бюллетень, собранный одним человеком, задаёт рамку ответа —
// а расхождение чаще всего в том, что вопрос поставлен не так. Поэтому
// предложить свою формулировку может любой участник открытого голосования.
async function addOwnOption() {
  const input = $("#own-opt");
  const label = (input.value || "").trim();
  if (!label) return err("Напиши свой вариант.");
  const btn = $("#add-own"); btn.disabled = true;
  try {
    await api(`/api/decisions/${D.decision.id}/options`,
              { label, origin: "proposed" });
    await loadDetail(D.decision.id);
  } catch (e) {
    err("Не удалось добавить вариант: " + e.message);
    btn.disabled = false;
  }
}

async function castVote() {
  const ids = [...document.querySelectorAll('input[name=opt]:checked')].map((i) => Number(i.value));
  if (!ids.length) return err("Выбери хотя бы один вариант.");
  const btn = $("#cast"); btn.disabled = true;
  try {
    const res = await api(`/api/decisions/${D.decision.id}/vote`, { option_ids: ids });
    renderTally(res.tally);
    const done = $("#cast-done");
    done.classList.remove("hidden");
    done.innerHTML = `✓ Голос учтён, вес <span class="weight-badge">${esc(res.weight)}</span>. Спасибо!`;
  } catch (e) { err("Не удалось проголосовать: " + e.message); btn.disabled = false; }
}

// ---------- ПОДГОТОВКА (диалог) ----------
function renderPrepare() {
  const box = $("#prepare");
  if (D && D.decision.status !== "open") {
    box.innerHTML = `<div class="note">${D.decision.status === "draft"
      ? "Черновик ещё не открыт — подготовка станет доступна после открытия."
      : "Голосование закрыто — подготовка больше не нужна."}</div>`;
    return;
  }
  if (!ME) { box.innerHTML = '<div class="note">Подготовка доступна после входа.</div>'; return; }
  if (!DLG) {
    box.innerHTML = `
      <p class="muted" style="font-size:13.5px;margin-top:0">
        Разбери вопрос с ИИ-собеседником: он идёт по реальному срезу спора, без подсказок
        «правильного» ответа. После ${D ? "" : ""}разбора судья оценит понимание и даст твоему
        голосу вес от 1 до 5.</p>
      <button class="btn primary" id="dlg-start">Начать разбор</button>`;
    $("#dlg-start").onclick = startDialogue;
    return;
  }
  // есть диалог — рендерим переписку
  const t = DLG.transcript || [];
  const last = t[t.length - 1];
  const pending = last && last.meta === "inform_offer_pending";
  let html = '<div class="chat" id="chat">';
  for (const m of t) {
    if (m.meta === "inform_offer_pending") {
      html += `<div class="offer"><div class="msg ai">${esc(m.content)}</div></div>`;
    } else {
      html += `<div class="msg ${m.role === "user" ? "me" : "ai"}">${esc(m.content)}</div>`;
    }
  }
  html += "</div>";
  html += `<div class="prog">ходов: ${esc(DLG.user_turns)} / ${esc(DLG.min_turns)} (мин.) · до ${esc(DLG.max_turns)} макс.</div>`;

  if (DLG.finished) {
    html += `<div class="note">Разбор завершён. Твой вес: <span class="weight-badge">${esc(DLG.weight)}</span>.
      ${DLG.summary ? '<div class="summary">' + esc(DLG.summary) + "</div>" : ""}</div>`;
  } else if (pending) {
    html += `<div class="row" style="margin-top:8px">
        <span class="muted" style="font-size:13px">ИИ предлагает пояснение:</span>
        <button class="btn" id="inf-yes">Показать</button>
        <button class="btn ghost" id="inf-no">Не нужно</button></div>`;
  } else {
    html += `<div class="compose">
        <textarea id="msg" placeholder="Твой ответ…"></textarea>
        <button class="btn" id="send">Отправить</button></div>`;
    const canFinalize = DLG.user_turns >= DLG.min_turns;
    html += `<button class="btn primary" id="finalize" style="margin-top:10px" ${canFinalize ? "" : "disabled"}>
        ${canFinalize ? "Готов — оценить и открыть голос" : `Ещё минимум ${+(DLG.min_turns - DLG.user_turns) || 0} ход(а)`}</button>`;
  }
  box.innerHTML = html;
  $("#chat") && ($("#chat").scrollTop = $("#chat").scrollHeight);
  if ($("#dlg-start")) $("#dlg-start").onclick = startDialogue;
  if ($("#send")) $("#send").onclick = sendMessage;
  if ($("#finalize")) $("#finalize").onclick = finalize;
  if ($("#inf-yes")) $("#inf-yes").onclick = () => informOffer(true);
  if ($("#inf-no")) $("#inf-no").onclick = () => informOffer(false);
}

function busy(text) {
  const box = $("#prepare");
  const b = document.createElement("div");
  b.className = "note"; b.id = "busy"; b.textContent = text;
  box.appendChild(b);
  document.querySelectorAll("#prepare button, #vote button").forEach((x) => (x.disabled = true));
}

async function startDialogue() {
  busy("ИИ читает срез спора и открывает разговор…");
  try { DLG = await api(`/api/decisions/${D.decision.id}/dialogue/start`, {}); }
  catch (e) { err("Не удалось начать: " + e.message); }
  renderPrepare(); renderVote(D);
}
async function sendMessage() {
  const text = $("#msg").value.trim();
  if (!text) return;
  busy("Собеседник думает…");
  try { DLG = await api(`/api/decisions/${D.decision.id}/dialogue/message`, { text }); }
  catch (e) { err("Сообщение не прошло: " + e.message); }
  renderPrepare();
}
async function informOffer(accept) {
  busy(accept ? "ИИ поясняет…" : "Продолжаем…");
  try { DLG = await api(`/api/decisions/${D.decision.id}/dialogue/inform`, { accept }); }
  catch (e) { err(e.message); }
  renderPrepare();
}
async function finalize() {
  busy("Судья перечитывает разбор и ставит вес…");
  try { DLG = await api(`/api/decisions/${D.decision.id}/dialogue/finalize`, {}); }
  catch (e) { err("Оценка не прошла: " + e.message); }
  renderPrepare(); renderVote(D);
}

// ---------- boot ----------
async function boot() {
  try { ME = await api("/api/auth/me"); } catch (_) {}
  if (ME && ME.name) $("#user").textContent = ME.name;
  const id = qs.get("id");
  if (id) { try { await loadDetail(Number(id)); } catch (e) { err("Голосование не найдено: " + e.message); renderList(); } }
  else renderList();
}
boot();
