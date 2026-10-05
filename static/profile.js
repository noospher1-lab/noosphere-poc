// Вынесено из profile.html: CSP script-src 'self' не допускает встроенных скриптов.
const $ = (s) => document.querySelector(s);

function money(x) { return "$" + Number(x).toFixed(2); }
function when(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("ru-RU",
    { day: "numeric", month: "short", year: "numeric" });
}

const esc = escHtml;   // общий, из safe.js: экранирует и кавычки (годится для атрибутов)

function renderVotes(votes) {
  const box = $("#votesList");
  if (!votes || !votes.length) {
    box.innerHTML = '<p class="muted" style="margin:0">Аккаунт ещё не голосовал.</p>';
    $("#votesCard").classList.remove("hidden");
    return;
  }
  box.innerHTML = votes.map((v) => {
    const opts = v.options.map((o) =>
      `<span class="opt ${o.origin === "reframe" ? "reframe" : ""}">`
      + esc(o.label || "(опция)") + (o.origin === "reframe" ? " · переформулировка" : "")
      + "</span>").join("");
    const w = v.weight != null ? Number(v.weight).toFixed(1) : "1.0";
    const st = { open: "идёт", closed: "закрыто", draft: "черновик" }[v.status] || v.status;
    const why = v.why ? `<div class="why">${esc(v.why)}</div>` : "";
    return `<div class="vote">
      <div class="q">${esc(v.question)}</div>
      <div class="opts">${opts}</div>
      <div class="meta">вес <span class="w">×${w}</span>
        · <span class="st">${esc(st)}</span>
        ${v.dialogue_score != null ? "· понимание " + esc(v.dialogue_score) + "/100 " : ""}
        · ${when(v.cast_at)}</div>
      ${why}
    </div>`;
  }).join("");
  $("#votesCard").classList.remove("hidden");
}

async function loadVotes(id) {
  try {
    const r = await fetch(`/api/authors/${id}/votes`);
    if (!r.ok) return null;
    return await r.json();
  } catch (_) { return null; }
}

const KIND_LABEL = { problem: "проблема", argument: "тезис", question: "вопрос",
  proposal: "предложение", exploration: "разбор", attribution: "атрибуция" };
const REL_LABEL = { support: "за", refute: "против", qualify: "уточнение",
  question: "вопрос", undercut: "не доказывает", proposal: "предложение",
  exploration: "разбор" };

function renderActivity(activity) {
  const box = $("#activityList");
  if (!activity || !activity.length) {
    box.innerHTML = '<p class="muted" style="margin:0">Пока ничего не написано.</p>';
    $("#activityCard").classList.remove("hidden");
    return;
  }
  box.innerHTML = activity.map((g) => {
    const made = g.created
      ? `<span class="badge made">создал ${g.topic_kind === "problem" ? "проблему" : "обсуждение"}</span>`
      : "";
    const items = g.items.filter((it) => !it.is_root).map((it) => {
      const label = it.rel ? (REL_LABEL[it.rel] || it.rel) : (KIND_LABEL[it.kind] || it.kind);
      const poi = it.poi_score != null ? ` · PoI ${esc(it.poi_score)}` : "";
      return `<div class="item"><span class="rel">${esc(label)}${poi}</span>
        <div class="t">${esc(it.text)}</div></div>`;
    }).join("");
    return `<div class="act">
      <div class="disc"><a href="/?topic=${+g.topic_root_id}" target="_blank">${esc(g.topic_title)}</a>${made}</div>
      ${items}
    </div>`;
  }).join("");
  $("#activityCard").classList.remove("hidden");
}

async function loadActivity(id) {
  try {
    const r = await fetch(`/api/authors/${id}/activity`);
    if (!r.ok) return null;
    return await r.json();
  } catch (_) { return null; }
}

// Публичный профиль другого аккаунта: только то, что публично — имя и история
// голосований. Приватное (счёт, вклад, данные) скрыто.
async function loadPublic(id) {
  const data = await loadVotes(id);
  if (!data) {
    $("#err").textContent = "Аккаунт не найден.";
    $("#err").classList.remove("hidden");
    return;
  }
  document.querySelector("header .tagline").textContent = "публичный профиль";
  $("#name").textContent = data.author.name || data.author.username || ("#" + id);
  if (data.author.color) $("#name").style.color = data.author.color;
  $("#handle").textContent = data.author.username ? "@" + data.author.username : "";
  $("#budgetCard").classList.add("hidden");
  $("#contribCard").classList.add("hidden");
  $("#accountCard").classList.add("hidden");
  renderVotes(data.votes);
  const act = await loadActivity(id);
  if (act) renderActivity(act.activity);
  $("#content").classList.remove("hidden");
}

async function load() {
  let p;
  try {
    const r = await fetch("/api/me/profile");
    if (r.status === 401) {
      $("#err").textContent = "Нужно войти — открой обсуждения и нажми «Войти».";
      $("#err").classList.remove("hidden");
      return;
    }
    if (!r.ok) throw new Error(await r.text());
    p = await r.json();
  } catch (e) {
    $("#err").textContent = "Не удалось загрузить кабинет: " + e.message;
    $("#err").classList.remove("hidden");
    return;
  }

  const a = p.author, acc = p.account || {}, act = p.activity || {};
  $("#name").textContent = a.name || a.username;
  $("#handle").textContent = "@" + (a.username || "");
  if (a.color) $("#name").style.color = a.color;

  $("#left").textContent = money(acc.left_usd || 0);
  $("#spent").textContent = money(acc.spent_usd || 0);
  $("#granted").textContent = money(acc.granted_usd || 0);
  $("#calls").textContent = acc.calls ?? 0;

  const granted = Number(acc.granted_usd) || 0;
  const used = granted > 0 ? Math.min(100, (acc.spent_usd / granted) * 100) : 0;
  $("#barFill").style.width = used.toFixed(1) + "%";

  // The state that actually blocks a tester is worth saying plainly, rather
  // than leaving them to work it out from a number. Having no key of your
  // own is the normal case now — the shared key pays, and this grant is what
  // bounds it — so it is no longer worth a message.
  if (acc.has_key) {
    $("#balanceNote").textContent =
      "К аккаунту привязан свой ключ ИИ — расход идёт по нему, без лимита счёта.";
  } else if ((acc.left_usd || 0) <= 0 && granted > 0) {
    $("#balanceNote").textContent =
      "Счёт израсходован. Читать можно по-прежнему; для публикации и действий с ИИ нужно пополнение.";
  } else {
    $("#balanceNote").textContent =
      "Счёт тратится на оценку текстов, диалог PoI, поиск и переводы.";
  }

  $("#nodes").textContent = act.nodes ?? 0;
  $("#arguments").textContent = act.arguments ?? 0;
  $("#questions").textContent = act.questions ?? 0;
  $("#avgPoi").textContent = act.avg_poi ?? "—";
  $("#dlgPoi").textContent = a.dialogue_poi != null ? Math.round(a.dialogue_poi) : "—";
  $("#firstAt").textContent = when(act.first_at);
  $("#lastAt").textContent = when(act.last_at);

  $("#content").classList.remove("hidden");
  loadPoints(a.id);
  const mine = await loadVotes(a.id);
  if (mine) renderVotes(mine.votes);
  const myActivity = await loadActivity(a.id);
  if (myActivity) renderActivity(myActivity.activity);
}

// ---- вклад: баллы, вехи, участники
// Названия начислений живут здесь, а не на сервере: сервер пишет в журнал вид
// действия ("argument", "mark", …), а как это назвать человеку — вопрос языка
// интерфейса, и менять его не должно значить пересборку журнала.
const PT_NAMES = {
  argument: "довод", question: "вопрос", problem: "заведена проблема",
  mark: "отметка за/против", stance: "отметка на позиции", vote: "голос",
  concession: "уступка", problem_link: "связь проблем",
  intervention: "предложение в реестр", trainer: "сессия тренажёра",
  milestone: "веха",
};

function renderPoints(p) {
  $("#ptTotal").textContent = p.total;
  // При одном участнике «1 из 1» выглядит издёвкой, но и прочерк на своём
  // месте неверен: место есть, сравнивать пока не с кем.
  // без баллов в рейтинге тебя ещё нет — «8 из 7» (дизайн 25.09) было ошибкой
  $("#ptRank").textContent = !(p.total > 0) ? "—"
                           : p.players > 1 ? Math.min(p.rank, p.players) + " из " + p.players : "1";
  $("#ptStreak").textContent = p.streak || 0;
  const done = p.milestones.filter((m) => m.done).length;
  $("#ptDone").textContent = done + " / " + p.milestones.length;

  $("#ptMilestones").innerHTML = p.milestones.map((m) =>
    `<div class="m ${m.done ? "on" : ""}">
       <div class="t"><span>${m.done ? "✓ " : ""}${esc(m.title)}</span><b>+${esc(m.points)}</b></div>
       <div class="h">${esc(m.hint)}</div>
     </div>`).join("");

  const box = $("#ptRecent");
  if (!p.recent.length) {
    box.innerHTML = '<p class="muted" style="margin:0">Пока пусто. Начисления'
      + ' появятся после первого действия в графе.</p>';
  } else {
    box.innerHTML = p.recent.map((r) => {
      const key = r.kind === "milestone" && r.payload && r.payload.key;
      const label = key
        ? (p.milestones.find((m) => m.key === key) || {}).title || "веха"
        : PT_NAMES[r.kind] || r.kind;
      const sign = r.amount > 0 ? "+" : "";
      return `<div class="r"><span class="k">${esc(label)} · ${when(r.ts)}</span>
        <span class="amt ${r.amount < 0 ? "neg" : ""}">${sign}${esc(r.amount)}</span></div>`;
    }).join("");
  }
}

async function loadPoints(myId) {
  try {
    const r = await fetch("/api/points/me");
    if (!r.ok) { $("#pointsCard").classList.add("hidden"); return; }
    const pts = await r.json();
    if (pts.anon) { $("#pointsCard").classList.add("hidden"); return; }
    renderPoints(pts);
  } catch (_) { $("#pointsCard").classList.add("hidden"); return; }
  try {
    const top = await (await fetch("/api/points/top?limit=15")).json();
    $("#ptTop").innerHTML = top.map((t, i) =>
      `<div class="r ${t.id === myId ? "me" : ""}">
         <span class="k">${i + 1}. ${esc(t.name || t.username)}</span>
         <span class="amt">${esc(t.total)}</span></div>`).join("")
      || '<p class="muted" style="margin:0">Пока никто не набрал баллов.</p>';
  } catch (_) { /* рейтинг — не главное в карточке, без него она живёт */ }
}

// ---- аккаунт: почта и пароль
// Кабинет обязан уметь то, ради чего у аккаунта вообще есть почта. Раньше здесь
// был только текст «аккаунт остаётся за тобой», а сменить адрес или пароль было
// нельзя ни из интерфейса, ни как-либо ещё.
async function loadAccount() {
  let acc;
  try { acc = await (await fetch("/api/account")).json(); }
  catch (_) { return; }
  if (!acc || acc.detail) return;
  $("#accName").textContent = acc.name || "—";
  $("#accLogin").textContent = "@" + acc.username;
  $("#newName").value = acc.name || "";
  $("#accEmail").textContent = acc.email_masked || "не указана";
  const tag = $("#accVerified");
  if (!acc.email_masked) { tag.textContent = ""; tag.className = "acc-tag"; }
  else if (acc.email_verified) { tag.textContent = "подтверждена"; tag.className = "acc-tag ok"; }
  else { tag.textContent = "не подтверждена"; tag.className = "acc-tag no"; }
  const pend = $("#accPending");
  if (acc.pending_email_masked) {
    pend.textContent = "Ждём подтверждения нового адреса: " + acc.pending_email_masked +
      ". Пока ссылка не открыта, аккаунт остаётся на прежнем.";
    pend.classList.remove("hidden");
  } else {
    pend.classList.add("hidden");
  }
}

function accMsg(text, bad) {
  const m = $("#accMsg");
  m.textContent = text || "";
  m.style.color = bad ? "var(--red)" : "var(--dim)";
}

async function accPost(url, body) {
  const r = await fetch(url, {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.detail || ("ошибка " + r.status));
  return d;
}

function toggleForm(id) {
  for (const f of ["nameForm", "emailForm", "passForm"]) {
    const el = $("#" + f);
    if (f === id) el.classList.toggle("hidden");
    else el.classList.add("hidden");
  }
  accMsg("");
}

$("#btnName").onclick = () => toggleForm("nameForm");
$("#btnEmail").onclick = () => toggleForm("emailForm");
$("#btnPass").onclick = () => toggleForm("passForm");
for (const b of document.querySelectorAll("[data-cancel]"))
  b.onclick = () => { $("#" + b.dataset.cancel).classList.add("hidden"); accMsg(""); };

$("#nameForm").onsubmit = async (e) => {
  e.preventDefault();
  try {
    const d = await accPost("/api/account/name", { name: $("#newName").value });
    accMsg(d.detail);
    $("#nameForm").classList.add("hidden");
    // имя видно в шапке профиля — обновляем сразу, иначе кажется, что не сохранилось
    const h1 = $("#name");
    if (h1) h1.textContent = d.name;
    loadAccount();
  } catch (err) { accMsg(err.message, true); }
};

$("#emailForm").onsubmit = async (e) => {
  e.preventDefault();
  try {
    const d = await accPost("/api/account/email", {
      email: $("#newEmail").value.trim(), password: $("#emailPass").value,
    });
    accMsg(d.detail);
    $("#emailForm").reset();
    $("#emailForm").classList.add("hidden");
    loadAccount();
  } catch (err) { accMsg(err.message, true); }
};

$("#passForm").onsubmit = async (e) => {
  e.preventDefault();
  // Второй ввод — не формальность: опечатка в новом пароле заперла бы человека
  // снаружи, а прочие сессии к этому моменту уже закрыты.
  if ($("#newPass").value !== $("#newPass2").value) {
    accMsg("новый пароль введён по-разному", true);
    return;
  }
  try {
    const d = await accPost("/api/account/password", {
      current: $("#curPass").value, password: $("#newPass").value,
    });
    accMsg(d.detail);
    $("#passForm").reset();
    $("#passForm").classList.add("hidden");
  } catch (err) { accMsg(err.message, true); }
};

const _pid = new URLSearchParams(location.search).get("id");
// чужой профиль — публичная страница: управление аккаунтом там ни к чему
if (_pid) { loadPublic(_pid); $("#accountCard").classList.add("hidden"); }
else { load(); loadAccount(); }
