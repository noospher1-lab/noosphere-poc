// Вынесено из stats.html: CSP script-src 'self' не допускает встроенных скриптов.
const $ = (s, r=document) => r.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"]/g,
  c => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;" }[c]));

function getToken() {
  let t = localStorage.getItem("admin_token");
  if (!t) {
    t = prompt("X-Admin-Token (ADMIN_TOKEN сервера):") || "";
    if (t) localStorage.setItem("admin_token", t);
  }
  return t;
}

async function api(path) {
  const r = await fetch(path, { headers: { "X-Admin-Token": getToken() } });
  if (r.status === 403) localStorage.removeItem("admin_token");  // спросим заново
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || String(r.status));
  return r.json();
}

// «5 минут назад» вместо голого ISO: на этой странице важно не когда именно,
// а насколько давно.
function ago(iso) {
  if (!iso) return "—";
  const sec = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (sec < 60)    return "только что";
  if (sec < 3600)  return Math.floor(sec / 60) + " мин назад";
  if (sec < 86400) return Math.floor(sec / 3600) + " ч назад";
  const d = Math.floor(sec / 86400);
  return d + " " + (d % 10 === 1 && d % 100 !== 11 ? "день" :
    (d % 10 >= 2 && d % 10 <= 4 && (d % 100 < 10 || d % 100 >= 20)) ? "дня" : "дней") + " назад";
}

function fmtDate(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("ru-RU", { day:"2-digit", month:"2-digit",
    year:"2-digit", hour:"2-digit", minute:"2-digit" });
}

// Доли цента здесь не шум, а типичная величина одного вызова: $0.0043 сказало бы
// «почти ничего», а $0.00 — «ничего», что уже неправда. До цента округляем
// только суммы от доллара, где четвёртый знак действительно не нужен.
function usd(x) {
  const v = Number(x) || 0;
  if (v === 0) return "$0";
  return "$" + (v >= 1 ? v.toFixed(2) : v.toFixed(4));
}

function tile(k, v, sub, cls="") {
  return `<div class="tile ${cls}"><div class="k">${esc(k)}</div>` +
         `<div class="v">${esc(v)}</div>` +
         (sub ? `<div class="sub">${sub}</div>` : "") + `</div>`;
}

function render(d) {
  const u = d.users, p = d.presence, win = d.online_window_min;

  $("#people").innerHTML =
    tile("зарегистрировано", u.registered, "живых аккаунтов, без служебных", "hero") +
    tile("онлайн сейчас", p.online,
         `<span class="dot ${p.online ? "" : "off"}"></span>активность за ${win} мин`, "live") +
    tile("активны за сутки", p.active_24h) +
    tile("активны за неделю", p.active_7d) +
    tile("подтвердили почту", u.verified,
         u.registered ? Math.round(100 * u.verified / u.registered) + "% от всех" : "") +
    tile("прошли диалог PoI", u.with_poi) +
    tile("новых за сутки", u.new_24h, "за неделю: " + esc(u.new_7d)) +
    tile("живых сессий", p.live_sessions, "включая неактивные вкладки");

  $("#online").innerHTML = d.online_now.length ? `<table>
    <tr><th>кто</th><th>логин</th><th class="num">последний запрос</th></tr>
    ${d.online_now.map(o => `<tr>
      <td class="who">${esc(o.name)}</td>
      <td class="muted">${esc(o.username)}</td>
      <td class="num muted">${ago(o.last_seen)}</td></tr>`).join("")}
    </table>` : `<div class="empty">сейчас никого — активность за ${win} мин</div>`;

  const m = d.spend_total;
  $("#money").innerHTML =
    tile("потрачено всего", usd(m.spent_usd), "на вызовы ИИ, за всё время", "hero") +
    tile("за сутки", usd(m.spent_24h_usd)) +
    tile("выдано бюджета", usd(m.granted_usd),
         "осталось: " + usd(m.granted_usd - m.spent_usd)) +
    tile("вызовов ИИ", m.calls);

  profiles = d.profiles;
  renderProfiles();

  const inv = d.invites, total = inv.total || 1;
  $("#invites").innerHTML = `
    <div class="bar">
      <i class="used" style="width:${100 * inv.used / total}%"></i>
      <i class="free" style="width:${100 * inv.free / total}%"></i>
    </div>
    <div class="legend">
      <span>потрачено <b>${+inv.used || 0}</b></span>
      <span>свободно <b>${+inv.free || 0}</b></span>
      <span>выпущено всего <b>${+inv.total || 0}</b></span>
    </div>`;

  const c = d.content, g = d.dialogues;
  $("#content").innerHTML = `<div class="kv">
    <div><span class="k">тем</span><span class="v">${+c.topics || 0}</span></div>
    <div><span class="k">узлов</span><span class="v">${+c.nodes || 0}</span></div>
    <div><span class="k">доводов</span><span class="v">${+c.arguments || 0}</span></div>
    <div><span class="k">вопросов</span><span class="v">${+c.questions || 0}</span></div>
    <div><span class="k">за сутки</span><span class="v">${+c.nodes_24h || 0}</span></div>
    <div><span class="k">диалогов начато</span><span class="v">${+g.started || 0}</span></div>
    <div><span class="k">из них с итогом</span><span class="v">${+g.finished || 0}</span></div>
  </div>`;

  $("#updated").textContent = "обновлено " +
    new Date().toLocaleTimeString("ru-RU", { hour:"2-digit", minute:"2-digit", second:"2-digit" });
  $("#error").innerHTML = "";
}

// ---- полный список профилей: сортировка по любому столбцу + фильтр.
// Состояние живёт вне render(): страница перечитывает данные раз в 30 секунд,
// и таблица не должна прыгать обратно к сортировке по умолчанию под руками.
let profiles = [];
let sortKey = "created_at", sortDesc = true, filter = "";

const COLS = [
  { k:"name",          t:"кто",         cell:r => `<span class="who">${esc(r.name)}</span>` +
      (r.own_key ? `<span class="tag" title="свой ключ Anthropic">свой ключ</span>` : "") },
  { k:"username",      t:"логин",       cell:r => `<a href="/u/${encodeURIComponent(r.username)}" class="muted">${esc(r.username)}</a>` },
  { k:"email",         t:"почта",       cell:r => r.email
      ? `<span class="${r.email_verified ? "yes" : "no"}" title="${r.email_verified ? "подтверждена" : "не подтверждена"}">${esc(r.email)}</span>`
      : `<span class="no">—</span>` },
  { k:"created_at",    t:"регистрация", num:true, cell:r => `<span class="muted">${fmtDate(r.created_at)}</span>` },
  { k:"last_seen",     t:"был здесь",   num:true, cell:r => `<span class="muted">${ago(r.last_seen)}</span>` },
  { k:"nodes",         t:"узлов",       num:true, cell:r => esc(r.nodes) || `<span class="zero">0</span>` },
  { k:"dialogue_poi",  t:"PoI",         num:true, cell:r => esc(r.dialogue_poi) ?? `<span class="zero">—</span>` },
  { k:"calls",         t:"вызовов",     num:true, cell:r => esc(r.calls) || `<span class="zero">0</span>` },
  { k:"spent_usd",     t:"потрачено",   num:true, cell:r => r.spent_usd
      ? `<span class="money">${usd(r.spent_usd)}</span>` : `<span class="zero">$0</span>` },
  { k:"spent_24h_usd", t:"за сутки",    num:true, cell:r => r.spent_24h_usd
      ? usd(r.spent_24h_usd) : `<span class="zero">$0</span>` },
  { k:"granted_usd",   t:"выдано",      num:true, cell:r => `<span class="muted">${usd(r.granted_usd)}</span>` },
  // Минус — это перерасход: бюджет ушёл в ноль, а вызовы продолжались.
  { k:"left_usd",      t:"осталось",    num:true, cell:r =>
      `<span class="${r.left_usd <= 0 ? "warn" : ""}">${usd(r.left_usd)}</span>` },
];

function renderProfiles() {
  const q = filter.trim().toLowerCase();
  const rows = profiles.filter(r => !q ||
    [r.username, r.name, r.email].some(v => (v || "").toLowerCase().includes(q)));

  rows.sort((a, b) => {
    let x = a[sortKey], y = b[sortKey];
    if (x === null || x === undefined) x = -Infinity;   // «никогда» — всегда в хвосте
    if (y === null || y === undefined) y = -Infinity;
    if (sortKey === "created_at" || sortKey === "last_seen") {
      x = x === -Infinity ? x : new Date(x).getTime();
      y = y === -Infinity ? y : new Date(y).getTime();
    }
    const c = typeof x === "string" && typeof y === "string"
      ? x.localeCompare(y, "ru") : (x < y ? -1 : x > y ? 1 : 0);
    return sortDesc ? -c : c;
  });

  $("#profilesCount").textContent = rows.length === profiles.length
    ? `${profiles.length} всего`
    : `${rows.length} из ${profiles.length}`;

  $("#profiles").innerHTML = rows.length ? `<table>
    <tr>${COLS.map(c => `<th data-k="${c.k}" class="${c.num ? "num" : ""}">${c.t}` +
      (sortKey === c.k ? `<span class="arrow">${sortDesc ? " ↓" : " ↑"}</span>` : "") +
      `</th>`).join("")}</tr>
    ${rows.map(r => `<tr>${COLS.map(c =>
      `<td class="${c.num ? "num" : ""}">${c.cell(r)}</td>`).join("")}</tr>`).join("")}
    </table>` : `<div class="empty">${profiles.length ? "никто не подошёл под фильтр"
                                                      : "пока ни одного аккаунта"}</div>`;

  $("#profiles").querySelectorAll("th").forEach(th => th.onclick = () => {
    const k = th.dataset.k;
    // тот же столбец — переворот; новый — сначала по убыванию (чаще всего нужен верх)
    if (sortKey === k) sortDesc = !sortDesc; else { sortKey = k; sortDesc = true; }
    renderProfiles();
  });
}

$("#q").oninput = e => { filter = e.target.value; renderProfiles(); };

async function load() {
  try {
    render(await api("/api/dev/stats"));
  } catch (e) {
    $("#error").innerHTML = `<div class="err">не вышло: ${esc(e.message)}</div>`;
  }
}

$("#refresh").onclick = load;
$("#forget").onclick = () => { localStorage.removeItem("admin_token"); location.reload(); };
load();
setInterval(load, 30000);   // «онлайн» с точностью до минуты — чаще незачем
