// Noosphere — tree UI, lazy edition.
//
// Read contract (scaling draft, principle 3): the client NEVER loads the whole
// graph. It reads /api/topics (roots) and, per expanded node, a ranked page of
// children from /api/nodes/{id}/children. Expanding a folder = fetching a page.
// /api/graph feeds the flat graph (/graph.html via /api/graph/map and /topic).

const PAGE = 20; // children per page

const $ = (s, r = document) => r.querySelector(s);
const el = (tag, cls, txt) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (txt != null) n.textContent = txt;
  return n;
};
async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) {
    // FastAPI отдаёт ошибку как {"detail": "..."} — и до сих пор это тело
    // уезжало в тост целиком, вместе со скобками и кавычками. Человек читал
    // «не вышло: {"detail":"требуется вход"}» и справедливо считал, что
    // сломалось что-то другое, а не что ему просто надо войти.
    const body = await r.text();
    let msg = body || String(r.status);
    try {
      const j = JSON.parse(body);
      if (typeof j.detail === "string") msg = j.detail;
      else if (Array.isArray(j.detail) && j.detail[0]?.msg) msg = j.detail[0].msg;
    } catch (_) { /* не JSON — показываем как есть */ }
    const err = new Error(msg);
    err.status = r.status;
    throw err;
  }
  return r.status === 204 ? null : r.json();
}
function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.remove("show"), 1800);
}

// Черновик растёт вместе с текстом (потолок — max-height в CSS, дальше прокрутка):
// в окне на две строки свой же довод не перечитать целиком перед отправкой.
// Где браузер знает field-sizing, это делает CSS в index.html, здесь — запасной
// путь. focusin — для черновика, восстановленного из хранилища без ввода.
if (!CSS.supports("field-sizing", "content")) {
  const grow = (e) => {
    const ta = e.target;
    if (ta.tagName !== "TEXTAREA") return;
    ta.style.height = "auto";
    ta.style.height = ta.scrollHeight + ta.offsetHeight - ta.clientHeight + "px";
  };
  document.addEventListener("input", grow);
  document.addEventListener("focusin", grow);
}

// ---- state
let TOPICS = [];
let ME = null;              // the logged-in author (from the session cookie)
const KIDS = new Map();     // nodeId -> {total, children:[...]}  (fetched pages)
const ROOT = new Map();     // nodeId -> topic root id (learned while walking down)
const expanded = new Set();
let selectedId = null;
// ответ на фрагмент: форма ответа регистрирует здесь свой хэндл, а всплывающее
// меню выделения через него ставит тип ребра, якорь и чип «в ответ на …».
let replyHandle = null;
// Поколение отрисовки правой панели. Она собирается из нескольких запросов, и
// часть блоков дописывается ПОЗЖЕ, когда их ответ пришёл. Без счётчика
// запоздавший ответ от прошлого узла дописывался в панель, собранную уже для
// другого: на экране висело два «Голосование по этой проблеме», а при двух
// одновременных selectNode — вся панель целиком в двух экземплярах.
// Поколение поднимает каждый, кто забирает панель себе: selectNode, форма
// создания и пустая панель.
let detailGen = 0;

// ---- hints: contextual tips, toggled from the header, remembered per browser
let HINTS = localStorage.getItem("noo_hints") !== "off";   // on by default
function hint(html) {
  if (!HINTS) return null;
  const h = el("div", "hint");
  h.innerHTML = html;
  return h;
}
function appendHint(container, html) {
  const h = hint(html);
  if (h) container.appendChild(h);
}
function updateHintsBtn() {
  const b = $("#hintsBtn");
  if (b) b.textContent = HINTS ? "💡 Подсказки: вкл" : "💡 Подсказки: выкл";
}
function toggleHints() {
  HINTS = !HINTS;
  localStorage.setItem("noo_hints", HINTS ? "on" : "off");
  updateHintsBtn();
  renderTree();
  if (selectedId != null) selectNode(selectedId);
  else renderEmptyDetail();
}

// The empty detail panel doubles as the "how it works" guide when hints are on.
function renderEmptyDetail() {
  detailGen++;                  // см. newTopicForm: панель забираем себе
  const d = $("#detail");
  d.innerHTML = "";
  d.appendChild(HINTS ? guidePanel() : shortEmpty());
}
function shortEmpty() {
  const e = el("div", "empty");
  e.innerHTML =
    "<h3>Выбери обсуждение слева</h3>" +
    "<p>Верхняя ветка — проблема, вопрос, предложение, тезис или разбор. " +
    "Разворачивай её, чтобы читать доводы за и против, задавать вопросы и " +
    "добавлять свои.</p>" +
    "<p class='muted'>Или начни своё — кнопка «Создать» сверху. " +
    "Подсказки можно выключить кнопкой «💡 Подсказки» вверху.</p>";
  return e;
}
function guidePanel() {
  const g = el("div", "guide");
  g.appendChild(el("h3", null, "Как здесь всё устроено"));
  g.appendChild(el("p", "lead",
    "Noosphere показывает не «кто победил в споре», а как устроено рассуждение: " +
    "каждый довод виден отдельно и связан с тем, к чему относится."));
  const steps = [
    ["1", "Читай дерево слева",
      "Верхние ветки открывают обсуждение — чаще всего это <b>проблема</b>, но " +
      "наверху может стоять и вопрос, предложение, тезис или разбор. Вложенные " +
      "— ответы, и цветная метка показывает связь с родителем: <b>за</b>, " +
      "<b>против</b>, <b>уточнение</b>, <b>вопрос</b>, <b>предложение</b>, " +
      "<b>разбор</b>. У проблемы вдобавок есть состояние: причины, масштаб и " +
      "накопитель попыток решения — у остальных видов его нет."],
    ["2", "PoI — это качество довода",
      "PoI оценивает, насколько довод проработан: логика, полнота, работа с " +
      "неопределённостью и с возражениями. Высокий PoI не значит «прав», значит " +
      "«сделан добросовестно» — поэтому <b>порядок доводов не зависит от PoI</b>: " +
      "внутри одного родителя они идут по времени, иначе первый в списке читался " +
      "бы как ответ. <b>Вес в голосовании</b> — отдельное: его даёт разбор " +
      "проблемы с ИИ в момент голосования, а не PoI твоих доводов."],
    ["3", "Реагируй: ▲ согласен / ▼ не согласен",
      "Так ты показываешь своё отношение к доводу. Согласие и несогласие — это " +
      "не оценка качества: сильный довод остаётся сильным, даже когда с ним " +
      "не согласны."],
    ["4", "Отвечай или заводи своё",
      "Выбери тип ответа (за/против/уточнение/вопрос/предложение/разбор) и " +
      "напиши. Своё сверху заводится кнопкой «Создать», там же выбирается вид. " +
      "Перед отправкой ИИ-компаньон разберёт черновик и с ним можно поспорить " +
      "— а опубликованный текст уже неизменен, поэтому думать стоит здесь. " +
      "Решаешь всё равно ты."],
    ["5", "Позиции — общая карта по обсуждению",
      "ИИ группирует близкие доводы в позиции. Их можно поддержать, оспорить, " +
      "развить или сделать вывод. Если тебя свели не туда — можно выйти в свою " +
      "отдельную позицию (дословно твоими словами)."],
    ["6", "Тренажёр рассуждения",
      "Короткий разговор с ИИ по одному вопросу. Это не экзамен на «правоту»: " +
      "разговор показывает <b>сильные и слабые стороны твоего рассуждения</b>, " +
      "чтобы было видно, что стоит подтянуть. Проходить можно с любым мнением."],
  ];
  for (const [n, title, body] of steps) {
    const s = el("div", "step");
    s.appendChild(el("div", "n", n));
    const col = el("div");
    col.appendChild(el("div", "b", title));
    const p = el("p"); p.innerHTML = body;
    col.appendChild(p);
    s.appendChild(col);
    g.appendChild(s);
  }
  const foot = el("p", "muted");
  foot.style.marginTop = "16px";
  foot.innerHTML = "Выбери обсуждение слева, чтобы начать. Эти подсказки можно " +
    "выключить кнопкой «💡 Подсказки» вверху.";
  g.appendChild(foot);
  return g;
}

// ---- auth: the author of every write is the session, not a dropdown
function renderAuthUI() {
  const u = $("#user");
  const badge = $("#poiBadge");
  badge.innerHTML = "";
  if (ME) {
    u.textContent = ME.name + " (@" + ME.username + ")";
    if (ME.color) u.style.color = ME.color;
    $("#loginBtn").style.display = "none";
    $("#logoutBtn").style.display = "";
    // Раньше здесь была вторая ссылка на ту же страницу — рядом со ссылкой
    // «Тренажёр» в шапке она читалась как две разные кнопки. Вход теперь один,
    // бейдж только показывает балл.
    badge.textContent = ME.dialogue_poi == null
      ? "PoI 10"
      : "PoI диалога: " + Math.round(ME.dialogue_poi);
  } else {
    u.textContent = "наблюдатель";
    u.style.color = "";
    $("#loginBtn").style.display = "";
    $("#logoutBtn").style.display = "none";
  }
}

// ---- фактическая высота ВСЕГО, что стоит над рабочей областью → --hdr.
// Карта позиционируется от неё (position:fixed). Высота непостоянна: на узком
// экране шапка переносится на две-три строки, на поворот телефона число
// меняется, а под шапкой может появиться полоса «условия изменились» — поэтому
// наблюдатель, а не разовый замер, и считаем сумму, а не одну шапку: иначе
// карта залезала бы под полосу.
let hdrObserver = null;
function trackHeaderHeight() {
  const h = document.querySelector("header");
  if (!h) return;
  const bar = $("#termsBar");
  const apply = () => {
    let px = h.getBoundingClientRect().height;
    if (bar && !bar.hidden) px += bar.getBoundingClientRect().height;
    document.documentElement.style.setProperty("--hdr", Math.round(px) + "px");
  };
  apply();
  // Наблюдатель ставится ОДИН раз: функцию зовут повторно, когда полоса
  // появляется или уходит, и каждый вызов заводил бы ещё один.
  if (!hdrObserver && window.ResizeObserver) {
    hdrObserver = new ResizeObserver(apply);
    hdrObserver.observe(h);
    if (bar) hdrObserver.observe(bar);
    addEventListener("orientationchange", () => setTimeout(apply, 150));
  }
}

// ---- присутствие: сколько всего зарегистрировано и сколько здесь сейчас.
// Открытая ручка, работает и для наблюдателя. Молча пропускаем ошибку: счётчик
// — украшение шапки, и упавший запрос не должен ронять загрузку дерева.
async function loadPresence() {
  const box = $("#presence");
  if (!box) return;
  try {
    const s = await api("/api/stats");
    const online = s.online
      ? `<span class="live"><span class="dot"></span><b>${s.online}</b> сейчас</span> · `
      : "";
    box.innerHTML = online + `<b>${s.registered}</b> ` + plural(s.registered,
      "участник", "участника", "участников");
    box.title = `Зарегистрировано: ${s.registered}. ` +
      `Онлайн — те, кто был активен за последние ${s.online_window_min} минут.`;
  } catch (_) { box.innerHTML = ""; }
}

function plural(n, one, few, many) {
  const a = Math.abs(n) % 100, b = a % 10;
  if (a > 10 && a < 20) return many;
  if (b > 1 && b < 5) return few;
  return b === 1 ? one : many;
}

async function loadMe() {
  try { ME = await api("/api/auth/me"); } catch (_) { ME = null; }
  renderAuthUI();
  termsNotice();
}

// «Существенные изменения покажем при входе» — это обещание стоит в самом п. 12
// условий, но выполнить его было нечем: согласие спрашивалось один раз при
// регистрации, сервер не знал, какую редакцию человек читал, а клиент не знал
// текущую. Теперь знают оба, и разошедшиеся версии дают эту полосу.
//
// Полоса не блокирует работу: закрыть площадку человеку, который не успел
// дочитать, — не то же самое, что сообщить ему. Но и молча проставить согласие
// нельзя: «прочитал» нажимает он сам.
async function termsNotice() {
  const bar = $("#termsBar");
  if (!bar) return;
  bar.hidden = true;
  if (!ME) return;
  let cfg;
  try { cfg = await api("/api/config"); } catch (_) { return; }
  const current = cfg && cfg.terms_version;
  if (!current || ME.terms_version === current) return;

  bar.innerHTML = "";
  const t = el("span");
  t.appendChild(el("b", null, "Условия участия изменились. "));
  t.appendChild(document.createTextNode(
    "С 10 сентября черновики и твой разговор с ИИ до публикации сохраняются "
    + "на сервере и видны оператору. "));
  const link = el("a", null, "Прочитать условия →");
  link.href = "/tos.html";
  link.target = "_blank";
  link.rel = "noopener";
  t.appendChild(link);
  bar.appendChild(t);
  bar.appendChild(el("div", "spacer"));

  const ok = el("button", "mini", "прочитал, согласен");
  ok.onclick = async () => {
    ok.disabled = true;
    try {
      await api("/api/account/terms", { method: "POST" });
      ME.terms_version = current;
      bar.hidden = true;
      // Пересчитать сразу: полоса ушла, и без этого карта осталась бы
      // опущенной на её высоту — наблюдатель на display:none не срабатывает.
      trackHeaderHeight();
      toast("спасибо — согласие записано");
    } catch (e) {
      ok.disabled = false;
      toast("ошибка: " + e.message);
    }
  };
  bar.appendChild(ok);
  bar.hidden = false;
  trackHeaderHeight();          // полоса меняет высоту шапки — пересчитать
}

// Вход и регистрация — два режима одной формы, а не одна форма с полями
// «при регистрации». Пришедший войти видит два поля и кнопку «Войти»;
// пришедший заводить аккаунт — пять полей, согласие и «Создать аккаунт».
let AUTH_MODE = "login";

function setAuthMode(mode) {
  AUTH_MODE = mode === "register" ? "register" : "login";
  const reg = AUTH_MODE === "register";
  for (const id of ["authName", "authEmail", "authInvite"]) {
    $("#" + id).style.display = reg ? "" : "none";
  }
  // у строки согласия свой display (flex) — его нельзя затирать пустой строкой
  $("#authTermsLine").style.display = reg ? "flex" : "none";
  $("#doLogin").style.display = reg ? "none" : "";
  $("#doRegister").style.display = reg ? "" : "none";
  $("#forgotLine").style.display = reg ? "none" : "";
  $("#toRegister").style.display = reg ? "none" : "";
  $("#toLogin").style.display = reg ? "" : "none";
  $("#authTitle").textContent = reg ? "Регистрация" : "Вход";
  // Браузеру важно, какое это поле: во входе он подставляет сохранённый пароль,
  // в регистрации — предлагает новый вместо чужого сохранённого.
  $("#authPass").setAttribute("autocomplete", reg ? "new-password" : "current-password");
  $("#authErr").textContent = "";
  hideResend();
}

function hideResend() {
  const line = $("#resendLine");
  if (line) { line.style.display = "none"; line.innerHTML = ""; }
}

function openAuth(msg, mode) {
  $("#authModal").style.display = "flex";
  const ce = $("#checkEmailBox");
  if (ce) { ce.style.display = "none"; ce.innerHTML = ""; }
  AUTH_MODE = mode === "register" ? "register" : "login";
  showForgot(false);
  // ПОСЛЕ showForgot, а не до: он чистит строку ошибки, и написанная раньше
  // причина стиралась молча. Форма открывалась без единого слова о том, зачем
  // её открыли, — «нажал добавить, а мне показали вход» вместо «войди, чтобы
  // собирать своё дерево».
  $("#authErr").textContent = msg || "";
  $("#authUser").focus();
}
function closeAuth() { $("#authModal").style.display = "none"; }

// ---- восстановление доступа
// Серверная часть (/api/auth/forgot + reset.html) была с самого начала, а входа
// в неё из интерфейса не было: забывший пароль упирался в тупик.
function showForgot(on) {
  for (const id of ["authUser", "authPass", "authName", "authEmail",
                    "authInvite", "authTermsLine"]) {
    const el = $("#" + id);
    if (el && on) el.style.display = "none";
  }
  $("#authModal .actions").style.display = on ? "none" : "";
  $("#authSwitchLine").style.display = on ? "none" : "";
  $("#forgotBox").style.display = on ? "" : "none";
  if (on) {
    $("#authTitle").textContent = "Восстановление доступа";
    $("#forgotLine").style.display = "none";
    $("#authErr").textContent = "";
    hideResend();
    // перенести уже введённую почту, чтобы не набирать заново
    const typed = ($("#authEmail").value || "").trim();
    if (typed) $("#forgotEmail").value = typed;
    $("#forgotEmail").focus();
  } else {
    // Что показать обратно — решает режим, а не список «всё, что пряталось»:
    // иначе возврат со страницы восстановления выкладывал поля регистрации
    // тому, кто просто входит.
    $("#authUser").style.display = "";
    $("#authPass").style.display = "";
    setAuthMode(AUTH_MODE);
  }
  const ce = $("#checkEmailBox");
  if (ce && !on) { ce.style.display = "none"; ce.innerHTML = ""; }
}

async function requestReset() {
  const email = ($("#forgotEmail").value || "").trim();
  if (!email) { $("#authErr").textContent = "укажи почту"; return; }
  const btn = $("#doForgot");
  btn.disabled = true; btn.textContent = "отправляю…";
  try {
    const r = await api("/api/auth/forgot", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ email }),
    });
    // Ответ намеренно одинаков и для существующего адреса, и для чужого —
    // иначе форма превращается в проверку «есть ли тут аккаунт у такого-то».
    $("#authErr").textContent = (r && r.detail)
      || "если аккаунт с такой почтой есть, ссылка отправлена";
  } catch (e) {
    $("#authErr").textContent = errText(e);
  } finally {
    btn.disabled = false; btn.textContent = "Прислать ссылку";
  }
}

// every write goes through this: logged in → proceed, otherwise → modal
function requireAuth() {
  if (ME) return true;
  openAuth("для этого действия нужен аккаунт");
  return false;
}

async function doAuth(path) {
  const body = {
    username: $("#authUser").value.trim(),
    password: $("#authPass").value,
  };
  if (path.endsWith("register")) {
    body.name = $("#authName").value.trim() || undefined;
    body.invite = $("#authInvite").value.trim() || undefined;
    body.email = $("#authEmail").value.trim() || undefined;
    // Сервер требует явного согласия. Проверяем и здесь, чтобы человек увидел
    // причину рядом с чекбоксом, а не общей ошибкой формы после запроса.
    body.accept_terms = $("#authTerms").checked;
    if (!body.accept_terms) {
      $("#authErr").textContent = "нужно принять условия и политику данных";
      return;
    }
  }
  try {
    const out = await api(path, {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    // Регистрация теперь НЕ впускает: аккаунт заработает, когда человек
    // откроет ссылку из письма. Показываем это вместо молчаливого «привет».
    if (out && out.check_email) {
      showCheckEmail(body.username, body.password);
      return;
    }
    ME = out;
    renderAuthUI();
    closeAuth();
    toast("привет, " + ME.name);
  } catch (e) {
    let msg = e.message;
    try { msg = JSON.parse(msg).detail || msg; } catch (_) { /* raw */ }
    $("#authErr").textContent = msg;
    // «адрес не подтверждён» — не тупик: даём выслать письмо заново прямо тут,
    // войти-то человек всё равно не может
    if (/не подтверждён/i.test(msg)) offerResend(body.username, body.password);
  }
}

// Экран «проверьте почту»: единственное, что должно быть видно после
// регистрации. Логин и пароль держим в замыкании, чтобы кнопка повторной
// отправки работала без входа — до подтверждения сессии у человека нет.
function showCheckEmail(username, password) {
  // прячем и вход, и восстановление — на этом шаге они только мешают
  showForgot(false);
  for (const id of ["authUser", "authPass", "authName", "authEmail",
                    "authInvite", "authTermsLine", "forgotLine",
                    "authSwitchLine"]) {
    const el = $("#" + id);
    if (el) el.style.display = "none";
  }
  $("#authModal .actions").style.display = "none";
  $("#authTitle").textContent = "Проверьте почту";
  $("#authErr").textContent = "";
  const box = $("#checkEmailBox");
  box.style.display = "";
  box.innerHTML = "";
  const p = el("div", "muted");
  p.style.fontSize = "12.5px";
  p.textContent = "Мы отправили письмо со ссылкой. Аккаунт заработает, когда " +
    "вы её откроете: до этого войти нельзя.";
  box.appendChild(p);
  const acts = el("div", "actions");
  acts.style.marginTop = "8px";
  const again = el("button", "mini", "выслать письмо ещё раз");
  again.onclick = () => resendVerification(username, password, again);
  const done = el("button", "mini primary", "Готово");
  done.onclick = () => { closeAuth(); location.reload(); };
  acts.append(done, again);
  box.appendChild(acts);
}

function offerResend(username, password) {
  const line = $("#resendLine");
  line.style.display = "";
  line.innerHTML = "";
  const b = el("a", "muted", "выслать письмо с подтверждением ещё раз");
  b.href = "#";
  b.style.fontSize = "12.5px";
  b.onclick = (e) => { e.preventDefault(); resendVerification(username, password, b); };
  line.appendChild(b);
}

async function resendVerification(username, password, btn) {
  const label = btn.textContent;
  btn.textContent = "отправляю…";
  try {
    await api("/api/auth/verify/resend", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    $("#authErr").textContent = "письмо отправлено — проверьте почту и спам";
  } catch (e) {
    $("#authErr").textContent = errText(e);
  } finally {
    btn.textContent = label;
  }
}

async function doLogout() {
  await api("/api/auth/logout", { method: "POST" });
  ME = null;
  renderAuthUI();
  toast("вышел");
}

// Дерево — рабочий экран, а не каталог всего: показываются темы, которые
// человек держит у себя. Искать — работа карты. Гость подборки не имеет,
// поэтому видит все темы (и по умолчанию попадает на карту).
let WS_IDS = new Set();          // что уже в подборке — для кнопок на карте
let PREVIEW = null;              // открытая, но НЕ добавленная тема

async function loadTopics() {
  if (ME) {
    TOPICS = await api("/api/workspace");
    WS_IDS = new Set(TOPICS.map(t => t.id));
  } else {
    TOPICS = await api("/api/topics");
    WS_IDS = new Set();
  }
  // Тема, открытая с карты без добавления, живёт в дереве до ухода со
  // страницы — иначе прочитать её было бы негде, а тихо добавлять всё,
  // во что заглянули, значит обессмыслить подборку.
  if (PREVIEW && !WS_IDS.has(PREVIEW.id)) TOPICS = [PREVIEW, ...TOPICS];
  for (const t of TOPICS) ROOT.set(t.id, t.id); // a root is its own topic
  renderTree();
  await openDeepLink();
}

async function workspaceToggle(id, want) {
  // Рабочее дерево — у аккаунта, поэтому без входа кнопка работать не может.
  // Говорим это прямо и открываем форму входа, а не сообщаем об ошибке.
  if (!ME) {
    openAuth("рабочее дерево — у аккаунта: войди, чтобы собирать своё");
    return;
  }
  const title = (MAP_TOPICS_TITLE && MAP_TOPICS_TITLE(id)) || "проблема";
  try {
    await api(`/api/workspace/${id}`, { method: want ? "POST" : "DELETE" });
    if (want) { WS_IDS.add(id); if (PREVIEW && PREVIEW.id === id) PREVIEW = null; }
    else WS_IDS.delete(id);
    await loadTopics();
    if (typeof MapView !== "undefined") MapView.syncWorkspace(WS_IDS);
    // Название в тосте, а не безличное «проблема»: когда добавляешь подряд
    // несколько карточек, только оно и отвечает на вопрос «а эту добавил?».
    toast(want ? `добавлено в дерево: ${title}`
               : `убрано из дерева: ${title} — в графе проблема осталась`);
  } catch (e) { toast("не вышло: " + e.message); }
}

// Название проблемы по id — для сообщений о действии. Живёт здесь, а не в
// MapView: тост показывает дерево, и оно же знает, что вообще открыто.
function MAP_TOPICS_TITLE(id) {
  const t = (typeof MapView !== "undefined" && MapView.titleOf)
    ? MapView.titleOf(id) : null;
  if (t) return t;
  const local = TOPICS.find(x => x.id === id);
  return local ? (local.title || shortLabel(local.text, 40)) : null;
}

// ?topic=N и ?view=map — вход по ссылке извне. Внутри приложения виды
// переключаются без перезагрузки, но ссылкой поделиться всё равно должно быть
// можно, поэтому адрес читается один раз на старте.
// Раскрыть путь до узла и показать его. Родителей ищем по ROOT/дереву: узел
// может лежать глубоко, и без раскрытия предков ссылка приводила бы в
// свёрнутую ветку, где его не видно.
async function revealNode(id, rootId) {
  // Корень узла известен заранее (topic_root_id из карточки) — ставим сразу.
  // selectNode решает по ROOT, корень перед ним или ответ, и без этой записи
  // ответ, до которого дерево ещё не дошло, рисовался с карточками корня:
  // «позиции по обсуждению», размежевание, доска.
  if (rootId != null) ROOT.set(id, rootId);
  try {
    // Родителя карточка узла не отдаёт, поэтому цепочку предков строим по
    // рёбрам графа: без раскрытия предков ссылка приводила бы в свёрнутую
    // ветку, где нужного узла попросту не видно.
    const g = await api("/api/graph");
    const parentOf = new Map();
    (g.links || []).forEach((l) => parentOf.set(l.source, l.target));
    const chain = [];                 // родитель, дед, …, корень
    let cur = id;
    for (let i = 0; i < 50 && parentOf.has(cur); i++) {
      cur = parentOf.get(cur);
      chain.push(cur);
    }
    // Ответы грузим у КАЖДОГО предка сверху вниз и у самого узла, а не только у
    // корня: раньше промежуточные ветки помечались раскрытыми, но оставались
    // пустыми — под ними висело «загрузка…», а сам узел в дерево не попадал.
    for (const a of [...chain.reverse(), id]) {
      expanded.add(a);
      await fetchChildren(a);
    }
    renderTree();
  } catch (e) { /* не смогли раскрыть — узел всё равно откроем в панели */ }
  await selectNode(id);
  const row = document.querySelector(`[data-id="${id}"]`);
  if (row) row.scrollIntoView({ block: "center", behavior: "smooth" });
}

let deepLinkDone = false;
async function openDeepLink() {
  if (deepLinkDone) return;
  deepLinkDone = true;
  const p = new URLSearchParams(location.search);
  if (p.get("view") === "map") { showView("map"); return; }
  if (p.get("newproblem")) { newTopicForm(); return; }
  // ?node=N — прямая ссылка на узел: сами находим его проблему и открываем
  // ветку. Без этого номер, на который ссылаются в текстах, никуда не ведёт.
  const wantNode = Number(p.get("node"));
  if (wantNode) {
    try {
      const n = await api(`/api/nodes/${wantNode}`);
      const root = n.topic_root_id || wantNode;
      await openTopic(root);
      await revealNode(wantNode, root);
      return;
    } catch (e) { toast("узел #" + wantNode + " не найден"); }
  }
  const want = Number(p.get("topic"));
  if (!want || !TOPICS.some(t => t.id === want)) return;
  await openTopic(want);
}

async function fetchChildren(id, { more = false } = {}) {
  const cur = KIDS.get(id);
  const offset = more && cur ? cur.children.length : 0;
  const page = await api(`/api/nodes/${id}/children?limit=${PAGE}&offset=${offset}`);
  const merged = more && cur
    ? { total: page.total, children: cur.children.concat(page.children) }
    : page;
  KIDS.set(id, merged);
  const root = ROOT.get(id) ?? id;
  for (const c of merged.children) ROOT.set(c.id, root);
  return merged;
}

// refresh pages we already show (topics + every expanded node), nothing else
async function refreshVisible() {
  await loadTopics();
  await Promise.all([...expanded].map((id) => fetchChildren(id).catch(() => {})));
  renderTree();
}

// Посевной довод подписан именем персоны, под которой нельзя войти. Без метки
// читатель считает её участником — а это ровно то доверие, которое дороже
// красивой картинки: узнав правду потом, он не поверит и остальному.
// Убрать метку — поставить false здесь (посев от этого не изменится).
const SHOW_SEED_BADGE = true;

function seedBadge(node) {
  if (!SHOW_SEED_BADGE || !node.author_is_seed) return null;
  const b = el("span", "seedtag", "посев");
  b.title = "Довод из посева: имя-персона, войти под ней нельзя, PoI не считался";
  return b;
}

// «Не доказывает» — единственная метка, которую по названию не угадать, и
// спутать её с «против» проще всего. Объяснение висело абзацем над деревом:
// четыре строки постоянного текста ради одного бейджа — несоразмерно, тем
// более что подписи строк легенды уже говорят, почему виды повторяются.
// Смысл переехал в подсказку при наведении — она ничего не занимает на экране.
const UNDERCUT_HINT =
  "Вывод может быть верен, но вот этот кусок его не доказывает. Целится в "
  + "выделенный фрагмент. Проверка на прочность, а не возражение автору.";

function undercutBadge() {
  const b = el("span", "rel undercut", "не доказывает");
  b.title = UNDERCUT_HINT;
  return b;
}

// Уступка (vault: decisions/2026-09-15-concession-act): ответ признаёт часть
// того, на что отвечает, и при этом возражает, уточняет или спрашивает про
// остальное. Отдельная метка, а не вид связи: у одного ответа их бывает две.
const CONCEDE_HINT =
  "Ответ признаёт часть того, на что отвечает. Признанное между ними уже не "
  + "спорно — спор идёт об остальном.";

function concedeBadge(quote) {
  const b = el("span", "rel concede", "признаёт");
  b.title = quote ? CONCEDE_HINT + "\nПризнано: «" + quote + "»" : CONCEDE_HINT;
  return b;
}

// ---- tree render
function relLabel(type) {
  return { support: "за", refute: "против", qualify: "уточнение", restate: "пересказ", question: "вопрос",
           proposal: "предложение", exploration: "разбор", atom: "атом",
           root: "обсуждение",
           undercut: "не доказывает", attribution: "атрибуция" }[type] || type;
}
const KIND_CHIP = { question: "вопрос", proposal: "предложение", exploration: "разбор" };
// Интерфейс русский, а ключи рубрик приходят с сервера латиницей (poi.py) —
// без словаря в панели узла висели бы «problem_fit» и «type: proposal».
const KIND_RU = {
  argument: "тезис", problem: "проблема", question: "вопрос",
  proposal: "предложение", exploration: "разбор", atom: "атом разбора",
  intervention: "интервенция", attribution: "атрибуция", detail: "уточнение",
};
const CRIT_RU = {
  clarity: "ясность",
  depth: "глубина",
  counterargument: "работа с возражением",
  evidence: "обоснованность",
  awareness_of_limits: "видит свои границы",
  relevance: "по делу",
  incisiveness: "бьёт в слабое место",
  generativity: "двигает разговор",
  informativeness: "добавляет новое",
  accuracy: "точность",
  concreteness: "конкретность",
  problem_fit: "отвечает проблеме",
  feasibility: "выполнимость",
  mechanism: "механизм держится",
  path: "есть путь отсюда",
  consequences: "продуманы последствия",
  evenhandedness: "честность к обеим сторонам",
  question_quality: "качество вопросов",
  fact_vs_guess: "факты отделены от догадок",
  coverage: "охват проблемы",
};
// one-sentence label for the tree row (shown in full, wraps up to 3 lines via CSS);
// full text lives in the detail panel. Only a safety cap for sentences without
// punctuation (or absurdly long ones) — normal sentences are never cut.
function shortLabel(text, safetyMax = 320) {
  if (!text) return "";
  const firstSentence = text.match(/^.*?[.!?](?=\s|$)/);
  let s = firstSentence ? firstSentence[0] : text;
  if (s.length > safetyMax) s = s.slice(0, safetyMax - 1).trimEnd() + "…";
  return s;
}
function nodeRow(node, type) {
  const row = el("div", "row" + (node.id === selectedId ? " sel" : ""));
  row.dataset.id = node.id;
  const hasKids = (node.reply_count ?? 0) > 0;
  const tw = el("span", "tw", hasKids ? (expanded.has(node.id) ? "▾" : "▸") : "·");
  tw.onclick = (e) => { e.stopPropagation(); toggleExpand(node.id); };
  row.appendChild(tw);
  // Корень несёт ОДИН бейдж — свой вид (проблема / вопрос / предложение /
  // тезис / разбор). Раньше их было два: «тема» + вид, и первый ничего не
  // сообщал — корень и так виден отступом.
  // Класс = сам вид: у проблемы, вопроса, предложения и разбора разные цвета,
  // иначе корни разной природы выглядят на одно лицо.
  row.appendChild(type === "root"
    ? el("span", "rel " + (node.kind || "argument"),
         KIND_RU[node.kind] || "обсуждение")
    : el("span", "rel " + type, relLabel(type)));
  // уступка — второе действие того же ответа: «против», но часть признаёт
  if (type !== "root" && node.concedes) row.appendChild(concedeBadge(node.concede_quote));
  // a topic root shows its own short title; replies fall back to a text excerpt
  // Название — то же, что в графе и в заголовке панели (vault: decisions/
  // 2026-09-15-one-label). Раньше дерево показывало начало текста, а граф —
  // короткую тему, и найденное в графе в дереве было не узнать. У ответа с
  // темой: название, под ним одна тихая строка текста — контекст не теряется.
  const label = type === "root" ? (node.title || shortLabel(node.text))
    : (node.label || shortLabel(node.text));
  let txt;
  if (type !== "root" && node.label && !node.text.startsWith(node.label.replace(/…$/, ""))) {
    txt = el("span", "txt named");
    txt.append(el("span", "lbl", label), el("span", "exc", shortLabel(node.text, 140)));
  } else {
    txt = el("span", "txt", label);
  }
  txt.title = node.text;
  // Связи между проблемами прямо в списке: без этого две проблемы, одна из
  // которых порождает другую, лежат рядом как равные, а уровень виден только
  // внутри карточки состояния — то есть нигде.
  if (type === "root" && ((node.causes || []).length || (node.effects || []).length)) {
    const pl = el("div", "plinks");
    const chip = (dir, p) => {
      const c = el("span", "pl " + dir, (dir === "up" ? "↑ причина: " : "↓ порождает: ") + p.title);
      c.title = dir === "up" ? "эту проблему порождает: " + p.title
                             : "эта проблема порождает: " + p.title;
      c.onclick = (e) => { e.stopPropagation(); selectNode(p.id); };
      return c;
    };
    for (const p of node.causes || []) pl.appendChild(chip("up", p));
    for (const p of node.effects || []) pl.appendChild(chip("down", p));
    txt.appendChild(pl);
  }
  row.appendChild(txt);
  // отозванный довод виден в дереве как отозванный — иначе на него отвечают,
  // не зная, что автор от него уже отказался
  if (node.retracted_at) {
    const tag = el("span", "rtag", "отозвано");
    tag.title = node.retract_note || "Автор больше не настаивает на этом доводе";
    row.appendChild(tag);
  }
  if (hasKids) row.appendChild(el("span", "poi", "(" + node.reply_count + ")"));
  const poi = el("span", "poi");
  // служебный текст платформы не оценивается вовсе — «…» здесь читалось бы как
  // «оценка вот-вот придёт», а она не придёт никогда. То же и с посевом: у
  // посевных доводов PoI не считался, и рисовать им правдоподобное число
  // значило бы повторить болезнь бота — оценку, которую никто не выставлял.
  // Проблему не оценивают по замыслу: это постановка вреда с состоянием, а не
  // довод, у которого есть вес.
  const unscored = node.author_is_service || node.author_is_seed
                || node.kind === "problem";
  poi.innerHTML = node.poi_score != null ? "PoI <b>" + node.poi_score + "</b>"
                : unscored ? "" : "…";
  if (unscored && node.poi_score == null)
    poi.title = node.author_is_service
      ? "Служебная публикация платформы — в ранжировании не участвует"
      : node.kind === "problem"
      ? "Проблема не оценивается — у неё состояние, а не PoI"
      : "Посевной довод — PoI не считался";
  row.appendChild(poi);
  // имя автора → его публичный профиль (история голосований). Новая вкладка,
  // чтобы не терять обсуждение; клик не выбирает узел.
  let who;
  if (node.author_id) {
    who = el("a", "who", node.author || "—");
    who.href = "/profile.html?id=" + node.author_id;
    who.target = "_blank";
    who.title = "профиль и история голосований — " + (node.author || "");
    who.onclick = (e) => e.stopPropagation();
  } else {
    who = el("span", "who muted", node.author || "—");
  }
  if (node.author_color) who.style.color = node.author_color;
  row.appendChild(who);
  const sb = seedBadge(node);
  if (sb) row.appendChild(sb);

  // Управление подборкой — только у корней и только у вошедшего.
  if (type === "root" && ME) {
    if (PREVIEW && PREVIEW.id === node.id) {
      row.classList.add("preview");
      const keep = el("span", "ws keep", "оставить у себя");
      keep.title = "Сейчас проблема открыта на просмотр и исчезнет при перезагрузке";
      keep.onclick = (e) => { e.stopPropagation(); workspaceToggle(node.id, true); };
      row.appendChild(keep);
    } else {
      // Подпись явная: голый «−» рядом с темой читается как «удалить тему»,
      // хотя убирает её только из ЛИЧНОЙ подборки и ни на кого не влияет.
      const drop = el("span", "ws", "убрать у себя");
      drop.title = "Убрать проблему из своего рабочего дерева. " +
        "В графе она остаётся — её видят все и найдёшь в каталоге";
      drop.onclick = (e) => { e.stopPropagation(); workspaceToggle(node.id, false); };
      row.appendChild(drop);
    }
  }
  row.onclick = () => selectNode(node.id);
  return row;
}
function renderSubtree(container, node, type) {
  container.appendChild(nodeRow(node, type));
  if (!expanded.has(node.id)) return;
  const page = KIDS.get(node.id);
  const box = el("div", "children");
  if (!page) {
    box.appendChild(el("div", "row muted", "загрузка…"));
  } else {
    for (const c of page.children) renderSubtree(box, c, c.rel);
    const left = page.total - page.children.length;
    if (left > 0) {
      const moreRow = el("div", "row muted", `▸ ещё ${left}…`);
      moreRow.onclick = async () => {
        await fetchChildren(node.id, { more: true });
        renderTree();
      };
      box.appendChild(moreRow);
    }
  }
  container.appendChild(box);
}
function renderTree() {
  const tree = $("#tree");
  tree.innerHTML = "";
  if (HINTS) {
    // Две строки, а не одна: тип узла и связь с родителем — разные вещи, и в
    // общем ряду «проблема» читалась как ещё один вид ответа. Слова местами
    // совпадают («вопрос» есть и там, и там) — тем более их надо развести.
    const kinds = el("div", "legend");
    kinds.append(
      el("span", "legend-key", "верхняя ветка — что это:"),
      el("span", "rel problem", "проблема"),
      el("span", "rel question", "вопрос"),
      el("span", "rel proposal", "предложение"),
      el("span", "rel argument", "тезис"),
      el("span", "rel exploration", "разбор"),
    );
    const rels = el("div", "legend");
    rels.append(
      el("span", "legend-key", "вложенная — как относится к тому, под чем стоит:"),
      el("span", "rel support", "за"),
      el("span", "rel refute", "против"),
      el("span", "rel qualify", "уточнение"),
      el("span", "rel restate", "пересказ"),
      el("span", "rel question", "вопрос"),
      el("span", "rel proposal", "предложение"),
      el("span", "rel exploration", "разбор"),
      undercutBadge(),
      concedeBadge(),
    );
    tree.append(kinds, rels);
  }
  if (!TOPICS.length) {
    tree.appendChild(el("div", "muted",
      "Пока пусто. Заведи первое кнопкой «+ Создать» сверху — проблему, "
      + "вопрос, предложение, тезис или разбор."));
    return;
  }
  for (const t of TOPICS) renderSubtree(tree, t, "root");
}
async function toggleExpand(id) {
  if (expanded.has(id)) {
    expanded.delete(id);
    renderTree();
    return;
  }
  expanded.add(id);
  renderTree();                      // shows "загрузка…" immediately
  if (!KIDS.has(id)) await fetchChildren(id);
  renderTree();
}

// ---- своё высказывание: снять / отозвать / приписать примечание
//
// Правки текста нет и не будет (vault: decisions/edit-delete-window). Работа над
// формулировкой идёт ДО отправки, с ИИ-компаньоном; опубликованное слово держит
// чужие ответы и уходит в цепочку. Автору остаются три разных действия:
//   СНЯТЬ — «я передумал это публиковать»: час, и только пока не ответили.
//   ОТОЗВАТЬ — «больше не настаиваю»: всегда, текст и ответы остаются на месте.
//   ПРИМЕЧАНИЕ — «уточняю / здесь ошибся»: всегда, ничего не переписывает.
function timeLeft(untilIso) {
  const mins = Math.ceil((new Date(untilIso) - new Date()) / 60000);
  if (mins <= 0) return null;
  return mins === 1 ? "меньше минуты" : `${mins} мин`;
}

function retractionBanner(node) {
  const b = el("div", "retracted");
  b.append(el("b", null, "Автор отозвал этот довод."));
  if (node.retract_note) b.append(" " + node.retract_note);
  b.append(el("div", "muted",
    "Текст и ответы на него остались: передумавший автор не уносит с собой " +
    "чужие возражения."));
  return b;
}

// Сила в споре (vault: decisions/2026-09-15-dialectic-strength). Словами, не
// числом: пороги условны, а число, которое потом поменяется, читается как оценка.
const DIALECTIC_WORD = {
  untested: "не оспаривался", holds: "держится",
  weakened: "ослаблен", shaken: "сильно ослаблен",
};
const DIALECTIC_HINT =
  "Считается по ветке под доводом, без ИИ: возражения ослабляют его, поддержка "
  + "укрепляет, а возражение, на которое ответили, бьёт слабее. PoI — про сам "
  + "текст, это — про то, как он выдержал спор.";

function dialecticLine(dx) {
  const line = el("div", "dstat-line");
  line.append("в споре: ");
  const w = el("span", "dstat " + dx.verdict, DIALECTIC_WORD[dx.verdict] || dx.verdict);
  w.title = DIALECTIC_HINT;
  line.appendChild(w);
  if (dx.attacks || dx.supports) {
    const parts = [];
    if (dx.attacks) parts.push("возражений: " + dx.attacks);
    if (dx.supports) parts.push("за: " + dx.supports);
    line.append(" · " + parts.join(" · "));
  }
  if (dx.unanswered && dx.unanswered.length) {
    line.append(" · без ответа: ");
    dx.unanswered.forEach((id, i) => {
      if (i) line.append(", ");
      const a = el("a", null, "#" + id);
      a.href = "#";
      a.title = "возражение, на которое ещё никто не ответил";
      a.onclick = (e) => { e.preventDefault(); selectNode(id); };
      line.appendChild(a);
    });
  }
  return line;
}

// РАЗМЕЖЕВАНИЕ (vault: decisions/2026-09-15-alignment-view). Фракция — похожий
// набор согласий; в неё не вступают, её считают. Всем — только размеры групп и
// доводы, по которым они расходятся, без имён: готовый список «кто с кем»
// становится ярлыком на человеке. С именами — только про себя самого.
// ЦЕННОСТИ (vault: decisions/2026-09-15-values). Список живой и приходит с
// сервера — в интерфейсе его не дублируем, иначе новая ценность появилась бы в
// базе и не появилась в формах.
let VALUES_CACHE = null;
function valuesList() {
  if (!VALUES_CACHE) VALUES_CACHE = api("/api/values").then((r) => r.values).catch(() => []);
  return VALUES_CACHE;
}

function valueLine(node, own) {
  if (!node.value && !own) return null;
  const line = el("div", "muted value-line");
  line.append("на что опирается: ");
  if (own) {
    const sel = el("select");
    sel.appendChild(new Option("не указана", ""));
    valuesList().then((list) => {
      for (const v of list) sel.appendChild(new Option(v.name, v.id));
      sel.value = node.value || "";
    });
    sel.title = "метка, а не текст — свою можно сменить или снять";
    sel.onchange = async () => {
      try {
        await api(`/api/nodes/${node.id}/value`, {
          method: "PUT", headers: { "content-type": "application/json" },
          body: JSON.stringify({ id: sel.value || null }),
        });
        toast(sel.value ? "ценность обновлена" : "ценность снята");
        selectNode(node.id);
      } catch (e) { toast("ошибка: " + e.message); }
    };
    line.appendChild(sel);
  } else {
    line.appendChild(el("b", null, node.value_name));
  }
  if (node.value_phrase) line.append(" · «" + node.value_phrase + "»");
  // что это за список и откуда берутся новые ценности — отдельная страница
  const about = el("a", null, "список ценностей");
  about.href = "/values.html";
  about.target = "_blank";
  line.append(" · ");
  line.appendChild(about);
  return line;
}

function alignmentItem(it) {
  if (it.kind === "node") {
    const a = el("a", null, "«" + shortLabel(it.label, 60) + "»");
    a.href = "#";
    a.title = "#" + it.id;
    a.onclick = (e) => { e.preventDefault(); selectNode(it.id); };
    return a;
  }
  return el("span", null, "позиция «" + shortLabel(it.label, 60) + "»");
}

function renderAlignment(body, a) {
  body.textContent = "";
  body.className = "";
  const pub = el("div", "al-pub");
  if (!a.people) {
    pub.append("Отметок пока мало — картины размежевания нет.");
  } else {
    let s = `С отметками: ${a.people} чел.`;
    if (a.groups.length >= 2) s += ` · групп: ${a.groups.length} (${a.groups.map((g) => g.size).join(", ")})`;
    else if (a.groups.length === 1) s += ` · одна группа из ${a.groups[0].size} — явного размежевания нет`;
    else s += ` · групп от ${a.min_group} человек пока нет`;
    if (a.unplaced) s += ` · вне групп: ${a.unplaced}`;
    pub.append(s);
  }
  body.appendChild(pub);

  // на что опираются группы: ценности доводов, с которыми группа в большинстве
  // согласна — спор часто не о фактах, а о том, что важнее
  if (a.groups.length >= 2 && a.groups.some((g) => (g.values || []).length)) {
    body.appendChild(el("div", "al-sub", "на что опираются группы"));
    a.groups.forEach((g, i) => {
      if (!(g.values || []).length) return;
      body.appendChild(el("div", "al-line",
        "группа " + (i + 1) + ": " + g.values.map((v) => v.name).join(", ")));
    });
  }

  if (a.dividing && a.dividing.length) {
    body.appendChild(el("div", "al-sub", "сильнее всего расходятся по"));
    for (const d of a.dividing) {
      const line = el("div", "al-line");
      line.appendChild(alignmentItem(d));
      const shares = d.agree_share.map((x, i) =>
        "группа " + (i + 1) + ": " + (x == null ? "—" : Math.round(x * 100) + "% за")).join(" · ");
      line.appendChild(el("span", "muted", "  " + shares));
      body.appendChild(line);
    }
  }

  if (!a.me) return;
  const me = el("div", "al-me");
  me.appendChild(el("div", "al-sub", "ты — это видно только тебе"));
  if (a.me.marks < a.min_common) {
    me.appendChild(el("div", "muted",
      `Твоих отметок здесь: ${a.me.marks}. Отметь «согласен / не согласен» хотя бы у `
      + `${a.min_common} доводов — появится, с кем ты совпадаешь.`));
    body.appendChild(me);
    return;
  }
  if (a.me.group != null) me.appendChild(el("div", null, "ты ближе к группе " + (a.me.group + 1)));
  const people = (title, list) => {
    if (!list.length) return;
    me.appendChild(el("div", "al-sub2", title));
    for (const p of list) {
      const det = el("details", "al-person");
      det.appendChild(el("summary", null, `${p.name} — сходитесь в ${p.same} из ${p.common}`));
      const row = (label, items) => {
        if (!items.length) return;
        const r = el("div", "al-items");
        r.append(label + ": ");
        items.forEach((it, i) => { if (i) r.append(", "); r.appendChild(alignmentItem(it)); });
        det.appendChild(r);
      };
      row("сходитесь", p.agree_items);
      row("расходитесь", p.differ_items);
      me.appendChild(det);
    }
  };
  people("чаще всего совпадаешь с", a.me.closest);
  people("больше всего расходишься с", a.me.farthest);
  if (!a.me.closest.length && !a.me.farthest.length)
    me.appendChild(el("div", "muted", `пока ни с кем нет ${a.min_common} общих отметок`));
  body.appendChild(me);
}

function alignmentCard(rootId, kind) {
  const card = el("div", "card align");
  card.appendChild(el("div", "section-title",
    "Размежевание в " + (kind === "problem" ? "проблеме" : "обсуждении")));
  appendHint(card, "Считается по отметкам «согласен / не согласен» у доводов и " +
    "позиций. Это вид, а не членство: в группу никто не вступает, ничего не " +
    "начисляется. Всем видны только размеры групп и доводы, по которым они " +
    "расходятся; с кем совпадаешь ты — видишь только ты.");
  const body = el("div", "muted");
  body.textContent = "считаю…";
  card.appendChild(body);
  api(`/api/topics/${rootId}/alignment`)
    .then((a) => renderAlignment(body, a))
    .catch((e) => { body.textContent = "не посчиталось: " + e.message; });
  return card;
}

function concededBlock(list) {
  const wrap = el("div", "conceded");
  wrap.appendChild(el("div", "section-title", "признали часть этого довода"));
  for (const c of list) {
    const line = el("div");
    const who = el("a", null, c.author || "—");
    who.href = "#";
    who.title = "открыть ответ #" + c.source_id;
    who.onclick = (e) => { e.preventDefault(); selectNode(c.source_id); };
    line.append(who, " ");
    if (c.rel) line.appendChild(el("span", "rel " + c.rel, relLabel(c.rel)));
    if (c.anchor_quote) line.append(" признаёт: «" + c.anchor_quote + "»");
    else line.append(" признаёт часть, не уточняя какую");
    wrap.appendChild(line);
  }
  return wrap;
}

function addendaBlock(addenda) {
  const wrap = el("div", "addenda");
  wrap.appendChild(el("div", "section-title", "Примечания автора"));
  for (const a of addenda) {
    const row = el("div", "addendum");
    row.appendChild(el("div", null, a.text));
    row.appendChild(el("div", "muted",
      new Date(a.created_at).toLocaleString("ru-RU")));
    wrap.appendChild(row);
  }
  return wrap;
}

function ownAuthorCard(node, redraw) {
  const c = el("div", "card");
  c.appendChild(el("div", "section-title", "Это твой довод"));
  const rm = node.removability || {};
  const left = rm.removable_until ? timeLeft(rm.removable_until) : null;

  const note = el("div", "muted");
  if (rm.can_remove && left)
    note.textContent = `Снять целиком можно ещё ${left} — пока никто не ответил. ` +
      `Дальше текст остаётся в графе навсегда.`;
  else
    note.textContent = rm.reason
      ? `Снять уже нельзя: ${rm.reason}.`
      : "Текст зафиксирован.";
  c.appendChild(note);
  appendHint(c, "Переписать опубликованное нельзя ни в какой момент — на нём " +
    "строят ответы. Поэтому ИИ-компаньон разбирает черновик <b>до</b> отправки. " +
    "Если передумал позже — <b>отзови</b> довод или добавь <b>примечание</b>.");

  const acts = el("div", "actions");
  if (rm.can_remove) {
    const del = el("button", "mini", "снять целиком");
    del.title = "Довод исчезнет из графа. Пока на него никто не ответил";
    del.onclick = async () => {
      if (!confirm("Снять довод целиком? Отменить это будет нельзя.")) return;
      try {
        await api(`/api/nodes/${node.id}`, { method: "DELETE" });
        toast("довод снят");
        const back = ROOT.get(node.id);
        expanded.delete(node.id);
        KIDS.delete(node.id);
        await refreshVisible();
        // снятый узел больше некому показывать — уходим к корню его темы
        if (back && back !== node.id) selectNode(back); else location.reload();
      } catch (e) { toast(errText(e)); }
    };
    acts.appendChild(del);
  }
  if (!node.retracted_at) {
    const retr = el("button", "mini", "отозвать");
    retr.title = "«Больше не настаиваю» — текст и ответы остаются, но помечены";
    retr.onclick = async () => {
      const why = prompt("Почему отзываешь? (необязательно, одной строкой)");
      if (why === null) return;
      try {
        await api(`/api/nodes/${node.id}/retract`, {
          method: "POST", headers: { "content-type": "application/json" },
          body: JSON.stringify({ note: why || null }),
        });
        toast("довод отозван");
        // дерево рисуется из кеша страниц — без обновления метка «отозвано»
        // появилась бы только после перезагрузки
        await refreshVisible();
        redraw();
      } catch (e) { toast(errText(e)); }
    };
    acts.appendChild(retr);
  }
  const add = el("button", "mini", "добавить примечание");
  add.title = "Датированная приписка сбоку: уточнение или признание ошибки";
  add.onclick = async () => {
    const text = prompt("Примечание к своему доводу:");
    if (!text || !text.trim()) return;
    try {
      await api(`/api/nodes/${node.id}/addendum`, {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ text }),
      });
      toast("примечание добавлено");
      redraw();
    } catch (e) { toast(errText(e)); }
  };
  acts.appendChild(add);
  c.appendChild(acts);
  return c;
}

function errText(e) {
  let msg = e.message;
  try { msg = JSON.parse(msg).detail || msg; } catch (_) { /* raw */ }
  return msg;
}

// ---- detail panel
async function selectNode(id) {
  const gen = ++detailGen;
  selectedId = id;
  renderTree();
  if (!expanded.has(id)) toggleExpand(id);

  let node;
  try { node = await api(`/api/nodes/${id}`); }
  catch (e) { toast("ошибка: " + e.message); return; }
  if (gen !== detailGen) return;      // панель уже пересобрали под другой узел
  const root = ROOT.get(id) ?? id;
  const isRoot = root === id;
  const d = $("#detail");
  d.innerHTML = "";

  // node card — a topic root has a short title (heading) distinct from its
  // body text; a reply has no title, so the heading is its own text
  const card = el("div", "card");
  let textEl;      // основной текст узла — цель выделения для ответа на фрагмент
  // Заголовок — то же название, что в графе и в строке дерева: у корня его
  // заголовок, у ответа — тема от оценки. Пока оценки нет, название совпадает
  // с началом текста — тогда крупно сам текст, без повтора.
  const heading = isRoot ? node.title
    : (node.label && !node.text.startsWith(node.label.replace(/…$/, "")) ? node.label : null);
  if (heading) {
    card.appendChild(el("h2", null, heading));
    textEl = el("div", null, node.text);
    textEl.style.marginBottom = "8px";
  } else {
    textEl = el("h2", null, node.text);
  }
  // обёртка с левым полем под маркеры оспоренных участков
  const tw = el("div", "hasmargins");
  tw.appendChild(textEl);
  card.appendChild(tw);
  // отзыв виден сразу под текстом: читать довод, не зная, что автор от него
  // отказался, — значит спорить с призраком
  if (node.retracted_at) card.appendChild(retractionBanner(node));
  const meta = el("div", "muted");
  // имя автора ведёт на его публичный профиль — историю голосований, из которой
  // люди сами строят транзакционную репутацию
  let authorEl = node.author || "—";
  if (node.author_id) {
    authorEl = el("a", null, node.author || "—");
    authorEl.href = "/profile.html?id=" + node.author_id;
    authorEl.target = "_blank";
    authorEl.title = "профиль и история голосований";
  }
  // an atom of an exploration is a point under investigation: it carries no
  // LLM base score (the разбор was scored as a whole) and no taken position
  const sbDetail = seedBadge(node);
  meta.append(
    "автор: ", authorEl, ...(sbDetail ? [" ", sbDetail] : []),
    "  ·  PoI: " + (node.poi_score != null ? node.poi_score
                    : node.kind === "problem" ? "— (проблема не оценивается: у неё состояние)"
                    : node.atom_group ? "— (атом разбора, живёт реакциями)"
                    : node.author_is_service ? "— (служебная публикация платформы)"
                    : node.author_is_seed ? "— (посевной довод, PoI не считался)"
                    : "оценивается…"),
    "  ·  тип: " + (KIND_RU[node.kind] || node.kind || "тезис"),
    ...(node.atom_group ? ["  ·  из разбора · " + node.atom_group] : []),
    "  ·  ответов: " + (node.reply_count ?? 0),
    "  ·  "
  );
  // Номер — не украшение: агенты и люди ссылаются друг на друга номерами
  // ([91], [92]), и без ссылки по такому номеру не перейти. Клик копирует
  // прямую ссылку на узел, сам номер ведёт на него же.
  const idLink = el("a", null, "#" + node.id);
  idLink.href = "/?node=" + node.id;
  idLink.title = "ссылка на этот узел — клик копирует её";
  idLink.onclick = (e) => {
    e.preventDefault();
    const url = location.origin + "/?node=" + node.id;
    if (navigator.clipboard) navigator.clipboard.writeText(url).then(
      () => toast("ссылка скопирована: " + url), () => {});
  };
  meta.appendChild(idLink);
  card.appendChild(meta);
  // на что опирается довод — у своего можно сменить (метка, не текст)
  if (!isRoot || node.kind !== "problem") {
    const vl = valueLine(node, !!(ME && node.author_id === ME.id));
    if (vl) card.appendChild(vl);
  }
  // В СПОРЕ: выдержал ли довод ветку под собой. У проблемы и вопроса этого нет:
  // они не утверждение, с которым спорят.
  if (node.dialectic && !["problem", "question", "exploration"].includes(node.kind))
    card.appendChild(dialecticLine(node.dialectic));
  appendHint(card, "<b>PoI</b> — насколько довод проработан, а не «правота». " +
    "На порядок в дереве он не влияет: внутри одного родителя доводы идут по " +
    "времени, иначе верхний читался бы как ответ. Вес в голосовании — " +
    "отдельное: его даёт разбор проблемы с ИИ при голосовании, не PoI доводов.");
  if (node.poi_breakdown && !node.poi_breakdown.seed) card.appendChild(critBreakdown(node.poi_breakdown));

  // reactions live INSIDE the argument card, right under the text — not a
  // separate block. Показываем простые счётчики: формула веса голоса после
  // poi-weight-restored (2026-07-21) ещё не зафиксирована, и рисовать «Σ PoI»
  // до этого — значит показывать число, которое потом изменится.
  const rwrap = el("div");
  rwrap.style.marginTop = "14px";
  rwrap.style.paddingTop = "12px";
  rwrap.style.borderTop = "1px solid var(--line)";
  const rbody = el("div", "muted"); rbody.textContent = "загрузка…";
  rwrap.appendChild(rbody);
  appendHint(rwrap, "Реакция — твоё отношение к доводу. На его PoI она не влияет: " +
    "качество оценивается по самому рассуждению, а не по числу согласных.");
  const ract = el("div", "actions");
  const agree = el("button", "mini", "▲ согласен");
  const dis = el("button", "mini", "▼ не согласен");
  agree.dataset.rx = "agree";            // loadReactions подсвечивает свою сторону
  dis.dataset.rx = "disagree";
  agree.onclick = () => react(id, root, "agree");
  dis.onclick = () => react(id, root, "disagree");
  ract.append(agree, dis);
  rwrap.appendChild(ract);
  card.appendChild(rwrap);
  // примечания автора — единственное, что прирастает к зафиксированному тексту
  if (node.addenda && node.addenda.length)
    card.appendChild(addendaBlock(node.addenda));
  // кто признал часть этого довода: сближение видно на самом доводе, а не
  // только в ответах, которые ещё надо раскрыть
  if (node.conceded_by && node.conceded_by.length)
    card.appendChild(concededBlock(node.conceded_by));
  // принадлежность нескольким проблемам (домашняя + принесённые) и маркеры
  // оспоренных участков — до реакций визуально не мешают, кладём в конец карточки
  if (node.belongings && node.belongings.length > 1)
    card.appendChild(belongingBar(node.belongings));
  d.appendChild(card);
  // распоряжаться своим доводом может только его автор
  if (ME && node.author_id === ME.id)
    d.appendChild(ownAuthorCard(node, () => selectNode(id)));
  // выделение текста узла → всплывающее меню типизированного ответа с якорем
  attachFragmentSelection(textEl);
  // маркеры оспоренных участков «на полях» — после вставки в DOM (нужен layout)
  if (node.fragment_replies && node.fragment_replies.length)
    renderMarginMarkers(tw, textEl, node.fragment_replies);
  loadReactions(id, root, rbody);

  // страница состояния — только у проблемы: причины, масштаб, накопитель
  // решений, атрибуции. У вопроса, предложения, тезиса и разбора состояния
  // нет по устройству — они живут деревом доводов и позициями.
  if (isRoot && node.kind === "problem") {
    const pcard = await problemCard(id);
    // Сторож нужен и ЗДЕСЬ, а не только после первого запроса: это второй и
    // последний await после очистки панели. Без него два одновременных
    // selectNode дописывали в одну панель по очереди, и на экране висело по
    // два «Проблема · состояние», «Решения на столе» и так далее.
    if (gen !== detailGen) return;
    d.appendChild(pcard);
  }
  // Голосование знает свой корень, а корень о голосовании молчал: войти в
  // него можно было только через отдельный раздел, зная, что оно вообще есть.
  // Привязывается голосование к ЛЮБОМУ корню (см. /api/decisions), поэтому и
  // показывается у любого — не только у проблемы.
  if (isRoot) {
    // Место под голосования занимается СРАЗУ, наполняется по приходе ответа —
    // иначе блок приезжал в самый низ панели, ниже позиций, куда его никто не
    // клал: он просто дописывался последним, когда запрос успевал вернуться.
    const vw = el("div");
    d.appendChild(vw);
    votesForProblem(id, node.kind).then((card) => card && vw.appendChild(card));
  }
  // Доска: решения на столе и открытые вопросы. Читается ПОСЛЕ состояния (у
  // проблемы) и ДО формы ответа — сначала видно, что уже предложено и что
  // осталось без ответа, потом пишешь своё.
  if (isRoot) d.appendChild(boardCards(id, node.kind));
  // Размежевание: как люди разделились и с кем совпадаешь ты. Тоже до формы
  // ответа — видно, где спор на самом деле идёт, прежде чем писать своё.
  if (isRoot) d.appendChild(alignmentCard(id, node.kind));

  // atomization: the author of an exploration can cut it into atoms
  if (node.kind === "exploration" && ME && node.author_id === ME.id)
    d.appendChild(atomizeCard(node));

  // position membership (п.10): show the author where their argument landed and
  // let them reject the composed text they were folded into
  if ((node.kind || "argument") === "argument" && node.position_id
      && !node.atom_group && node.position_headline)
    d.appendChild(positionMembershipCard(node));

  // reply form
  d.appendChild(replyForm(id));

  // positions (only for the discussion root)
  if (isRoot) {
    // «по проблеме» — только когда наверху действительно проблема: у корня-
    // вопроса или предложения этот заголовок обещал бы страницу, которой нет
    const where = node.kind === "problem" ? "проблеме" : "обсуждению";
    const pc = el("div", "card");
    pc.appendChild(el("div", "section-title", "Позиции по " + where));
    appendHint(pc, "ИИ сводит близкие доводы в <b>позиции</b> — общую карту " +
      "мнений по " + where + ". Позицию можно поддержать, оспорить, развить или выйти из " +
      "неё в свою, если тебя свели не туда.");
    const pbody = el("div"); pbody.textContent = "сборка позиций…";
    pc.appendChild(pbody);
    d.appendChild(pc);
    loadPositions(root, pbody);

    // Линза «участники · PoI в теме» убрана (решение 2026-07-22): система не
    // считает и не показывает постоянное «стояние» участника. PoI считает только
    // ИИ-судья при голосовании; транзакционную репутацию люди строят сами по
    // публичной истории голосований (профиль аккаунта). Накопительный слой под
    // капотом (author_topic_poi, сила поддержки позиций) — отдельная уборка,
    // сцеплен с редизайном позиций/реакций.
  }
}

function critBreakdown(bd) {
  const box = el("div", "crit");
  for (const [k, v] of Object.entries(bd)) {
    if (["topic", "comment", "kind", "seed"].includes(k)) continue;
    box.appendChild(el("span", "muted", CRIT_RU[k] || k));
    const bar = el("div", "bar");
    const span = el("span"); span.style.width = Math.max(0, Math.min(100, v)) + "%";
    bar.appendChild(span);
    box.appendChild(bar);
    box.appendChild(el("span", null, String(v)));
  }
  if (bd.comment) {
    const c = el("div", "muted"); c.style.gridColumn = "1/4"; c.style.marginTop = "4px";
    c.textContent = "“" + bd.comment + "”";
    box.appendChild(c);
  }
  return box;
}

// ---- atomization (vault: exploration-atomization): the AI proposes groups
// and atoms cut from the разбор in the author's own words; the author edits,
// unchecks, and confirms — nothing reaches the graph without their consent.
const ATOM_LABEL = { argument: "тезис", question: "вопрос",
                     detail: "дополнение", proposal: "предложение" };

function positionMembershipCard(node) {
  const card = el("div", "card");
  const own = node.position_stance === "dissent" || node.dissented;
  card.appendChild(el("div", "section-title",
    own ? "твоя отдельная позиция" : "позиция, в которую вошёл довод"));
  card.appendChild(el("div", null, node.position_headline || ""));
  if (node.position_composed && !own) {
    const c = el("div", "muted", node.position_composed);
    c.style.marginTop = "6px";
    card.appendChild(c);
  }
  if (own) {
    card.appendChild(el("div", "muted",
      "вынесено в собственную позицию — показывается твоими словами дословно."));
    return card;
  }
  // only the author can dissent from how their own argument was composed
  if (ME && node.author_id === ME.id) {
    card.appendChild(el("div", "muted",
      "это ИИ-композиция пула, включающая твой довод. Если она искажает " +
      "твою мысль — вынеси довод в отдельную позицию (дословно)."));
    const act = el("div", "actions");
    const btn = el("button", "mini", "не согласен с трактовкой — выйти");
    btn.onclick = async () => {
      if (!confirm("Вынести твой довод в отдельную позицию? Пул будет " +
                   "перекомпонован без него.")) return;
      btn.disabled = true; btn.textContent = "выношу…";
      try {
        await api(`/api/nodes/${node.id}/dissent`, { method: "POST" });
        toast("довод вынесен в собственную позицию");
        selectNode(node.id);
      } catch (e) {
        toast("ошибка: " + e.message);
        btn.disabled = false; btn.textContent = "не согласен с трактовкой — выйти";
      }
    };
    act.appendChild(btn);
    card.appendChild(act);
  }
  return card;
}

function atomizeCard(node) {
  const card = el("div", "card");
  card.appendChild(el("div", "section-title", "атомизация разбора"));
  card.appendChild(el("div", "muted",
    "ИИ предложит разбить разбор на группы и атомы (тезисы, вопросы, " +
    "дополнения, предложения) твоими же словами; ты правишь и подтверждаешь — " +
    "без подтверждения в граф ничего не попадает."));
  const act = el("div", "actions");
  const go = el("button", "mini", "⚛ предложить атомизацию");
  const box = el("div");
  box.style.display = "none";
  go.onclick = async () => {
    go.disabled = true; go.textContent = "ИИ читает разбор…";
    let pre;
    try { pre = await api(`/api/nodes/${node.id}/atomize`, { method: "POST" }); }
    catch (e) {
      toast("ошибка: " + e.message);
      go.disabled = false; go.textContent = "⚛ предложить атомизацию";
      return;
    }
    go.disabled = false; go.textContent = "⚛ предложить заново";
    if (pre.already_atomized) {
      go.disabled = true; go.textContent = "разбор уже атомизирован";
      box.style.display = ""; box.innerHTML = "";
      box.appendChild(el("div", "muted", "этот разбор уже разбит на атомы"));
      return;
    }
    renderAtomPreview(box, node, pre.groups || []);
  };
  act.appendChild(go);
  card.appendChild(act);
  card.appendChild(box);
  return card;
}

function renderAtomPreview(box, node, groups) {
  box.innerHTML = "";
  box.style.display = "";
  if (!groups.length) {
    box.appendChild(el("div", "muted", "ИИ не нашёл, что атомизировать"));
    return;
  }
  const rows = [];
  for (const g of groups) {
    box.appendChild(el("div", "section-title", "группа: " + g.title));
    for (const a of g.atoms) {
      const line = el("div");
      const chk = el("input");
      chk.type = "checkbox"; chk.checked = true; chk.title = "включить этот атом";
      line.appendChild(chk);
      line.appendChild(el("span", "rel " + (a.type === "question" ? "question" : "qualify"),
                          ATOM_LABEL[a.type] || a.type));
      const ta = el("textarea");
      ta.value = a.text; ta.rows = 2;
      line.appendChild(ta);
      box.appendChild(line);
      rows.push({ chk, ta, type: a.type, group: g.title });
    }
  }
  const act = el("div", "actions");
  const make = el("button", "primary", "создать атомы");
  make.onclick = async () => {
    const atoms = rows.filter((r) => r.chk.checked && r.ta.value.trim())
      .map((r) => ({ type: r.type, text: r.ta.value.trim(), group: r.group }));
    if (!atoms.length) { toast("не выбран ни один атом"); return; }
    make.disabled = true; make.textContent = "создаю…";
    try {
      const res = await api(`/api/nodes/${node.id}/atomize/confirm`, {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ atoms }),
      });
      toast(res.already_atomized
        ? "разбор уже был атомизирован — дубли не создаются"
        : "создано атомов: " + res.created.length);
      box.style.display = "none";
      expanded.add(node.id);
      await fetchChildren(node.id);
      await loadTopics();
      selectNode(node.id);
    } catch (e) {
      toast("ошибка: " + e.message);
      make.disabled = false; make.textContent = "создать атомы";
    }
  };
  const cancel = el("button", "mini", "отмена");
  cancel.onclick = () => { box.style.display = "none"; };
  act.append(make, cancel);
  box.appendChild(act);
}

// bucketRow (PoI-гистограмма поддержки) удалена 2026-07-22: поддержка позиции —
// число людей, не распределение по PoI-«стоянию».

// ИСТОРИЯ МНЕНИЙ (vault: decisions/2026-09-15-opinion-history). Ценно не только
// «сколько сейчас за и против», но и кто передумал и почему. История —
// информация, а не балл: за смену стороны ничего не начисляется.
// Своя нынешняя сторона по узлу: по ней клик по другой кнопке — смена стороны.
const REACT_MINE = new Map();
const STANCE_WORD = { agree: "за", disagree: "против" };

// «#43» в тексте — ссылка на узел: люди и так ссылаются номерами
function appendWithNodeRefs(parent, text) {
  for (const part of String(text).split(/(#\d+)/)) {
    const m = /^#(\d+)$/.exec(part);
    if (m) {
      const a = el("a", null, part);
      a.href = "#";
      a.onclick = (e) => { e.preventDefault(); selectNode(Number(m[1])); };
      parent.appendChild(a);
    } else if (part) parent.append(part);
  }
}

function opinionHistory(changes) {
  const wrap = el("div", "ophist");
  wrap.appendChild(el("div", "section-title", "история мнений"));
  for (const c of changes.slice().reverse()) {          // свежие сверху
    const line = el("div", "oph-line");
    line.append((c.author || "—") + ": " + STANCE_WORD[c.from] + " → " + STANCE_WORD[c.to]);
    const when = new Date(c.ts).toLocaleString("ru-RU",
      { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
    line.appendChild(el("span", "muted", " · " + when));
    if (c.why) {
      const w = el("div", "oph-why");
      w.append("«");
      appendWithNodeRefs(w, c.why);
      w.append("»");
      line.appendChild(w);
    }
    wrap.appendChild(line);
  }
  return wrap;
}

async function loadReactions(id, root, target) {
  try {
    const r = await api(`/api/reactions/${id}?topic=${root}`);
    REACT_MINE.set(id, r.mine || null);
    // своя отметка видна на кнопке: без этого непонятно, что нажатие другой — смена
    const box = target.parentNode;
    if (box) box.querySelectorAll("[data-rx]").forEach((b) => {
      const on = b.dataset.rx === r.mine;
      b.classList.toggle("on", on);
      b.title = on ? "твоя отметка сейчас" : "";
    });
    target.textContent = "";
    // простые счётчики — формула веса ещё открыта (poi-weight-restored)
    if (!r.agree.count && !r.disagree.count) {
      target.append("Пока нет реакций — будь первым.");
    } else {
      const a = el("span"); a.style.color = "var(--green)";
      a.textContent = `▲ ${r.agree.count} согласны`;
      const sep = el("span", "muted", "   ·   ");
      const dsp = el("span"); dsp.style.color = "var(--red)";
      dsp.textContent = `▼ ${r.disagree.count} не согласны`;
      target.append(a, sep, dsp);
    }
    if ((r.changes || []).length) target.appendChild(opinionHistory(r.changes));
  } catch (e) { target.textContent = "ошибка: " + e.message; }
}

// Смена стороны: одно необязательное «почему». null — человек передумал менять.
function askWhy(stance) {
  return new Promise((resolve) => {
    const back = el("div", "modal");
    back.style.display = "flex";
    const card = el("div", "card modal-card");
    card.appendChild(el("div", "section-title", "Меняешь сторону на «" + STANCE_WORD[stance] + "»"));
    card.appendChild(el("div", "muted",
      "Почему — необязательно, можно оставить пустым. Строка попадёт в историю " +
      "мнений у этого довода; на довод можно сослаться номером, например #43. " +
      "Ничего не начисляется."));
    const ta = el("textarea");
    ta.maxLength = 280;
    ta.rows = 3;
    ta.placeholder = "что тебя переубедило (необязательно)";
    ta.style.width = "100%";
    ta.style.marginTop = "8px";
    card.appendChild(ta);
    const act = el("div", "actions");
    const done = (v) => { back.remove(); resolve(v); };
    const save = el("button", "primary mini", "сохранить");
    save.onclick = () => done(ta.value.trim());
    const skip = el("button", "mini", "без объяснения");
    skip.onclick = () => done("");
    const cancel = el("button", "mini", "отмена");
    cancel.onclick = () => done(null);
    act.append(save, skip, cancel);
    card.appendChild(act);
    back.appendChild(card);
    document.body.appendChild(back);
    ta.focus();
  });
}

// loadTopicPoi (линза «PoI в теме») удалена 2026-07-22: система не показывает
// постоянное «стояние» участника; репутацию люди строят по истории голосований.

async function react(id, root, stance) {
  if (!requireAuth()) return;
  const mine = REACT_MINE.get(id);
  const switching = !!mine && mine !== stance;
  let why = "";
  if (switching) {
    why = await askWhy(stance);
    if (why === null) return;                    // передумал менять
  }
  try {
    await api("/api/reactions", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ node_id: id, stance, why: why || undefined }),
    });
    toast(switching ? "сторона изменена" : "реакция учтена");
    if (selectedId === id) selectNode(id);
  } catch (e) { toast("ошибка: " + e.message); }
}


// ---- необратимость: сказать до, а не после
//
// Опубликованный текст неизменен (vault: decisions/edit-delete-window), и
// узнать об этом человек должен ДО отправки. Подсказки в форме для этого не
// годятся: их можно выключить, и тогда единственное упоминание исчезает. Один
// раз на браузер показываем явное окно, дальше — тихая строка у кнопки.
function irreversibleNote() {
  const n = el("div", "muted");
  n.style.fontSize = "12px";
  n.textContent = "опубликованное не редактируется";
  n.title = "Снять свой довод можно в течение часа, пока никто не ответил. " +
            "Позже — только отозвать или добавить примечание";
  return n;
}

function confirmIrreversible() {
  if (localStorage.getItem("noo_irrev_ok") === "1") return Promise.resolve(true);
  return new Promise((resolve) => {
    const back = el("div", "modal");
    back.style.display = "flex";
    const card = el("div", "card modal-card");
    card.appendChild(el("div", "section-title", "Прежде чем опубликовать"));
    card.appendChild(el("div", null,
      "Опубликованный текст нельзя отредактировать — ни сейчас, ни потом. " +
      "На нём строят ответы, и он остаётся в общем корпусе."));
    const list = el("div", "muted");
    list.style.marginTop = "8px";
    list.textContent = "Снять свой довод целиком можно в течение часа и только " +
      "пока никто не ответил. Позже остаются отзыв («больше не настаиваю») и " +
      "примечание сбоку.";
    card.appendChild(list);
    const acts = el("div", "actions");
    const ok = el("button", "primary", "Понятно, публикую");
    const no = el("button", null, "Вернуться к тексту");
    ok.onclick = () => { localStorage.setItem("noo_irrev_ok", "1"); back.remove(); resolve(true); };
    no.onclick = () => { back.remove(); resolve(false); };
    acts.append(ok, no);
    card.appendChild(acts);
    back.appendChild(card);
    document.body.appendChild(back);
    ok.focus();
  });
}

// ---- AI navigator: pre-publication check against the topic's positions.
// A suggestion, never a block — "отправить всё равно" is always available.
async function precheck(root, text) {
  try {
    return await api(`/api/topics/${root}/precheck`, {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ text }),
    });
  } catch (_) { return { verdict: "new" }; }   // fail-open
}

// ---- AI navigator, full pass (vault: ai-navigator-draft-review): BEFORE a
// draft is published, one call checks its type, suggests one improvement and
// looks for existing answers/counters in the graph. A suggestion, never a
// block — fail-open: any error means "post as is".
async function reviewDraft(payload) {
  try {
    return await api("/api/draft/review", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch (e) {
    // Still fail-open — a dead navigator never blocks publishing. But an
    // exhausted grant is the one error the author can act on, so it gets
    // said out loud instead of looking like the AI just went quiet.
    const msg = String(e && e.message || "");
    if (msg.includes("бюджет") || msg.includes("исчерпан"))
      toast("ИИ-бюджет исчерпан — компаньон отключён, публиковать можно");
    return null;
  }
}

function reviewHasNotes(rev) {
  return !!rev && (!rev.type_ok || !!rev.quality_note || rev.verdict !== "new"
                   || (rev.placement && rev.placement !== "here") || !!rev.think
                   || !!rev.concedes);
}

// ---- Черновик переживает обновление страницы.
//
// Опубликованное не правится (vault: decisions/edit-delete-window), поэтому вся
// работа над текстом происходит ДО отправки — и раньше она держалась в памяти
// вкладки: случайный F5 уносил и черновик, и весь разговор с компаньоном.
// Храним у себя, в браузере автора: на сервере черновиков по-прежнему нет
// (vault: decisions/ai-navigator-draft-review), запись видна только ему и
// стирается публикацией.
const DRAFT_TTL_MS = 7 * 24 * 60 * 60 * 1000;

function draftKey(id) {
  return `noo_draft_${(ME && ME.id) || "anon"}_${id ?? "root"}`;
}

function draftRead(id) {
  try {
    const raw = localStorage.getItem(draftKey(id));
    if (!raw) return null;
    const d = JSON.parse(raw);
    if (!d || Date.now() - (d.ts || 0) > DRAFT_TTL_MS) { draftDrop(id); return null; }
    return d;
  } catch (e) { return null; }
}

function draftWrite(id, patch) {
  try {
    const cur = draftRead(id) || {};
    localStorage.setItem(draftKey(id),
                         JSON.stringify({ ...cur, ...patch, ts: Date.now() }));
  } catch (e) { /* приватный режим или переполнение — молча живём дальше */ }
}

function draftDrop(id) {
  try { localStorage.removeItem(draftKey(id)); } catch (e) {}
}

function draftAge(ts) {
  const m = Math.round((Date.now() - ts) / 60000);
  if (m < 1) return "только что";
  if (m < 60) return `${m} мин назад`;
  const h = Math.round(m / 60);
  return h < 24 ? `${h} ч назад` : `${Math.round(h / 24)} дн назад`;
}


// ---- ИИ-компаньон: разговор о ЧЕРНОВИКЕ, пока он ещё черновик.
//
// Разбор выше — один ход: ИИ сказал, автор послушался или нет. Здесь автор может
// возразить, и разговор продолжается. Это стало обязательным, когда мы убрали
// правку опубликованного текста (vault: decisions/edit-delete-window): думать
// вместе можно только ДО отправки.
//
// История живёт здесь, в замыкании формы, и умирает вместе с публикацией —
// на сервере черновиков не остаётся.
async function companionTurn(payload) {
  return await api("/api/draft/companion", {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
}

function companionThread(hint, { getText, setText, connectTo, opening, restore }) {
  const history = [];
  const box = el("div", "companion-box");
  const log = el("div", "companion-log");
  box.appendChild(log);

  function say(role, text) {
    const line = el("div", "cmsg " + role);
    line.appendChild(el("div", "who", role === "author" ? "ты" : "компаньон"));
    line.appendChild(el("div", null, text));
    log.appendChild(line);
    log.scrollTop = log.scrollHeight;
  }
  // Сначала поднимаем сохранённое, потом — вступительную реплику, и только
  // если говорить ещё не начинали: иначе разговор открывался бы вопросом
  // компаньона поверх уже состоявшегося обсуждения.
  if (restore && restore.length) {
    restore.forEach((h) => {
      history.push(h);
      say(h.role === "author" ? "author" : "companion", h.text);
    });
  } else if (opening) {
    history.push({ role: "companion", text: opening });
    say("companion", opening);
  }

  const ta = el("textarea");
  ta.rows = 2;
  ta.placeholder = "ответить компаньону — или спросить его самому";
  box.appendChild(ta);

  const acts = el("div", "actions");
  const send = el("button", "mini", "ответить");
  const wider = el("button", "mini", "поискать шире по каталогу");
  wider.title = "Компаньон посмотрит не только эту проблему, но и остальные";
  let scope = "near";

  async function turn(msg) {
    if (!msg) return;
    say("author", msg);
    history.push({ role: "author", text: msg });
    draftWrite(connectTo, { text: getText(), history });
    ta.value = "";
    send.disabled = true; send.textContent = "думает…";
    try {
      const out = await companionTurn({
        text: getText(), connect_to: connectTo, history, scope,
      });
      say("companion", out.reply);
      history.push({ role: "companion", text: out.reply });
      draftWrite(connectTo, { text: getText(), history });
      // Потолок разговора существует (он платный), но упираться в него молча —
      // значит получить ошибку вместо предупреждения. Считаем вслух с трёх.
      if (typeof out.turns_left === "number" && out.turns_left <= 3) {
        const left = el("div", "muted");
        left.style.fontSize = "12.5px";
        left.textContent = out.turns_left > 0
          ? `осталось ходов: ${out.turns_left} — дальше только публиковать`
          : "ходы кончились — публикуй или начни разговор заново";
        log.appendChild(left);
      }
      if (out.suggestion) {
        const s = el("div", "csuggest");
        s.appendChild(el("div", "muted", "предлагает формулировку:"));
        s.appendChild(el("div", null, out.suggestion));
        const take = el("button", "mini", "взять её");
        take.onclick = () => {
          setText(out.suggestion);
          s.appendChild(el("span", "muted", " — вставлено в черновик"));
          take.remove();
        };
        s.appendChild(take);
        log.appendChild(s);
        log.scrollTop = log.scrollHeight;
      }
    } catch (e) {
      say("companion", "не отвечаю сейчас: " + errText(e) +
          " — можно публиковать и без меня");
    } finally {
      send.disabled = false; send.textContent = "ответить";
    }
  }

  send.onclick = () => turn(ta.value.trim());
  ta.onkeydown = (e) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); send.click(); }
  };
  wider.onclick = () => {
    scope = "map";
    wider.remove();
    turn("Посмотри шире: может, этому месту в графе есть лучшая альтернатива?");
  };
  acts.append(send, wider);
  box.appendChild(acts);
  hint.appendChild(box);
  return box;
}

const TYPE_LABEL = { support: "за", refute: "против", qualify: "уточнение", restate: "пересказ",
                     question: "вопрос", proposal: "предложение", exploration: "разбор" };

// Renders the navigator's suggestions into `hint`. The author stays in charge:
// callbacks wire "switch type", "go to the node", "support the position",
// "post as is" and "cancel"; editing the draft and resending re-reviews it.
function renderReview(hint, rev, { root, onSend, onSwitch, onSupport, onSplit,
                                   onCause, onConcede, onValue, switchLabel, getText, setText,
                                   connectTo, rootTest }) {
  hint.innerHTML = "";
  hint.style.display = "";
  hint.className = "card";
  hint.style.borderColor = "var(--bronze)";
  hint.appendChild(el("div", "section-title", "ИИ-компаньон — разбор перед публикацией"));
  appendHint(hint, "Опубликованный текст правится <b>только здесь</b>: после " +
    "отправки он неизменен. Возражай компаньону, спрашивай его — он для этого.");

  const actions = el("div", "actions");

  if (rootTest && !rev.type_ok) {
    // Проблема проходит ТЕСТ на заявленный вред, а не классификацию по видам,
    // — поэтому кнопки «отправить как …» здесь нет: навигатор не называет
    // вид, он говорит только «вреда не видно». Переключить вид автор может
    // сам, списком слева от «опубликовать»; «отправить как есть» ниже.
    const t = el("div");
    t.appendChild(el("b", null, "похоже, это пока не проблема. "));
    t.appendChild(document.createTextNode(rev.type_note || ""));
    hint.appendChild(t);
    hint.appendChild(el("div", "muted",
      "Это не запрет: можно дописать постановку, сменить вид в списке слева "
      + "от «опубликовать» — вопрос, предложение, тезис, разбор — или "
      + "отправить как есть."));
  } else if (!rootTest && !rev.type_ok && rev.suggested_type) {
    const t = el("div");
    t.appendChild(el("b", null, "похоже, тип не совпадает. "));
    t.appendChild(document.createTextNode(rev.type_note || ""));
    hint.appendChild(t);
    // the quality note and verdict were already computed for the suggested
    // type (one LLM call covers both versions) — switching publishes right away
    const sw = el("button", "mini",
      "отправить как «" + (switchLabel || TYPE_LABEL[rev.suggested_type] || rev.suggested_type) + "»");
    sw.onclick = () => { hint.style.display = "none"; onSwitch(rev.suggested_type); };
    actions.appendChild(sw);
  }

  if (rev.verdict === "answered" || rev.verdict === "countered" || rev.verdict === "said") {
    // «said» — тот же тезис уже есть (повтор, не возражение); свой узел — своя подпись
    hint.appendChild(el("div", "section-title",
      rev.verdict === "answered" ? "на этот вопрос уже есть ответ"
      : rev.verdict === "countered" ? "на этот тезис уже есть возражение"
      : rev.node_own ? "ты это уже писал" : "это уже сказано в обсуждении"));
    hint.appendChild(el("b", null, rev.target_text || ""));
    // узел может быть из ДРУГОГО обсуждения (найден по смыслу, на любом языке)
    const foreign = rev.node_root && rev.node_root !== root;
    if (foreign) hint.appendChild(el("div", "muted", "в обсуждении «" + (rev.node_topic || "#" + rev.node_root) + "»"));
    if (rev.note) hint.appendChild(el("div", "muted", rev.note));
    const go = el("button", "mini", foreign ? "перейти к узлу в том обсуждении" : "перейти к узлу");
    go.onclick = () => {
      hint.style.display = "none";
      const r = rev.node_root ?? root;
      if (r != null) ROOT.set(rev.node_id, r);
      if (foreign) openTopic(r).catch(() => {}).then(() => selectNode(rev.node_id));
      else selectNode(rev.node_id);
    };
    actions.appendChild(go);
  } else if (rev.verdict === "similar" || rev.verdict === "covered") {
    hint.appendChild(el("div", "section-title",
      rev.verdict === "covered" ? "это уже есть в обсуждении" : "похожая позиция уже есть"));
    hint.appendChild(el("b", null, rev.headline || ""));
    if (rev.note) hint.appendChild(el("div", "muted", rev.note));
    if (onSupport) {
      const supp = el("button", "mini", "▲ поддержать её");
      supp.onclick = () => onSupport(rev.position_id);
      actions.appendChild(supp);
    }
  }

  // УСТУПКА. Разбор нашёл, что черновик признаёт часть родителя. Форма уже
  // отметила это сама (onConcede) — здесь только видно, что именно попадёт в
  // граф: опубликованное не правится, и признание за автора без его ведома
  // было бы хуже, чем потерянное.
  if (rev.concedes && onConcede) {
    hint.appendChild(el("div", "section-title", "ты признаёшь часть довода"));
    hint.appendChild(el("b", null, "«" + rev.concedes + "»"));
    hint.appendChild(el("div", "muted",
      "Отмечено в форме: ответ выйдет с меткой «признаёт». Не признаёшь — сними галочку."));
    onConcede(rev.concedes);
  }

  // ценность — одной тихой строкой: отмечена в форме, можно сменить
  if (rev.value && onValue) {
    onValue(rev.value, rev.value_phrase || "");
    const v = el("div", "muted");
    v.textContent = "опирается на ценность «" + rev.value_name + "»"
      + (rev.value_phrase ? " — " + rev.value_phrase : "")
      + " · отмечено в форме, можно сменить или снять";
    hint.appendChild(v);
  }

  // two contributions glued together: editable split preview — the parts are
  // the author's own words, each will be its own node under its own rubric
  if (rev.split && onSplit) {
    hint.appendChild(el("div", "section-title", "похоже, здесь два вклада"));
    const parts = [];
    for (const p of rev.split) {
      const line = el("div");
      line.appendChild(el("span", "rel " + (p.type === "question" ? "question" : p.type),
                          TYPE_LABEL[p.type] || p.type));
      const pta = el("textarea");
      pta.value = p.text; pta.rows = 2;
      line.appendChild(pta);
      hint.appendChild(line);
      parts.push({ type: p.type, ta: pta });
    }
    hint.appendChild(el("div", "muted",
      "каждая часть станет отдельным узлом и получит оценку по своей рубрике"));
    const both = el("button", "mini", "опубликовать оба");
    both.onclick = () => {
      const out = parts.map((p) => ({ type: p.type, text: p.ta.value.trim() }))
                       .filter((p) => p.text);
      if (out.length < 2) { toast("обе части должны быть непустыми"); return; }
      hint.style.display = "none";
      onSplit(out);
    };
    actions.appendChild(both);
  }

  // МЕСТО. Не «ты не прав», а «кажется, это про другое» — и уйти туда можно
  // одним нажатием, вместе с уже написанным черновиком.
  if (rev.placement === "elsewhere" && rev.place_id) {
    hint.appendChild(el("div", "section-title", "кажется, это к другой проблеме"));
    hint.appendChild(el("b", null, rev.place_title || ("#" + rev.place_id)));
    if (rev.place_note) hint.appendChild(el("div", "muted", rev.place_note));
    const go = el("button", "mini", "открыть ту проблему");
    go.onclick = () => {
      hint.style.display = "none";
      ROOT.set(rev.place_id, rev.place_id);
      openTopic(rev.place_id).catch(() => selectNode(rev.place_id));
    };
    actions.appendChild(go);
  } else if (rev.placement === "own_problem") {
    hint.appendChild(el("div", "section-title", "это тянет на отдельную проблему"));
    if (rev.place_note) hint.appendChild(el("div", "muted", rev.place_note));
    const nt = el("button", "mini", "завести отдельной проблемой");
    nt.onclick = () => { hint.style.display = "none"; newTopicForm(getText && getText()); };
    actions.appendChild(nt);
  } else if (rev.placement === "cause" && onCause) {
    // ПРИЧИНА. Ответ соглашается, что вред есть, и называет ему причину
    // уровнем выше — это не «за» и не «против», а связь «Б порождает А».
    // Уровень нигде не хранится: он читается из таких связей. Ответ
    // публикуется как обычно и становится ОБОСНОВАНИЕМ связи — спор о ней
    // идёт под ним. Прежде чем заводить новую проблему, предлагаем сойтись
    // к уже существующей: иначе у одной первопричины будет 50 копий.
    hint.appendChild(el("div", "section-title", "ты называешь причину этой проблемы"));
    if (rev.place_note) hint.appendChild(el("div", "muted", rev.place_note));
    // Памятка схемы «от причины к следствию» — постоянная, без модели: связь
    // «порождает» потом будут оспаривать ровно этими вопросами.
    const cq = el("div", "muted");
    cq.textContent = "перед тем как связать, проверь: не совпадение ли это? "
      + "нет ли третьей причины, которая порождает обе? не наоборот ли — "
      + "не эта проблема порождает ту?";
    hint.appendChild(cq);
    const cbox = el("div", "causebox");
    let pick = null;                                  // id существующей или null = новая
    const rows = [];
    const mark = () => rows.forEach(({ row, id }) => row.classList.toggle("on", id === pick));
    for (const m of (rev.cause_matches || [])) {
      const row = el("div", "d");
      row.appendChild(el("span", "t", m.title));
      row.appendChild(el("span", "m", "   · уже есть — связать с ней"));
      row.onclick = () => { pick = m.id; mark(); };
      rows.push({ row, id: m.id });
      cbox.appendChild(row);
    }
    const nrow = el("div", "d");
    nrow.appendChild(el("span", "t", "новая проблема: "));
    const tIn = el("input");
    tIn.type = "text"; tIn.value = rev.cause_title || "";
    tIn.placeholder = "заголовок проблемы-причины";
    tIn.onfocus = () => { pick = null; mark(); };
    nrow.appendChild(tIn);
    nrow.onclick = () => { pick = null; mark(); };
    rows.push({ row: nrow, id: null });
    cbox.appendChild(nrow);
    mark();
    hint.appendChild(cbox);
    // Одна мысль — один узел. Новая причина: текст из поля выше становится
    // ПОСТАНОВКОЙ проблемы Б, ответа под А не остаётся — иначе одна запись
    // висела в двух местах. Схождение: у Б свой текст, а этот довод «А
    // порождается Б» — вклад в обсуждение А, он публикуется ответом здесь.
    const note = el("div", "muted");
    const syncNote = () => {
      note.textContent = pick != null
        ? "твой текст опубликуется ответом здесь как обоснование связи; у той "
          + "проблемы появится «следствие», у этой — «причина»"
        : "твой текст станет ПОСТАНОВКОЙ новой проблемы — одной записью, без "
          + "ответа здесь. Если он написан как ответ («согласен, но…»), перепиши "
          + "его как описание вреда: кто страдает и в чём";
    };
    for (const r of rows) r.row.addEventListener("click", syncNote);
    tIn.addEventListener("focus", syncNote);
    syncNote();
    hint.appendChild(note);
    const link = el("button", "mini", "опубликовать и связать как причину");
    link.onclick = () => {
      const title = tIn.value.trim();
      if (pick == null && !title) { toast("дай заголовок проблеме-причине"); tIn.focus(); return; }
      hint.style.display = "none";
      onCause(pick != null ? { cause_id: pick } : { title });
    };
    actions.appendChild(link);
  }

  if (rev.quality_note) {
    const q = el("div");
    q.appendChild(el("b", null, "как усилить: "));
    q.appendChild(document.createTextNode(rev.quality_note));
    hint.appendChild(q);
    hint.appendChild(el("div", "muted", "поправь текст и нажми «отправить» ещё раз — компаньон перечитает"));
  }

  // Вид рассуждения (vault: decisions/2026-09-15-argument-schemes): вопрос
  // компаньона ниже взят из проверочных вопросов этого вида, а не придуман с
  // нуля. Подпись объясняет, откуда вопрос, — без неё он читается придиркой.
  if (rev.scheme_name && rev.think) {
    const s = el("div", "muted");
    s.textContent = "вид рассуждения: «" + rev.scheme_name + "» — вопрос компаньона "
      + "ниже из тех, на которых такие доводы обычно ломаются";
    hint.appendChild(s);
  }

  const anyway = el("button", "mini", "отправить как есть");
  anyway.onclick = () => { hint.style.display = "none"; onSend(); };
  const cancel = el("button", "mini", "отмена");
  cancel.onclick = () => { hint.style.display = "none"; };
  actions.append(anyway, cancel);
  hint.appendChild(actions);

  // РАЗГОВОР. Открывается вопросом компаньона, если он его задал; иначе автор
  // начинает сам. Ниже кнопок — чтобы «отправить как есть» оставалось на виду
  // и разговор не выглядел обязательным этапом.
  // Разговор НЕ начинается заново на каждом «отправить». Раньше панель
  // перерисовывалась, companionThread получал пустую историю, и компаньон
  // заходил на новый круг замечаний, не помня, что уже разобрано с автором.
  // История лежит в черновике и поднимается вместе с ним.
  if (getText) {
    const kept = (draftRead(connectTo) || {}).history || [];
    companionThread(hint, {
      getText, setText, connectTo,
      restore: kept,
      // вступительный вопрос — только когда говорить ещё не начинали
      opening: kept.length ? null : (rev.think || null),
    });
  }
}

// ---- ответ на фрагмент: выделение текста → типизированное действие с якорем
// Глаголы действия, а метка связи потом называется иначе: возразил → «против».
// Раньше здесь стояло «Опровергнуть» — оно обещало больше, чем требует
// механика: refute это «спорю с этим доводом», а опровергнуть значит доказать
// ложность. И это было самое боевое слово в интерфейсе, выбиваясь из ряда
// «Уточнить / Поддержать / Спросить». «Оспорить» взять нельзя — им называется
// действие над ПОЗИЦИЕЙ (встречная позиция, другая механика).
const FRAG_ACTIONS = [
  ["refute", "Возразить"], ["undercut", "Показать, что не доказывает"],
  ["qualify", "Уточнить"],
  ["support", "Поддержать"], ["question", "Спросить"],
];
let fragPopEl = null;
function killFragPop() { if (fragPopEl) { fragPopEl.remove(); fragPopEl = null; } }
addEventListener("scroll", killFragPop, true);
addEventListener("mousedown", (e) => {
  if (fragPopEl && !fragPopEl.contains(e.target)) killFragPop();
});

function attachFragmentSelection(textEl) {
  textEl.classList.add("selectable");
  textEl.addEventListener("mouseup", () => {
    setTimeout(() => {                       // дать выделению устояться
      const sel = window.getSelection();
      if (!sel || sel.isCollapsed || !sel.rangeCount) return;
      const range = sel.getRangeAt(0);
      if (!textEl.contains(range.commonAncestorContainer)) return;
      const quote = sel.toString();
      if (!quote.trim()) return;
      // смещение начала выделения в тексте узла (устойчиво к нескольким текст-узлам)
      const pre = range.cloneRange();
      pre.selectNodeContents(textEl);
      pre.setEnd(range.startContainer, range.startOffset);
      const start = pre.toString().length;
      showFragPop(range, { start, end: start + quote.length, quote });
    }, 0);
  });
}

function showFragPop(range, anchor) {
  killFragPop();
  if (!replyHandle) return;
  const pop = el("div", "fragpop");
  for (const [type, label] of FRAG_ACTIONS) {
    const b = el("button", "mini", label);
    b.onclick = () => {
      window.getSelection().removeAllRanges();
      killFragPop();
      replyHandle.setFragment(anchor, type);
    };
    pop.appendChild(b);
  }
  document.body.appendChild(pop);
  const r = range.getBoundingClientRect();
  pop.style.top = Math.max(6, r.top - pop.offsetHeight - 8) + "px";
  let left = r.left + r.width / 2 - pop.offsetWidth / 2;
  pop.style.left = Math.max(6, Math.min(left, innerWidth - pop.offsetWidth - 6)) + "px";
  fragPopEl = pop;
}

// маркеры оспоренных участков «на полях»: один маркер с ЧИСЛОМ у каждого участка,
// к которому есть ответы (не подсветка всего текста). Позиция берётся из реального
// прямоугольника участка в тексте — маркер стоит на строке своего фрагмента.
function renderMarginMarkers(wrap, textEl, replies) {
  const textNode = textEl.firstChild;
  if (!textNode || textNode.nodeType !== 3) return;
  const len = textNode.textContent.length;
  const groups = new Map();
  for (const a of replies) {
    if (a.anchor_start == null) continue;
    const key = a.anchor_start + ":" + a.anchor_end;
    (groups.get(key) || groups.set(key, { s: a.anchor_start, e: a.anchor_end, items: [] }).get(key))
      .items.push(a);
  }
  const wrect = wrap.getBoundingClientRect();
  for (const g of groups.values()) {
    let top;
    try {
      const r = document.createRange();
      r.setStart(textNode, Math.min(g.s, len));
      r.setEnd(textNode, Math.min(g.e, len));
      top = r.getBoundingClientRect().top - wrect.top;
    } catch (_) { continue; }
    const stale = g.items.every(x => x.stale);
    const mk = el("span", "margmark m-" + g.items[0].type + (stale ? " stale" : ""),
      String(g.items.length));
    mk.style.top = Math.max(0, top) + "px";
    mk.title = g.items.map(x => relLabel(x.type) + ": «" + shortLabel(x.anchor_quote, 60) + "»"
      + (x.author ? " — " + x.author : "") + (x.stale ? " · участок изменился" : "")).join("\n");
    mk.onclick = () => selectNode(g.items[0].source_id);
    wrap.appendChild(mk);
  }
}

// принадлежность узла нескольким проблемам (домашняя + принесённые)
function belongingBar(belongings) {
  const bar = el("div", "belong");
  for (const b of belongings) {
    const t = b.topic_title || shortLabel(b.topic_text, 40) || ("#" + b.topic_root_id);
    const chip = el("span", "b" + (b.is_home ? " home" : ""), (b.is_home ? "◆ " : "") + t);
    if (!b.is_home) chip.onclick = () => selectNode(b.topic_root_id);
    chip.title = b.is_home ? "домашняя проблема"
      : "принесён сюда" + (b.placed_by ? " · " + b.placed_by : "");
    bar.appendChild(chip);
  }
  return bar;
}

// ---- доска обсуждения: решения на столе и открытые вопросы
//
// Предложения и вопросы лежали в графе с самого начала, но увидеть их можно
// было только развернув нужную ветку дерева — то есть уже зная, что они там
// есть. Накопитель на странице проблемы отвечает на другой вопрос: что УЖЕ
// пробовали в реальности и чем кончилось. Это факты; предложение — то, что
// ещё только предлагают сделать, и места у него не было вовсе.
//
// Порядок — по времени, как приходит с сервера. Предложения по одной проблеме
// взаимоисключающи, и сортировка по PoI читалась бы как «вот правильное»
// (то же решение, что и для детей узла).
function boardCards(rootId, rootKind) {
  // Контейнер возвращается СРАЗУ и наполняется по приходе ответа: иначе форма
  // ответа и позиции ниже ждали бы этот запрос, а он тут не главный.
  const wrap = el("div");
  api(`/api/topics/${rootId}/board`)
    .then((data) => fillBoard(wrap, rootId, rootKind, data))
    .catch(() => { /* доска не пришла — панель просто без неё */ });
  return wrap;
}

function fillBoard(frag, rootId, rootKind, data) {
  const props = data.proposals || [];
  const qs = data.questions || [];
  const openQs = qs.filter((q) => !q.reply_count);
  const answered = qs.filter((q) => q.reply_count);

  // строка доски: текст, автор, PoI и счётчики. Клик ведёт к самому узлу —
  // спорить с предложением надо там, где оно живёт, а не в списке.
  const row = (n, extra) => {
    const b = el("div", "b" + (n.retracted_at ? " retr" : ""));
    b.appendChild(el("div", "b-txt", shortLabel(n.text, 240)));
    const m = el("div", "b-meta");
    m.appendChild(el("span", null, n.author || "—"));
    const unscored = n.author_is_service || n.author_is_seed;
    if (n.poi_score != null) m.appendChild(el("span", null, "PoI " + n.poi_score));
    else if (!unscored) m.appendChild(el("span", null, "PoI …"));
    for (const x of extra(n)) m.appendChild(el("span", null, x));
    if (n.retracted_at) {
      const t = el("span", "rtag", "отозвано");
      t.title = n.retract_note || "Автор больше не настаивает";
      m.appendChild(t);
    }
    b.appendChild(m);
    b.onclick = () => { ROOT.set(n.id, rootId); selectNode(n.id); };
    return b;
  };

  // кнопка «предложить решение» / «задать вопрос»: форма ответа на этой же
  // странице, тип в ней уже выбран — иначе совет «ответь предложением»
  // требует найти форму и вспомнить, какой пункт в списке нужен
  const jump = (type, label) => {
    const btn = el("button", "mini", label);
    btn.onclick = () => {
      if (!requireAuth()) return;
      if (replyHandle && replyHandle.parentId === rootId && replyHandle.setType)
        replyHandle.setType(type);
    };
    return btn;
  };

  const pc = el("div", "card board");
  pc.appendChild(el("div", "section-title",
    (rootKind === "problem" ? "Решения на столе · " : "Предложения · ") + props.length));
  appendHint(pc, rootKind === "problem"
    ? "Это <b>предложения</b> — что сделать. Не путать с накопителем выше: там "
      + "то, что уже пробовали, с исходом. Здесь то, что ещё только предлагают, "
      + "и по каждому можно спорить внутри."
    : "Предложения, высказанные в этом обсуждении. По каждому можно спорить "
      + "внутри — клик открывает сам довод.");
  if (!props.length)
    pc.appendChild(el("div", "muted", rootKind === "problem"
      ? "пока ни одного — и это приглашение, а не недоделка"
      : "пока ни одного"));
  for (const n of props)
    pc.appendChild(row(n, (x) => [`за ${x.agree} · против ${x.disagree}`,
                                  `возражений: ${x.reply_count}`]));
  const pa = el("div", "actions");
  pa.appendChild(jump("proposal", "+ предложить решение"));
  pc.appendChild(pa);
  frag.appendChild(pc);

  const qc = el("div", "card board");
  qc.appendChild(el("div", "section-title", "Открытые вопросы · " + openQs.length));
  appendHint(qc, "Вопросы, на которые в обсуждении ещё <b>никто не ответил</b>. "
    + "Ответить — значит написать ответ этому вопросу: клик открывает его.");
  if (!openQs.length)
    qc.appendChild(el("div", "muted", "без ответа не осталось ни одного"));
  for (const n of openQs) qc.appendChild(row(n, () => []));
  if (answered.length) {
    const more = el("button", "mini", `показать отвечённые (${answered.length})`);
    const box = el("div");
    box.style.display = "none";
    for (const n of answered)
      box.appendChild(row(n, (x) => [`ответов: ${x.reply_count}`]));
    more.onclick = () => {
      const open = box.style.display === "none";
      box.style.display = open ? "" : "none";
      more.textContent = open ? "скрыть отвечённые"
                              : `показать отвечённые (${answered.length})`;
    };
    const qa = el("div", "actions");
    qa.appendChild(more);
    qc.appendChild(qa);
    qc.appendChild(box);
  }
  const qact = el("div", "actions");
  qact.appendChild(jump("question", "+ задать вопрос"));
  qc.appendChild(qact);
  frag.appendChild(qc);
}


// ---- страница проблемы: причины, масштаб, реестр решений, атрибуции
const OC_LABEL = { success: "успех", partial: "частично", mixed: "смешанно",
                   failure: "провал", unclear: "неясно" };
async function problemCard(nodeId) {
  const card = el("div", "card");
  card.appendChild(el("div", "section-title", "Проблема · состояние"));
  const body = el("div", "muted", "загрузка…");
  card.appendChild(body);
  let p;
  try { p = await api(`/api/problems/${nodeId}`); }
  catch (e) { body.textContent = "не загрузилось: " + e.message; return card; }
  body.className = ""; body.innerHTML = "";

  if (p.causes) {
    body.appendChild(el("div", "section-title", "Причины и составные части"));
    body.appendChild(el("div", "causes", p.causes));
  }
  // Связи с другими проблемами: «Б порождает А». Уровень нигде не хранится —
  // он читается отсюда: причины — выше, следствия — ниже, а цепочка ведёт к
  // проблемам, у которых причин пока не названо. Каждая связь — спорное
  // утверждение с обоснованием-узлом; «оспорено» считается по рёбрам под ним.
  const links = p.links || { causes: [], effects: [] };
  if (links.causes.length || links.effects.length) {
    const lb = el("div", "prob-block");
    if (links.causes.length) {
      lb.appendChild(el("div", "section-title", "Причины · проблемы уровнем выше"));
      for (const l of links.causes) lb.appendChild(problemLinkRow(l, "cause"));
    }
    if (links.effects.length) {
      const t = el("div", "section-title", "Следствия · что эта проблема порождает");
      if (links.causes.length) t.style.marginTop = "10px";
      lb.appendChild(t);
      for (const l of links.effects) lb.appendChild(problemLinkRow(l, "effect"));
    }
    const chain = (p.chain || []).filter((c) => c.depth > 1 || c.cycle);
    if (chain.length) {
      const ch = el("div", "chain");
      ch.appendChild(el("span", "m", "цепочка выше: "));
      for (const c of chain) {
        const a = el("span", "t" + (c.cycle ? " cyc" : ""),
                     "↑".repeat(c.depth) + " " + (c.title || shortLabel(c.text, 50))
                     + (c.cycle ? " (замкнутый круг)" : ""));
        a.onclick = () => selectNode(c.id);
        ch.appendChild(a);
      }
      lb.appendChild(ch);
    }
    body.appendChild(lb);
  }
  // Масштаб — где встречается и в каких объёмах. Строками: один регион, одна
  // цифра, свой источник у каждой. Старый сплошной абзац (scale_note/url)
  // показываем только пока строк нет — данные, записанные до разделения.
  const rows = p.scale || [];
  if (rows.length || p.scale_note || p.scale_url) {
    const s = el("div", "prob-block");
    s.appendChild(el("div", "section-title", "Масштаб · где и в каких объёмах"));
    for (const r of rows) s.appendChild(scaleRow(r, nodeId));
    if (!rows.length) {
      if (p.scale_note) s.appendChild(el("div", "causes", p.scale_note));
      if (p.scale_url) {
        const src = el("div", "scale-src");
        if (p.scale_excerpt) src.appendChild(el("span", "q", "«" + p.scale_excerpt + "» "));
        const a = el("a", null, "источник ↗");
        a.href = p.scale_url; a.target = "_blank"; a.rel = "noopener";
        src.appendChild(a);
        s.appendChild(src);
      }
    }
    body.appendChild(s);
  }

  const reg = el("div", "prob-block");
  reg.appendChild(el("div", "section-title",
    "Накопитель решений · " + (p.interventions_total || 0)));
  if (p.outcomes && Object.keys(p.outcomes).length) {
    const sum = el("div", "outsum");
    for (const [k, n] of Object.entries(p.outcomes))
      sum.appendChild(el("span", "oc " + k, (OC_LABEL[k] || k) + " · " + n));
    reg.appendChild(sum);
  }
  const ivs = p.interventions || [];
  if (!ivs.length) reg.appendChild(el("div", "muted",
    "пока нет записей — внеси первую попытку: где пробовали и чем кончилось"));
  for (const iv of ivs) reg.appendChild(await interventionCard(iv));
  body.appendChild(reg);

  // Чего не хватает — читается ПОСЛЕ реестра, потому что это про дыру именно в
  // нём: что ни одна из перечисленных попыток не закрыла и что не переносится.
  // Пустой блок показываем тоже — он говорит, куда писать следующий довод.
  const g = el("div", "prob-block");
  g.appendChild(el("div", "section-title", "Чего не хватает"));
  g.appendChild(p.gap
    ? el("div", "causes", p.gap)
    : el("div", "muted", "не описано — а это главное место для довода: "
        + "что во всех попытках выше осталось незакрытым"));
  body.appendChild(g);

  // авторские действия: заполнить состояние и накопитель (иначе страница пустая)
  if (ME) {
    const foot = el("div", "actions"); foot.style.marginTop = "12px";
    const editBtn = el("button", "mini", "✎ править состояние");
    editBtn.onclick = () => {
      const f = problemEditForm(nodeId, p, () => selectNode(nodeId));
      if (f) { card.appendChild(f); editBtn.disabled = true; }
    };
    const ivBtn = el("button", "mini", "+ вмешательство");
    ivBtn.onclick = () => interventionForm(nodeId, card);
    const scBtn = el("button", "mini", "+ строка масштаба");
    scBtn.onclick = () => scaleForm(nodeId, card);
    foot.append(editBtn, scBtn, ivBtn);
    card.appendChild(foot);
  }
  return card;
}

// связь «Б порождает А» одной строкой: та проблема, кто заявил, обоснование
// (узел, под которым идёт спор о связи) и сводка этого спора
function problemLinkRow(l, side) {
  const row = el("div", "plink");
  const head = el("div", "pl-head");
  const t = el("span", "pl-title", (side === "cause" ? "↑ " : "↓ ")
               + (l.problem_title || shortLabel(l.problem_text, 60)));
  t.onclick = () => selectNode(side === "cause" ? l.cause_id : l.effect_id);
  head.appendChild(t);
  // Статус связи — по силе обоснования в споре, а не по голому числу
  // возражений: раньше «оспорено · 2» стояло и тогда, когда оба уже отбиты.
  const dx = l.dialectic;
  if (dx && dx.verdict !== "untested") {
    const open = (dx.unanswered || []).length;
    const word = dx.verdict === "holds" ? "держится"
      : (dx.verdict === "weakened" ? "ослаблена" : "сильно ослаблена")
        + (open ? " · без ответа " + open : "");
    const s = el("span", "dstat " + dx.verdict, "связь " + word);
    s.title = DIALECTIC_HINT;
    head.appendChild(s);
  } else if (l.disputed) head.appendChild(el("span", "oc failure", "оспорено · " + l.disputed));
  else if (l.supported) head.appendChild(el("span", "oc success", "поддержано · " + l.supported));
  row.appendChild(head);
  const meta = el("div", "pl-meta");
  meta.append("связал: " + (l.author || "—"));
  // обоснование = сама постановка причины (одна запись) — тогда ссылка на
  // «обоснование» вела бы туда же, куда и заголовок; показываем только текст
  const selfJust = l.node_id === l.cause_id;
  if (l.node_id && !selfJust) {
    meta.append(" · ");
    const j = el("a", null, l.node_retracted_at ? "обоснование (отозвано)" : "обоснование");
    j.href = "#"; j.onclick = (e) => { e.preventDefault(); selectNode(l.node_id); };
    meta.appendChild(j);
  }
  if (l.node_text) meta.appendChild(el("span", "q", " «" + shortLabel(l.node_text, 110) + "»"));
  row.appendChild(meta);
  return row;
}

// строка масштаба: регион — цифра — источник. Цифра не правится: её снимают и
// вносят заново, иначе число меняется под уже написанными доводами.
function scaleRow(r, nodeId) {
  const row = el("div", "scale-row");
  const head = el("div", "sr-head");
  head.append(el("span", "sr-region", r.region), el("span", "sr-figure", r.figure));
  if (ME) {
    const x = el("span", "sr-x", "×");
    x.title = "снять строку";
    x.onclick = async () => {
      try {
        await api(`/api/scale/${r.id}`, { method: "DELETE" });
        toast("строка снята"); selectNode(nodeId);
      } catch (e) { toast("ошибка: " + e.message); }
    };
    head.appendChild(x);
  }
  row.appendChild(head);
  if (r.source_url || r.source_excerpt) {
    const src = el("div", "scale-src");
    if (r.source_excerpt) src.appendChild(el("span", "q", "«" + r.source_excerpt + "» "));
    if (r.source_url) {
      const a = el("a", null, "источник ↗");
      a.href = r.source_url; a.target = "_blank"; a.rel = "noopener";
      src.appendChild(a);
      if (r.retrieved_at) src.appendChild(el("span", null,
        " · проверено " + new Date(r.retrieved_at).toLocaleDateString("ru-RU")));
    }
    row.appendChild(src);
  }
  return row;
}

function scaleForm(nodeId, mount) {
  if (!requireAuth()) return;
  const f = el("div", "pedit prob-block");
  f.appendChild(el("div", "section-title", "Добавить строку масштаба"));
  const two = el("div", "two");
  const region = el("input"); region.placeholder = "Где — страна, город, площадка";
  const figure = el("input"); figure.placeholder = "Сколько — цифра словами";
  two.append(region, figure);
  const url = el("input"); url.placeholder = "Ссылка на источник цифры";
  const exc = el("textarea"); exc.placeholder = "Выдержка из источника (текст)";
  f.append(two, url, exc);
  const act = el("div", "actions");
  const save = el("button", "primary mini", "внести");
  save.onclick = async () => {
    if (!region.value.trim() || !figure.value.trim())
      return toast("нужны и регион, и цифра");
    try {
      await api(`/api/problems/${nodeId}/scale`, {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({
          region: region.value.trim(), figure: figure.value.trim(),
          source_url: url.value.trim() || null,
          source_excerpt: exc.value.trim() || null,
        }),
      });
      toast("строка внесена"); selectNode(nodeId);
    } catch (e) { toast("ошибка: " + e.message); }
  };
  act.appendChild(save); f.appendChild(act);
  mount.appendChild(f);
}

// правка состояния проблемы: причины + масштаб (данные извне с выдержкой)
function problemEditForm(nodeId, p, rerender) {
  if (!requireAuth()) return null;
  const f = el("div", "pedit prob-block");
  f.appendChild(el("div", "section-title", "Состояние проблемы"));
  const causes = el("textarea"); causes.placeholder = "Причины и составные части";
  causes.value = p.causes || "";
  const gap = el("textarea");
  gap.placeholder = "Чего не хватает: что ни одна из попыток не закрыла и что не переносится";
  gap.value = p.gap || "";
  f.append(causes, gap);
  const act = el("div", "actions");
  const save = el("button", "primary mini", "сохранить состояние");
  save.onclick = async () => {
    try {
      await api(`/api/problems/${nodeId}`, {
        method: "PUT", headers: { "content-type": "application/json" },
        body: JSON.stringify({
          causes: causes.value.trim() || null,
          gap: gap.value.trim() || null,
          // масштаб живёт строками (см. scaleForm) — здесь его больше нет,
          // но старые поля не затираем: шлём то, что уже лежит в базе
          scale_note: p.scale_note || null, scale_url: p.scale_url || null,
          scale_excerpt: p.scale_excerpt || null,
        }),
      });
      toast("состояние сохранено"); rerender();
    } catch (e) { toast("ошибка: " + e.message); }
  };
  act.appendChild(save); f.appendChild(act);
  return f;
}

// запись в накопитель решений — факт (провал регистрируется наравне с успехом)
function interventionForm(nodeId, mount) {
  if (!requireAuth()) return;
  const f = el("div", "pedit prob-block");
  f.appendChild(el("div", "section-title", "Внести вмешательство"));
  const what = el("input"); what.placeholder = "Что пробовали (вмешательство)";
  const two1 = el("div", "two");
  const actor = el("input"); actor.placeholder = "Кто";
  const geo = el("input"); geo.placeholder = "Где — страна/регион (необязательно)";
  two1.append(actor, geo);
  const two2 = el("div", "two");
  const when = el("input"); when.placeholder = "Когда (год/период)";
  const oc = el("select");
  for (const [v, l] of Object.entries(OC_LABEL)) oc.appendChild(new Option(l, v));
  two2.append(when, oc);
  const outcome = el("textarea"); outcome.placeholder = "Что вышло (исход)";
  // условия — главный проверочный вопрос схемы «по прецеденту»: получится ли
  // так же в другом месте, зависит от того, похожи ли условия
  const cond = el("input");
  cond.placeholder = "От каких условий зависело — чтобы другие поняли, похожи ли их условия";
  const url = el("input"); url.placeholder = "Ссылка на источник (необязательно)";
  const exc = el("textarea"); exc.placeholder = "Выдержка из источника (текст)";
  f.append(what, two1, two2, outcome, cond, url, exc);
  const act = el("div", "actions");
  const save = el("button", "primary mini", "внести в реестр");
  save.onclick = async () => {
    if (!what.value.trim()) { toast("опиши, что пробовали"); return; }
    try {
      await api(`/api/problems/${nodeId}/interventions`, {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({
          what: what.value.trim(), actor: actor.value.trim() || null,
          geo: geo.value.trim() || null, when_text: when.value.trim() || null,
          outcome: outcome.value.trim() || null, outcome_kind: oc.value,
          conditions: cond.value.trim() || null,
          source_url: url.value.trim() || null, source_excerpt: exc.value.trim() || null,
        }),
      });
      toast("вмешательство внесено"); selectNode(nodeId);
    } catch (e) { toast("ошибка: " + e.message); }
  };
  act.appendChild(save); f.appendChild(act);
  mount.appendChild(f); what.focus();
}

async function interventionCard(iv) {
  const box = el("div", "iv");
  const head = el("div", "iv-head");
  head.appendChild(el("span", "iv-what", iv.what));
  head.appendChild(el("span", "oc " + iv.outcome_kind,
    OC_LABEL[iv.outcome_kind] || iv.outcome_kind));
  box.appendChild(head);
  const meta = [];
  if (iv.geo) meta.push("📍 " + iv.geo);
  if (iv.when_text) meta.push("🕐 " + iv.when_text);
  if (iv.actor) meta.push("👤 " + iv.actor);
  if (meta.length) box.appendChild(el("div", "iv-meta", meta.join("    ")));
  if (iv.outcome) box.appendChild(el("div", "iv-out", iv.outcome));
  if (iv.conditions) box.appendChild(el("div", "iv-cond", "условия: " + iv.conditions));
  if (iv.source_url) {
    const src = el("div", "scale-src");
    if (iv.source_excerpt) src.appendChild(el("span", "q", "«" + iv.source_excerpt + "» "));
    const a = el("a", null, "источник ↗");
    a.href = iv.source_url; a.target = "_blank"; a.rel = "noopener";
    src.appendChild(a);
    box.appendChild(src);
  }
  // атрибуции об этом факте — оспоримые причинные утверждения, живут в графе
  let det;
  try { det = await api(`/api/interventions/${iv.id}`); } catch (_) { det = null; }
  for (const at of (det && det.attributions) || []) {
    const a = el("div", "attr");
    a.appendChild(el("div", null, at.text));
    a.appendChild(el("div", "disp", "атрибуция · " + (at.author || "—") +
      "   · оспорено: " + (at.reply_count || 0)));
    a.style.cursor = "pointer";
    a.onclick = () => selectNode(at.id);    // перейти к спору по атрибуции
    box.appendChild(a);
  }
  const act = el("div", "actions");
  const add = el("button", "mini", "+ атрибуция");
  add.onclick = () => attributionForm(iv.id, box);
  act.appendChild(add);
  box.appendChild(act);
  return box;
}

function attributionForm(interventionId, mount) {
  if (!requireAuth()) return;
  const f = el("div", "prob-block");
  const ta = el("textarea");
  ta.placeholder = "Сработало благодаря… / переносимо на… (причинное утверждение)";
  f.appendChild(ta);
  const act = el("div", "actions");
  const send = el("button", "primary mini", "заявить");
  send.onclick = async () => {
    const text = ta.value.trim();
    if (!text) { toast("напиши причинное утверждение"); ta.focus(); return; }
    try {
      await api(`/api/interventions/${interventionId}/attribution`, {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ text }),
      });
      toast("атрибуция добавлена — по ней теперь можно спорить");
      if (selectedId != null) selectNode(selectedId);
    } catch (e) { toast("ошибка: " + e.message); }
  };
  act.appendChild(send);
  f.appendChild(act);
  mount.appendChild(f);
  ta.focus();
}

function replyForm(parentId) {
  const card = el("div", "card");
  card.appendChild(el("div", "section-title", "Ответить"));
  appendHint(card, "Выбери, как твой довод относится к этому доводу " +
    "(за / против / уточнение / вопрос / предложение / разбор), и напиши его. " +
    "Перед отправкой " +
    "ИИ-компаньон разберёт черновик — с ним можно спорить и переспрашивать. " +
    "<b>После публикации текст изменить нельзя</b>: на нём строят ответы.");
  const ta = el("textarea");
  ta.placeholder = "Твой довод…";
  // чип якоря: показывает, на какой участок отвечаем (ответ на фрагмент)
  const chip = el("div", "anchor-chip");
  chip.style.display = "none";
  let anchor = null;              // {start, end, quote} либо null (ответ на весь узел)
  const clearAnchor = () => { anchor = null; chip.style.display = "none"; };
  card.appendChild(chip);
  card.appendChild(ta);
  const act = el("div", "actions");
  const typeSel = el("select");
  // «не доказывает» (undercut) целится в участок — доступно только с якорем.
  // Слово выбрано Alex 10.09 вместо «подрыв»: тот был калькой с undercut и
  // по-русски читался как диверсия. Здесь спорят не с выводом, а с опорой:
  // вывод может быть верен, но ЭТОТ кусок его не доказывает.
  for (const [v, l] of [["support", "за"], ["refute", "против"], ["qualify", "уточнение"],
                        ["restate", "пересказ"],
                        ["undercut", "не доказывает этот участок"], ["question", "вопрос"],
                        ["proposal", "предложение"], ["exploration", "разбор"]])
    typeSel.appendChild(new Option(l, v));
  const send = el("button", "primary", "отправить");
  const hint = el("div");                       // the navigator's suggestion box
  hint.style.display = "none";

  // УСТУПКА: «признаю часть этого довода». Ставит автор или разбор (с цитатой
  // того, что признано). Не для «за» — «за» и так согласие.
  const concedeOpt = el("label", "concede-opt");
  const concedeBox = el("input");
  concedeBox.type = "checkbox";
  const concedeQ = el("span", "q");
  const concedeX = el("span", "x", "✕");
  concedeX.title = "убрать цитату — признание останется без уточнения";
  let concedeQuote = null;
  const setConcede = (quote) => {
    concedeBox.checked = true;
    concedeQuote = quote || null;
    concedeQ.textContent = quote ? "«" + shortLabel(quote, 90) + "»" : "";
    concedeX.style.display = quote ? "" : "none";
  };
  const clearConcede = () => {
    concedeBox.checked = false;
    concedeQuote = null;
    concedeQ.textContent = "";
    concedeX.style.display = "none";
  };
  concedeX.onclick = (e) => { e.preventDefault(); setConcede(null); };
  concedeBox.onchange = () => { if (!concedeBox.checked) clearConcede(); };
  concedeX.style.display = "none";
  concedeOpt.append(concedeBox, "признаю часть этого довода", concedeQ, concedeX);
  concedeOpt.title = CONCEDE_HINT;
  const syncConcede = () => {
    const off = typeSel.value === "support";
    concedeOpt.style.display = off ? "none" : "";
    if (off) clearConcede();
  };
  typeSel.addEventListener("change", syncConcede);

  // ЦЕННОСТЬ (vault: decisions/2026-09-15-values): на что опирается довод.
  // Ставит разбор черновика, автор видит здесь и может сменить или снять.
  const valueOpt = el("div", "value-opt");
  const valueSel = el("select");
  valueSel.appendChild(new Option("не указана", ""));
  const valuePh = el("span", "q");
  let valuePhrase = "", pendingValue = null;
  valuesList().then((list) => {
    for (const v of list) {
      const o = new Option(v.name, v.id);
      o.title = v.meaning;
      valueSel.appendChild(o);
    }
    if (pendingValue) valueSel.value = pendingValue;
  });
  const setValue = (id, phrase) => {
    pendingValue = id;
    valueSel.value = id;
    valuePhrase = phrase || "";
    valuePh.textContent = valuePhrase ? "«" + shortLabel(valuePhrase, 80) + "»" : "";
  };
  const clearValue = () => { pendingValue = null; valueSel.value = ""; valuePhrase = ""; valuePh.textContent = ""; };
  // формулировка ИИ относилась к его ценности — выбрал другую, она больше не про то
  valueSel.onchange = () => { pendingValue = valueSel.value || null; valuePhrase = ""; valuePh.textContent = ""; };
  valueOpt.append("на что опирается: ", valueSel, valuePh);
  valueOpt.title = "Ценность, на которую опирается довод. Предлагает ИИ-разбор, " +
    "решаешь ты; поменять можно и после публикации — это метка, а не текст.";

  // Черновик, переживший обновление страницы. Подставляется молча, но с
  // видимой пометкой: человек должен понимать, откуда в поле текст.
  const restored = draftRead(parentId);
  if (restored && (restored.text || (restored.history || []).length)) {
    ta.value = restored.text || "";
    if (restored.type) typeSel.value = restored.type;
    const bar = el("div", "muted");
    bar.style.fontSize = "12.5px";
    bar.style.margin = "6px 0";
    bar.append(`черновик восстановлен · ${draftAge(restored.ts)} `);
    const hist = restored.history || [];
    if (hist.length) {
      const back = el("button", "mini", `вернуть разговор с компаньоном (${hist.length})`);
      back.onclick = () => {
        hint.style.display = "";
        hint.innerHTML = "";
        hint.className = "card";
        hint.appendChild(el("div", "section-title", "ИИ-компаньон — разговор продолжается"));
        companionThread(hint, {
          getText: () => ta.value.trim(),
          setText: (t) => { ta.value = t; reviewedText = t.trim(); draftWrite(parentId, { text: t }); },
          connectTo: parentId, restore: hist,
        });
        back.remove();
      };
      bar.appendChild(back);
    }
    const fresh = el("button", "mini", "начать заново");
    fresh.onclick = () => {
      draftDrop(parentId);
      ta.value = "";
      hint.style.display = "none";
      bar.remove();
    };
    bar.appendChild(fresh);
    card.appendChild(bar);
  }

  // Пишем на каждый ввод: единственный момент, когда терять нечего, — это
  // когда ещё ничего не написано.
  let saveTimer = null;
  const rememberDraft = () => {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(
      () => draftWrite(parentId, { text: ta.value, type: typeSel.value }), 400);
  };
  ta.addEventListener("input", rememberDraft);
  typeSel.addEventListener("change", rememberDraft);

  // форма показывает якорь и, для «не доказывает», требует его
  const showAnchor = (a, type) => {
    anchor = a;
    chip.innerHTML = "";
    chip.append("в ответ на: «" + shortLabel(a.quote, 90) + "»");
    const x = el("span", "x", "✕"); x.title = "убрать привязку к участку";
    // без якоря «не доказывает» бессмысленно: оно целится в участок
    x.onclick = () => { clearAnchor(); if (typeSel.value === "undercut") typeSel.value = "refute"; };
    chip.appendChild(x);
    chip.style.display = "";
    if (type) typeSel.value = type;
    ta.focus();
    card.scrollIntoView({ behavior: "smooth", block: "nearest" });
  };
  // хэндл для всплывающего меню выделения и для кнопок доски («предложить
  // решение», «задать вопрос»): они выбирают тип прямо здесь, чтобы совет
  // «ответь предложением» не превращался в поиск формы и нужного пункта
  replyHandle = {
    parentId,
    setFragment: showAnchor,
    setType: (t) => {
      typeSel.value = t;
      rememberDraft();
      card.scrollIntoView({ behavior: "smooth", block: "center" });
      ta.focus();
    },
  };

  const doSend = async (text) => {
    if (typeSel.value === "undercut" && !anchor) {
      toast("«не доказывает» целится в участок — выдели фрагмент текста"); return;
    }
    if (!await confirmIrreversible()) return;
    try {
      const node = await api("/api/argument", {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({
          text, connect_to: parentId, edge_type: typeSel.value,
          anchor: anchor || undefined,
          concedes: concedeBox.checked && typeSel.value !== "support"
            ? { quote: concedeQuote || undefined } : undefined,
          value: valueSel.value ? { id: valueSel.value, phrase: valuePhrase || undefined } : undefined,
        }),
      });
      const hadValue = valueSel.value
        ? valueSel.options[valueSel.selectedIndex].text : "";
      clearAnchor();
      clearConcede();
      clearValue();
      draftDrop(parentId);            // опубликовано — хранить больше нечего
      toast("добавлено — PoI оценивается в фоне…" + (hadValue
        ? " · опирается на «" + hadValue + "», поменять можно в панели довода" : ""));
      expanded.add(parentId);
      await fetchChildren(parentId);   // refresh just this branch
      await loadTopics();              // reply counts on roots may change
      selectNode(parentId);
      return node;                     // нужен тому, кто свяжет его дальше
    } catch (e) { toast("ошибка: " + e.message); }
  };

  // Текст, который ИИ уже разобрал. Второй прогон того же текста стоит вызова
  // из гранта автора и не даёт ничего — а когда текст ЦЕЛИКОМ написан
  // компаньоном, разбор и вовсе превращается в проверку ИИ самого себя:
  // находится новое замечание, автор правит, круг повторяется. Разбирается
  // только то, что изменилось.
  let reviewedText = null;

  const runReview = async () => {
    const text = ta.value.trim();
    if (!text) { toast("напиши ответ"); ta.focus(); return; }
    if (!requireAuth()) return;
    if (text === reviewedText) { hint.style.display = "none"; await doSend(text); return; }
    send.disabled = true; send.textContent = "ИИ читает черновик…";
    const root = ROOT.get(parentId) ?? parentId;
    const rev = await reviewDraft({ text, connect_to: parentId, edge_type: typeSel.value });
    reviewedText = text;
    // ценность из разбора — в форму сразу, даже если больше заметок нет: автор
    // видит её в форме и в подсказке после публикации, сменить может в панели
    if (rev && rev.value) setValue(rev.value, rev.value_phrase);
    send.disabled = false; send.textContent = "отправить";
    // nothing to suggest (or the navigator is down) → publish silently
    if (!reviewHasNotes(rev)) { hint.style.display = "none"; await doSend(text); return; }

    renderReview(hint, rev, {
      root,
      // компаньону нужен ЖИВОЙ текст: автор правит черновик прямо во время
      // разговора, и следующий ход должен читать то, что в поле сейчас
      getText: () => ta.value.trim(),
      // формулировку писал тот же ИИ и с полным контекстом ветки — гонять её
      // через разбор значит спрашивать его же мнение о собственном тексте
      setText: (t) => { ta.value = t; reviewedText = t.trim(); draftWrite(parentId, { text: t }); },
      connectTo: parentId,
      // разбор нашёл уступку — отмечаем в форме, автор видит и может снять
      onConcede: (quote) => { if (typeSel.value !== "support") setConcede(quote); },
      onValue: (id, phrase) => setValue(id, phrase),
      onSend: async () => { await doSend(ta.value.trim()); },
      // advice on the card already applies to the suggested type — publish
      onSwitch: async (type) => {
        typeSel.value = type;
        await doSend(ta.value.trim());
      },
      onSupport: async (pid) => {
        hint.style.display = "none";
        ta.value = "";
        await positionVote(pid, root, "agree");
      },
      // ответ назвал причину: публикуем его как обычно, затем связываем
      // проблему-причину (существующую или новую) со следствием — этим
      // корнем. Если публикация сорвалась, связывать нечего.
      onCause: async (choice) => {
        const text = ta.value.trim();
        let payload;
        if (choice.cause_id != null) {
          // схождение: довод публикуется ответом здесь, связь ссылается на него
          const node = await doSend(text);
          if (!node) return;
          payload = { cause_id: choice.cause_id, node_id: node.id };
        } else {
          // новая причина: одна запись — корень проблемы Б, ответа здесь нет
          if (!await confirmIrreversible()) return;
          payload = { title: choice.title, text };
        }
        try {
          const link = await api(`/api/problems/${root}/causes`, {
            method: "POST", headers: { "content-type": "application/json" },
            body: JSON.stringify(payload),
          });
          const ct = (link.cause && link.cause.title) || "";
          if (choice.cause_id == null) { ta.value = ""; draftDrop(parentId); }
          toast(link.existed ? "такая связь уже есть — твой ответ под ней"
                             : "связано: «" + ct + "» → причина");
          await loadTopics();          // новая проблема появляется в списке
          selectNode(root);            // у следствия теперь виден блок «причины»
        } catch (e) {
          toast((choice.cause_id != null ? "ответ опубликован, но связать не вышло: "
                                         : "не вышло завести причину: ") + e.message);
        }
      },
      // two glued contributions, approved by the author → two sibling nodes
      onSplit: async (parts) => {
        try {
          for (const p of parts) {
            await api("/api/argument", {
              method: "POST", headers: { "content-type": "application/json" },
              body: JSON.stringify({ text: p.text, connect_to: parentId, edge_type: p.type }),
            });
          }
          toast("добавлено " + parts.length + " узла — PoI оценивается в фоне…");
          ta.value = "";
          expanded.add(parentId);
          await fetchChildren(parentId);
          await loadTopics();
          selectNode(parentId);
        } catch (e) { toast("ошибка: " + e.message); }
      },
    });
  };
  send.onclick = runReview;
  act.append(typeSel, send);
  syncConcede();
  card.appendChild(concedeOpt);
  card.appendChild(valueOpt);
  card.appendChild(act);
  // видно всегда, в отличие от подсказок: их выключают, и тогда о
  // неизменности текста узнать негде
  card.appendChild(irreversibleNote());
  card.appendChild(hint);
  return card;
}

// ---- new topic: a root node, opened straight from the header
// prefill — черновик, принесённый из другой формы: компаньон сказал «это тянет
// на отдельную проблему», и терять уже написанное на переходе нельзя.
// Открытые голосования по этому корню — строкой со входом. Голосование
// привязывается к ЛЮБОМУ корню, поэтому слово в заголовке зависит от вида.
async function votesForProblem(rootId, rootKind) {
  let list;
  try { list = await api("/api/decisions?status=open"); }
  catch (e) { return null; }
  const mine = (list || []).filter((d) => d.topic_root_id === rootId);
  if (!mine.length) return null;
  const card = el("div", "card");
  const what = rootKind === "problem" ? "этой проблеме" : "этому обсуждению";
  card.appendChild(el("div", "section-title",
                      (mine.length > 1 ? "Голосования по " : "Голосование по ") + what));
  mine.forEach((d) => {
    const row = el("div");
    row.style.margin = "6px 0";
    const a = el("a", null, d.question);
    a.href = "/vote.html?id=" + d.id;
    row.appendChild(a);
    const meta = el("div", "muted");
    meta.style.fontSize = "12.5px";
    meta.textContent = `голосов: ${d.voters ?? 0} · вариантов: ${d.options ?? 0}`
      + " — можно проголосовать или предложить свой вариант";
    row.appendChild(meta);
    card.appendChild(row);
  });
  return card;
}

function newTopicForm(prefill) {
  selectedId = null;
  detailGen++;                  // панель теперь наша: запоздавший selectNode её не тронет
  renderTree();
  const d = $("#detail");
  d.innerHTML = "";
  const card = el("div", "card");
  card.appendChild(el("div", "section-title", "Создать"));
  const titleIn = el("input");
  titleIn.type = "text";
  titleIn.placeholder = "Проблема — заявленный вред, коротко";   // см. syncKind
  titleIn.style.width = "100%";
  titleIn.style.marginBottom = "8px";
  card.appendChild(titleIn);
  // подсказка дублей: по мере ввода заголовка показываем соседние проблемы —
  // ПРЕДЛОЖЕНИЕ, не гейт. Создать своё всё равно можно.
  const dupes = el("div", "dupes");
  card.appendChild(dupes);
  let dupT;
  const checkDupes = async () => {
    const q = titleIn.value.trim();
    if (q.length < 3) { dupes.innerHTML = ""; return; }
    let hits;
    try { hits = await api("/api/problems/suggest?title=" + encodeURIComponent(q)); }
    catch (_) { return; }
    dupes.innerHTML = "";
    if (!hits || !hits.length) return;
    dupes.appendChild(el("div", "section-title",
      kindSel.value === "problem" ? "похожие проблемы уже есть — может, сюда?"
                                  : "есть проблемы про то же — может, ответить внутри?"));
    for (const h of hits) {
      const row = el("div", "d");
      row.appendChild(el("span", "t", h.title || shortLabel(h.text, 60)));
      row.appendChild(el("span", "m", "   · " + (h.nodes || 0) + " узлов"));
      row.onclick = () => selectNode(h.id);
      dupes.appendChild(row);
    }
  };
  titleIn.addEventListener("input", () => { clearTimeout(dupT); dupT = setTimeout(checkDupes, 350); });
  const ta = el("textarea");
  ta.placeholder = "Постановка: в чём вред, кого касается, каков масштаб…";
  if (prefill) ta.value = prefill;                                // см. syncKind

  // Новая проблема пишется дольше ответа — терять её при обновлении страницы
  // тем более нечего. Ключ "root": черновиков проблем одновременно один.
  const savedRoot = draftRead("root");
  // Вид применяется НЕ здесь: kindSel объявлен ниже, и обращение к нему до
  // объявления упало бы ReferenceError. Держим до syncKind().
  let savedKind = null;
  if (!prefill && savedRoot && (savedRoot.text || savedRoot.title)) {
    savedKind = savedRoot.kind || null;
    if (savedRoot.title) titleIn.value = savedRoot.title;
    if (savedRoot.text) ta.value = savedRoot.text;
    const bar = el("div", "muted");
    bar.style.fontSize = "12.5px";
    bar.append(`черновик проблемы восстановлен · ${draftAge(savedRoot.ts)} `);
    const fresh = el("button", "mini", "начать заново");
    fresh.onclick = () => { draftDrop("root"); titleIn.value = ""; ta.value = ""; bar.remove(); };
    bar.appendChild(fresh);
    card.appendChild(bar);
  }
  // как и в форме ответа: один и тот же текст не разбирается дважды. У корня
  // к тексту добавлен вид: у проблемы рамка разбора другая, чем у остальных.
  let reviewedRoot = null;
  let reviewedKind = null;
  let rootTimer = null;
  const rememberRoot = () => {
    clearTimeout(rootTimer);
    rootTimer = setTimeout(
      () => draftWrite("root", { title: titleIn.value, text: ta.value,
                                 kind: kindSel.value }), 400);
  };
  ta.addEventListener("input", rememberRoot);
  titleIn.addEventListener("input", rememberRoot);

  // Черновик, принесённый с карты: там форма проблемы есть, а разбора и
  // компаньона нет, поэтому она отдаёт написанное сюда. Ключ снимается сразу
  // — второй раз тот же текст подставляться не должен.
  //
  // Рубрика применяется НЕ здесь: domSel/subSel объявлены ниже, и обращение к
  // ним отсюда уходило в TDZ — ReferenceError молча съедался catch'ем, и
  // принесённая с карты рубрика терялась КАЖДЫЙ раз. А по своей же логике
  // (см. ниже) проблема без рубрики не находится ни одним фильтром карты —
  // то есть переносом с карты человек ровно её и лишался.
  let carriedFacets = null;
  try {
    const carried = sessionStorage.getItem("noo_draft_problem");
    if (carried) {
      sessionStorage.removeItem("noo_draft_problem");
      const dr = JSON.parse(carried);
      if (dr.title) titleIn.value = dr.title;
      if (dr.text) ta.value = dr.text;
      carriedFacets = dr;
    }
  } catch (e) { /* черновик не пережил перенос — форма просто пустая */ }
  card.appendChild(ta);
  // Рубрика спрашивается ЗДЕСЬ, в единственной форме создания темы. Пока их
  // было две — на карте и тут, — эта не спрашивала ничего, и всё созданное
  // основным путём падало в «без рубрики» и не находилось ни одним фильтром.
  const rub = el("div");
  rub.style.margin = "10px 0 2px";
  const rubRow = el("div");
  rubRow.style.display = "flex";
  rubRow.style.gap = "8px";
  const domSel = el("select"), subSel = el("select");
  domSel.style.flex = subSel.style.flex = "1 1 0";
  domSel.appendChild(new Option("— направление —", ""));
  rubRow.appendChild(domSel); rubRow.appendChild(subSel);
  rub.appendChild(rubRow);

  const geoIn = el("input");
  geoIn.type = "text";
  geoIn.placeholder = "География — страна, регион или союз (необязательно)";
  geoIn.style.width = "100%"; geoIn.style.marginTop = "8px";
  const geoHits = el("div"); geoHits.className = "cmp-geo-hits";
  const geoChosen = el("div"); geoChosen.className = "cmp-chosen";
  const chosen = new Set();
  rub.appendChild(geoIn); rub.appendChild(geoHits); rub.appendChild(geoChosen);

  const tagsIn = el("input");
  tagsIn.type = "text";
  tagsIn.placeholder = "Теги через запятую (необязательно)";
  tagsIn.style.width = "100%"; tagsIn.style.marginTop = "8px";
  rub.appendChild(tagsIn);
  card.appendChild(rub);

  let TAX = null;
  const geoAll = () => !TAX ? [] : [
    ...TAX.unions.map(u => u.name),
    ...TAX.geo_tree.map(c => c.name),
    ...TAX.geo_tree.flatMap(c => c.regions.map(r => r.name)),
    ...TAX.countries,
  ];
  function renderChosen() {
    geoChosen.innerHTML = [...chosen]
      .map(n => `<span class="cmp-chip">${n}<i>×</i></span>`).join("");
    geoChosen.querySelectorAll(".cmp-chip i").forEach(x => {
      x.onclick = () => { chosen.delete(x.parentElement.firstChild.textContent); renderChosen(); };
    });
  }
  geoIn.addEventListener("input", () => {
    const q = geoIn.value.trim().toLowerCase();
    if (!q) { geoHits.innerHTML = ""; return; }
    const hits = [...new Set(geoAll())]
      .filter(n => n.toLowerCase().includes(q) && !chosen.has(n)).slice(0, 8);
    geoHits.innerHTML = hits.map(n => `<span class="cmp-hit">${n}</span>`).join("");
    geoHits.querySelectorAll(".cmp-hit").forEach(x => {
      x.onclick = () => { chosen.add(x.textContent); geoIn.value = ""; geoHits.innerHTML = ""; renderChosen(); };
    });
  });
  function fillSubs() {
    const d = TAX && TAX.domains.find(x => x.id === domSel.value);
    subSel.innerHTML = "";
    subSel.appendChild(new Option(d ? "— подветвь необязательна —" : "—", ""));
    (d ? d.subs : []).forEach(s => subSel.appendChild(new Option(s, s)));
    subSel.disabled = !d;
  }
  domSel.onchange = fillSubs;
  // Справочник тянем лениво и молча: без него форма всё равно работает, тема
  // просто уйдёт без рубрики — это хуже, но не повод не дать её создать.
  api("/api/taxonomy").then(t => {
    TAX = t;
    t.domains.forEach(d => domSel.appendChild(new Option(d.name, d.id)));
    fillSubs();
    // Рубрика принесённого черновика — только теперь: до загрузки справочника
    // в domSel нет ни одного варианта, и присваивание не удержалось бы.
    if (carriedFacets) {
      if (carriedFacets.domain) { domSel.value = carriedFacets.domain; fillSubs(); }
      if (carriedFacets.sub) subSel.value = carriedFacets.sub;
      for (const g of carriedFacets.geo || []) chosen.add(g);
      renderChosen();
      if (carriedFacets.tags && carriedFacets.tags.length)
        tagsIn.value = carriedFacets.tags.join(", ");
    }
  }).catch(() => { rub.style.display = "none"; });
  fillSubs();

  const act = el("div", "actions");
  // ВИД КОРНЯ. Вернулся 2026-09-09: убрать просили слово «тема», а ушла вместе
  // с ним и возможность завести наверху что-либо, кроме проблемы — человек с
  // вопросом на руках упирался в тупик. Слово «тема» не вернулось ни в одном
  // пункте: виды называются проблема / вопрос / предложение / тезис / разбор.
  //
  // Проблема остаётся первой и по умолчанию — она одна несёт состояние
  // (причины, масштаб, накопитель попыток). Остальные виды живут деревом
  // доводов и позициями; страницы состояния у них нет, и бейдж в дереве
  // честно называет вид, чтобы два сорта корней не выглядели одинаково.
  const kindSel = el("select");
  for (const [v, l] of [["problem", "проблема"], ["question", "вопрос"],
                        ["proposal", "предложение"], ["argument", "тезис"],
                        ["exploration", "разбор"]])
    kindSel.appendChild(new Option(l, v));
  const send = el("button", "primary", "опубликовать");
  const PLACEHOLDER = {
    problem:     ["Проблема — заявленный вред, коротко",
                  "Постановка: в чём вред, кого касается, каков масштаб…"],
    question:    ["Вопрос — коротко, одним предложением",
                  "Что именно непонятно и почему это важно выяснить…"],
    proposal:    ["Предложение — что сделать, коротко",
                  "Что предлагается, зачем и что должно измениться…"],
    argument:    ["Тезис — утверждение, коротко",
                  "Утверждение и на чём оно держится…"],
    exploration: ["Разбор — о чём он, коротко",
                  "Разбор: что известно, что спорно, что осталось открытым…"],
  };
  const syncKind = () => {
    const [t, b] = PLACEHOLDER[kindSel.value] || PLACEHOLDER.problem;
    titleIn.placeholder = t;
    ta.placeholder = b;
    // подсказка дублей ищет ПРОБЛЕМЫ, и её заголовок читается по-разному в
    // зависимости от вида — перерисовываем, если она уже на экране
    if (dupes.childNodes.length) checkDupes();
  };
  kindSel.onchange = () => { syncKind(); rememberRoot(); };
  const cancel = el("button", "mini", "отмена");
  cancel.onclick = () => { renderEmptyDetail(); };
  const hint = el("div");                       // карточка разбора компаньона
  hint.style.display = "none";

  const doCreate = async (text) => {
    const title = titleIn.value.trim();
    if (!title) { toast("укажи название"); titleIn.focus(); return; }
    if (!await confirmIrreversible()) return;
    send.disabled = true; send.textContent = "создаю…";
    try {
      // no connect_to => a new root; the review has no topic to compare
      // against yet, so only the type/quality checks apply here
      const node = await api("/api/argument", {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({
          text, kind: kindSel.value, title,
          domain: domSel.value || null,
          sub: subSel.value || null,
          geo: [...chosen],
          tags: tagsIn.value.split(",").map(s => s.trim()).filter(Boolean),
        }),
      });
      // Состояние (причины, масштаб, накопитель) есть ТОЛЬКО у проблемы —
      // звать заполнять его у вопроса или предложения означало бы обещать
      // страницу, которой там нет.
      const noun = KIND_RU[kindSel.value] || "узел";
      toast(!domSel.value
        ? noun + " создан(а), но без рубрики — в каталоге найдут только поиском"
        : kindSel.value === "problem"
        ? "проблема создана — заполни состояние ниже"
        : noun + " создан(а) — PoI оценивается в фоне…");
      draftDrop("root");                // создано — черновик не нужен
      ROOT.set(node.id, node.id);
      await loadTopics();
      MapView.reload();                 // карта должна увидеть проблему сразу
      selectNode(node.id);
    } catch (e) {
      toast("ошибка: " + e.message);
      send.disabled = false; send.textContent = "опубликовать";
    }
  };

  const submit = async () => {
    const text = ta.value.trim();
    // Молчаливый return читался как «кнопка не работает»: название заполнено,
    // жмёшь — ничего. Пустой текст объясняем так же, как пустое название.
    if (!text) { toast("напиши постановку — одним-двумя абзацами"); ta.focus(); return; }
    if (!titleIn.value.trim()) { toast("укажи название"); titleIn.focus(); return; }
    if (!requireAuth()) return;
    // Проблему навигатор проверяет ОДНИМ тестом (заявлен ли вред и не стоит
    // ли такая уже на доске), остальные виды — как ответы, по форме текста.
    // Как и везде, это предложение, а не запрет: «отправить как есть» на
    // карточке остаётся.
    const isProblem = kindSel.value === "problem";
    send.disabled = true; send.textContent = "ИИ читает черновик…";
    // Переключение вида МЕНЯЕТ рамку разбора у проблемы: тест на вред и
    // классификация по видам — разные вопросы. Поэтому «этот текст уже
    // разобран» помнит и вид, с которым разбирали.
    if (text === reviewedRoot && kindSel.value === reviewedKind) {
      hint.style.display = "none"; await doCreate(text); return;
    }
    const rev = await reviewDraft({ text, kind: kindSel.value });
    reviewedRoot = text; reviewedKind = kindSel.value;
    send.disabled = false; send.textContent = "опубликовать";
    if (!reviewHasNotes(rev)) { hint.style.display = "none"; await doCreate(text); return; }
    renderReview(hint, rev, {
      root: null,
      // тест на вред — только у проблемы; у остальных видов обычная карточка
      // «похоже, тип не совпадает» с кнопкой переключения
      rootTest: isProblem,
      switchLabel: KIND_RU[rev.suggested_type],
      getText: () => ta.value.trim(),
      setText: (t) => { ta.value = t; reviewedRoot = t.trim(); draftWrite("root", { text: t }); },
      connectTo: null,
      onSend: async () => { await doCreate(ta.value.trim()); },
      // «отправить как «предложение»» — переключить вид и опубликовать: совет
      // о качестве на карточке уже относится к предложенному виду
      onSwitch: async (type) => {
        if (PLACEHOLDER[type]) { kindSel.value = type; syncKind(); }
        await doCreate(ta.value.trim());
      },
    });
  };
  send.onclick = submit;
  if (savedKind && PLACEHOLDER[savedKind]) kindSel.value = savedKind;
  syncKind();                      // плейсхолдеры под выбранный вид
  act.append(kindSel, send, cancel);
  card.appendChild(act);
  card.appendChild(irreversibleNote());
  // Второй путь, а не запасной выход: завести своё сверху можно любым видом,
  // но ответ ВНУТРИ уже стоящей проблемы почти всегда прочитают больше людей
  // — там есть, к чему относиться. Предложение, не гейт.
  //
  // И только когда таким проблемам есть чем быть. На пустом графе эта строка
  // отправляла на пустую карту: «найди проблему» — а их ноль. Ровно тот тупик,
  // из-за которого сегодня вернули выбор вида, только зайдённый с другой
  // стороны. Первому человеку показывать нечего — он и есть первый.
  if (TOPICS.some((t) => t.kind === "problem")) {
    const other = el("div", "muted");
    other.style.marginTop = "10px";
    other.style.fontSize = "12.5px";
    other.appendChild(document.createTextNode(
      "Это про проблему, которая уже стоит? Ответ внутри неё прочитают скорее. "));
    const toMap = el("a", "", "Найти проблему в каталоге →");
    toMap.href = "#";
    toMap.onclick = (e) => {
      e.preventDefault();
      showView("map");
      toast("выбери проблему и ответь внутри неё — вопросом, тезисом или предложением");
    };
    other.appendChild(toMap);
    card.appendChild(other);
  }
  card.appendChild(hint);
  d.appendChild(card);
  ta.focus();
}

// ---- positions
async function loadPositions(root, target) {
  try {
    const data = await api(`/api/positions/${root}`);
    target.innerHTML = "";
    if (!data.positions.length) { renderNoPositions(root, target); return; }
    const byId = new Map(data.positions.map((p) => [p.id, p]));
    for (const p of data.positions) target.appendChild(positionCard(p, root, byId, data.links || []));
  } catch (e) { target.textContent = "ошибка: " + e.message; }
}

// Позиций ещё нет. Раньше их собирал сам сервер при первом чтении — и платил
// за это общий ключ, кто бы страницу ни открыл, хоть не входя. Теперь это
// заказ: его делает вошедший и со своего гранта, как «развить», «оспорить» и
// «сделать вывод» рядом.
function renderNoPositions(root, target) {
  if (!ME) {
    target.appendChild(el("div", "muted",
      "Позиции ещё не собраны. Их сводит ИИ по просьбе участника — войди, "
      + "чтобы собрать."));
    return;
  }
  target.appendChild(el("div", "muted", "Позиции ещё не собраны."));
  const act = el("div", "actions");
  const btn = el("button", "mini", "собрать позиции");
  btn.title = "вызов ИИ — оплачивается из твоего гранта";
  btn.onclick = async () => {
    btn.disabled = true; btn.textContent = "ИИ сводит доводы…";
    try {
      await api(`/api/positions/${root}/recompute`, { method: "POST" });
      await loadPositions(root, target);
      toast("позиции собраны");
    } catch (e) {
      btn.disabled = false; btn.textContent = "собрать позиции";
      toast("ошибка: " + e.message);
    }
  };
  act.appendChild(btn);
  target.appendChild(act);
  target.appendChild(el("div", "muted",
    "Это вызов ИИ: он прочитает доводы и сведёт близкие в общие позиции. "
    + "Списывается с твоего гранта."));
}

function positionCard(p, root, byId, links) {
  const box = el("div"); box.style.borderTop = "1px solid var(--line)";
  box.style.padding = "10px 0";
  const head = el("div");
  // подпись позиции по-русски: латинское "support" в русском интерфейсе
  // читается как непереведённый техномусор
  const STANCE_RU = { support: "за", oppose: "против", mixed: "смешанная",
                      dissent: "отдельная", conclusion: "вывод" };
  const st = p.stance || "mixed";
  head.append(el("span", "pill stance-" + st, STANCE_RU[st] || st), " ");
  head.appendChild(el("b", null, p.headline || "(без заголовка)"));
  box.appendChild(head);
  if (p.composed) box.appendChild(el("div", "muted", p.composed));
  if (p.author) box.appendChild(el("div", "muted", "✍ вывод подписал: " + p.author));
  const sup = p.support;
  // сколько ЛЮДЕЙ за позицией — число, без PoI-взвешивания (решение 2026-07-22:
  // система не показывает постоянное «стояние»)
  box.appendChild(el("div", "muted meta-poi", `сторонников: ${sup.count}`));

  // inter-position links, both directions (oppose / conclusion)
  const LINK_OUT = { oppose: "⚔ оспаривает: ", conclusion: "✦ вывод из: " };
  const LINK_IN = { oppose: "⚔ оспорена: ", conclusion: "→ есть вывод: " };
  for (const l of links || []) {
    let label = null, other = null;
    if (l.from_position === p.id) { label = LINK_OUT[l.type]; other = byId.get(l.to_position); }
    else if (l.to_position === p.id) { label = LINK_IN[l.type]; other = byId.get(l.from_position); }
    if (!label || !other) continue;  // link to a position dropped by a re-cluster
    box.appendChild(el("div", "muted", label + "«" + (other.headline || "позиция #" + other.id) + "»"));
  }

  const act = el("div", "actions");
  const vote = el("button", "mini", "▲ поддержать");
  vote.onclick = () => positionVote(p.id, root, "agree");
  const cont = el("button", "mini", "развить");
  cont.onclick = () => positionText(p.id, root, "continue", "чем развить позицию?");
  const opp = el("button", "mini", "оспорить");
  opp.onclick = () => positionText(p.id, root, "oppose", "контр-довод:");
  const q = el("button", "mini", "вопрос");
  q.onclick = () => positionText(p.id, root, "question", "острый вопрос к позиции:");
  act.append(vote, cont, opp, q);
  const cbox = el("div"); cbox.style.display = "none";
  if (p.stance !== "conclusion") {
    // two steps (п.9): ИИ ПРЕДЛАГАЕТ вывод (ничего не пишет), автор правит и
    // подписывает — вывод входит в карту как ЕГО оценённый аргумент, не как
    // анонимный ИИ-текст
    const concl = el("button", "mini", "сделать вывод");
    concl.onclick = async () => {
      if (!requireAuth()) return;
      concl.disabled = true; concl.textContent = "ИИ предлагает вывод…";
      let pre;
      try { pre = await api(`/api/positions/${p.id}/conclude`, { method: "POST" }); }
      catch (e) {
        toast("ошибка: " + e.message);
        concl.disabled = false; concl.textContent = "сделать вывод";
        return;
      }
      concl.disabled = false; concl.textContent = "предложить заново";
      renderConcludeDraft(cbox, p, root, pre);
    };
    act.append(concl);
  }
  box.appendChild(act);
  box.appendChild(cbox);

  if (p.planets && p.planets.length) {
    const pl = el("div", "muted"); pl.style.marginTop = "6px";
    pl.textContent = "спутники: " + p.planets
      .map((x) => `${KIND_RU[x.kind] || x.kind}·PoI${x.poi ?? "—"}`).join(", ");
    box.appendChild(pl);
  }
  return box;
}

function renderConcludeDraft(box, p, root, pre) {
  box.style.display = ""; box.innerHTML = "";
  box.appendChild(el("div", "muted",
    "ИИ предложил вывод — отредактируй и подпиши своим именем; он войдёт в " +
    "карту как ТВОЙ оценённый аргумент, а не как анонимный ИИ-текст:"));
  const hl = el("input"); hl.type = "text";
  hl.value = pre.headline || ""; hl.placeholder = "заголовок вывода";
  hl.style.width = "100%"; hl.style.margin = "6px 0";
  const ta = el("textarea"); ta.value = pre.composed || ""; ta.rows = 4;
  box.appendChild(hl); box.appendChild(ta);
  const act = el("div", "actions");
  const sign = el("button", "primary", "подписать и опубликовать");
  sign.onclick = async () => {
    const composed = ta.value.trim();
    if (!composed) { toast("вывод пустой"); return; }
    sign.disabled = true; sign.textContent = "публикую…";
    try {
      await api(`/api/positions/${p.id}/conclude/confirm`, {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ headline: hl.value.trim(), composed }),
      });
      toast("вывод опубликован — PoI оценивается в фоне…");
      selectNode(root);
    } catch (e) {
      toast("ошибка: " + e.message);
      sign.disabled = false; sign.textContent = "подписать и опубликовать";
    }
  };
  act.appendChild(sign);
  box.appendChild(act);
}

async function positionVote(pid, root, stance) {
  if (!requireAuth()) return;
  try {
    await api(`/api/positions/${pid}/vote`, {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ stance }),
    });
    toast("голос учтён");
    selectNode(root);
  } catch (e) { toast("ошибка: " + e.message); }
}

async function positionText(pid, root, action, prompt_) {
  if (!requireAuth()) return;
  const text = prompt(prompt_);
  if (!text || !text.trim()) return;
  // navigator pre-check (suggestion, not a block) — skip for "continue",
  // which by definition builds on the position it belongs to
  if (action !== "continue") {
    const pre = await precheck(root, text.trim());
    if (pre.verdict !== "new" && pre.position_id !== pid) {
      const msg = (pre.verdict === "covered"
        ? "Это уже есть в обсуждении: «" : "Похожая позиция уже есть: «")
        + (pre.headline || "") + "»\n" + (pre.note || "") + "\n\nВсё равно отправить?";
      if (!confirm(msg)) return;
    }
  }
  try {
    await api(`/api/positions/${pid}/${action}`, {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ text: text.trim() }),
    });
    toast("отправлено — PoI оценивается в фоне…");
    if (KIDS.has(root)) await fetchChildren(root);
    selectNode(root);
  } catch (e) { toast("ошибка: " + e.message); }
}

// ---- live updates: refresh only what is on screen (topics + expanded pages)
function listenEvents() {
  try {
    const es = new EventSource("/api/events");
    es.onmessage = (ev) => {
      let data = {};
      try { data = JSON.parse(ev.data); } catch (_) { /* ignore */ }
      // scoring finished for the node open in the detail panel → redraw it
      if (data.type === "node_scored" && data.node_id === selectedId) {
        selectNode(selectedId);
      }
      if (data.type === "node_score_failed") {
        toast("оценка узла #" + data.node_id + " не удалась");
      }
      if (data.type === "position_updated" && selectedId != null) {
        selectNode(selectedId); // re-render positions with the composed headline
      }
      // topic_poi_updated больше не перерисовывает панель: линза «PoI в теме»
      // убрана (2026-07-22), система не показывает постоянное «стояние»
      clearTimeout(listenEvents._t);
      listenEvents._t = setTimeout(() => refreshVisible(), 500);
    };
  } catch (e) { /* SSE optional */ }
}

// ---- boot
$("#refresh").onclick = () => refreshVisible();
$("#hintsBtn").onclick = () => toggleHints();
$("#newTopic").onclick = () => { showView("tree"); newTopicForm(); };

// ---- переключение видов. Одна страница: переход не рвёт живое соединение,
// не перелогинивает и не заводит вторую форму создания темы.
function showView(v) {
  const map = v === "map";
  $("#mapView").style.display = map ? "" : "none";
  $("#wrap").style.display = map ? "none" : "";
  $("#tagline").textContent = map ? "каталог обсуждений" : "дерево обсуждений";
  document.querySelectorAll("#viewSeg .seg-i").forEach(b =>
    b.classList.toggle("on", b.dataset.view === v));
  if (map) {
    MapView.open($("#mapView"), {
      workspace: WS_IDS,
      onOpenTopic: (id, topic) => { showView("tree"); openTopic(id, topic); },
      onToggleWorkspace: (id, want) => workspaceToggle(id, want),
    });
  }
  history.replaceState(null, "", map ? "?view=map" : location.pathname);
}
document.querySelectorAll("#viewSeg .seg-i").forEach(b => {
  b.onclick = () => showView(b.dataset.view);
});

// Открыть тему из карты: раскрыть и выделить, без перезагрузки страницы.
// Если темы нет в подборке — она показывается как временная, а не добавляется
// молча: открыть и оставить у себя должны остаться разными действиями.
async function openTopic(id, topic) {
  if (ME && !WS_IDS.has(id)) {
    PREVIEW = topic
      ? { id, title: topic.title, text: topic.text || topic.title,
          kind: "argument", poi_score: null, reply_count: topic.nodes || 0,
          author: topic.author || null, author_color: null }
      : (await api(`/api/nodes/${id}`).catch(() => null));
    if (PREVIEW) PREVIEW.id = id;
    await loadTopics();
  }
  expanded.add(id);
  await fetchChildren(id).catch(() => {});
  selectNode(id);
  document.querySelector(`[data-id="${id}"]`)
    ?.scrollIntoView({ block: "center", behavior: "smooth" });
}
$("#loginBtn").onclick = () => openAuth();
$("#logoutBtn").onclick = () => doLogout();

// Приход с лендинга: ?login открывает вход, ?register — регистрацию. Без этого
// человек, уже нажавший «Войти» на noosphere.live, попадал на дерево и должен
// был нажать «Войти» второй раз — шаг, которого он не просил.
const AUTH_INTENT = (() => {
  const p = new URLSearchParams(location.search);
  return p.has("register") ? "register" : p.has("login") ? "login" : null;
})();
// Параметр убирается из адреса сразу, чтобы «назад» и обновление страницы не
// открывали форму снова у того, кто её закрыл. САМО открытие — в boot(), после
// loadMe(): здесь, на верхнем уровне, ME ещё null, и вошедшему показывали
// форму входа поверх его же дерева.
if (AUTH_INTENT) history.replaceState(null, "", location.pathname);

$("#doLogin").onclick = () => doAuth("/api/auth/login");
$("#doRegister").onclick = () => doAuth("/api/auth/register");
$("#toRegister").onclick = (e) => {
  e.preventDefault(); setAuthMode("register"); $("#authUser").focus();
};
$("#toLogin").onclick = (e) => {
  e.preventDefault(); setAuthMode("login"); $("#authUser").focus();
};
$("#authCancel").onclick = () => closeAuth();
$("#forgotLink").onclick = (e) => { e.preventDefault(); showForgot(true); };
$("#forgotBack").onclick = () => showForgot(false);
$("#doForgot").onclick = () => requestReset();
$("#forgotEmail").onkeydown = (e) => { if (e.key === "Enter") requestReset(); };
$("#authPass").addEventListener("keydown", (e) => {
  if (e.key !== "Enter") return;
  doAuth(AUTH_MODE === "register" ? "/api/auth/register" : "/api/auth/login");
});
(async function boot() {
  try {
    updateHintsBtn();
    renderEmptyDetail();
    await loadMe();
    await loadTopics();
    listenEvents();
    trackHeaderHeight();
    loadPresence();                       // не ждём: шапка догрузится сама
    setInterval(loadPresence, 60000);     // «онлайн» с точностью до минуты
    // Гостю показываем карту: рабочего дерева у него нет, а карта честно
    // отвечает «вот что здесь есть» — это лучший первый экран.
    // Явная ссылка (?topic= / ?view=) сильнее умолчания и уже отработала.
    if (!ME && !location.search) showView("map");
    // Форма — только гостю: у вошедшего сессия уже есть, и модалка поверх его
    // дерева читается как «тебя разлогинили».
    if (AUTH_INTENT && !ME) openAuth(null, AUTH_INTENT);
  } catch (e) {
    $("#tree").innerHTML = '<div class="muted" style="padding:10px">' +
      "не удалось загрузить: " + e.message + "<br>Postgres запущен? seed выполнен?</div>";
  }
})();
