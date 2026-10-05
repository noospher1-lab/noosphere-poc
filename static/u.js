// Вынесено из u.html: CSP script-src 'self' не допускает встроенных скриптов.
const $ = s => document.querySelector(s);
// Имя участника берётся из адреса страницы: один документ обслуживает все
// профили, сервер отдаёт его на любой /u/<логин>.
const username = decodeURIComponent(location.pathname.replace(/^\/u\//, "")).trim();

const esc = escHtml;   // общий, из safe.js: экранирует и кавычки

const dt = s => s ? new Date(s).toLocaleDateString("ru-RU",
  { year: "numeric", month: "long", day: "numeric" }) : "";

// Подписи типов: в базе они английские, а страницу читают люди. У корня
// обсуждения связи нет вообще (rel пуст) — там осмысленно показать не тип
// связи, а то, что человек это обсуждение начал.
const REL = {
  support: "в поддержку", oppose: "против", question: "вопрос",
  answer: "ответ", attribution: "атрибуция", continue: "продолжение",
};
const KIND = {
  problem: "проблема", argument: "тезис", question: "вопрос",
  position: "позиция", intervention: "решение", exploration: "исследование",
  proposal: "предложение",
};
const contribLabel = it =>
  it.is_root ? "начал обсуждение"
             : (REL[it.rel] || KIND[it.kind] || it.kind || "запись");

function renderActivity(groups) {
  if (!groups || !groups.length) {
    $("#actCard").innerHTML =
      '<div class="section-title">Что писал</div><div class="muted">Пока ничего не публиковал.</div>';
    return;
  }
  $("#activity").innerHTML = groups.map(g => `
    <div class="topic">
      <div class="t">${esc(g.topic_title || g.topic_text || "Без названия")}</div>
      ${(g.items || []).map(it => `
        <div class="contrib">
          <div class="meta">
            <span>${esc(contribLabel(it))}</span>
            <span>${dt(it.created_at)}</span>
            ${it.poi_score != null ? `<span class="poi">PoI ${esc(it.poi_score)}</span>` : ""}
          </div>
          <div class="txt">${esc(it.title || it.text || "")}</div>
        </div>`).join("")}
    </div>`).join("");
}

function renderVotes(votes) {
  if (!votes || !votes.length) {
    $("#voteCard").classList.add("hidden");
    return;
  }
  $("#votes").innerHTML = votes.map(v => `
    <div class="vote">
      <div class="q">${esc(v.question || v.title || "Голосование")}</div>
      <div class="opts">${(v.options || []).map(o =>
        `<span class="chip">${esc(o.label)}</span>`).join("")}</div>
      ${v.weight != null ? `<div class="note">вес голоса ${esc(v.weight)}${
        v.cast_at ? " · " + dt(v.cast_at) : ""}</div>` : ""}
      ${v.why ? `<div class="why">${esc(v.why)}</div>` : ""}
    </div>`).join("");
}

(async () => {
  if (!username) { fail("Не указан участник."); return; }
  let r;
  try {
    r = await fetch(`/api/u/${encodeURIComponent(username)}`);
  } catch (e) { fail("Сеть недоступна."); return; }
  if (r.status === 404) { fail("Такого участника нет."); return; }
  if (!r.ok) { fail("Не удалось загрузить профиль."); return; }

  const d = await r.json();
  const a = d.author || {};
  document.title = `${a.name || a.username} — Noosphere`;

  $("#ava").style.background = a.color || "#e0b878";
  $("#ava").textContent = (a.name || a.username || "?").trim()[0].toUpperCase();
  $("#name").textContent = a.name || a.username;
  $("#login").textContent = "@" + (a.username || "");
  if (d.is_me) $("#meBadge").classList.remove("hidden");
  $("#since").textContent = a.created_at ? `Участник с ${dt(a.created_at)}` : "";

  const s = d.summary || {};
  $("#sNodes").textContent = s.nodes || 0;
  $("#sArgs").textContent = s.arguments || 0;
  $("#sQuest").textContent = s.questions || 0;
  $("#sPoi").textContent = s.avg_poi != null ? s.avg_poi : "—";

  renderActivity(d.activity);
  renderVotes(d.votes);
  $("#body").classList.remove("hidden");
})();

function fail(msg) {
  $("#err").textContent = msg;
  $("#err").classList.remove("hidden");
}
