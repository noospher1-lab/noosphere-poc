// Noosphere — tree UI, lazy edition.
//
// Read contract (scaling draft, principle 3): the client NEVER loads the whole
// graph. It reads /api/topics (roots) and, per expanded node, a ranked page of
// children from /api/nodes/{id}/children. Expanding a folder = fetching a page.
// /api/graph is only used by the force-directed mode (/graph.html).

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
  const d = $("#detail");
  d.innerHTML = "";
  d.appendChild(HINTS ? guidePanel() : shortEmpty());
}
function shortEmpty() {
  const e = el("div", "empty");
  e.innerHTML =
    "<h3>Выбери обсуждение слева</h3>" +
    "<p>Каждая ветка — проблема. Разворачивай её, чтобы читать доводы за и " +
    "против, задавать вопросы и добавлять свои.</p>" +
    "<p class='muted'>Или начни своё — кнопка «Новая проблема» сверху. " +
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
      "Верхние ветки — проблемы, вложенные — ответы. Цветная метка показывает " +
      "связь с родителем: <b>за</b>, <b>против</b>, <b>уточнение</b>, <b>вопрос</b>."],
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
    ["4", "Отвечай или заводи проблему",
      "Выбери тип ответа (за/против/уточнение/вопрос) и напиши. Перед отправкой " +
      "ИИ-компаньон разберёт черновик и с ним можно поспорить — а опубликованный " +
      "текст уже неизменен, поэтому думать стоит здесь. Решаешь всё равно ты."],
    ["5", "Позиции — общая карта по проблеме",
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
  foot.innerHTML = "Выбери проблему слева, чтобы начать. Эти подсказки можно " +
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

// ---- фактическая высота шапки → --hdr.
// Карта позиционируется от неё (position:fixed). Высота непостоянна: на узком
// экране шапка переносится на две-три строки, и на поворот телефона число
// меняется — поэтому наблюдатель, а не разовый замер.
function trackHeaderHeight() {
  const h = document.querySelector("header");
  if (!h) return;
  const apply = () => document.documentElement.style.setProperty(
    "--hdr", Math.round(h.getBoundingClientRect().height) + "px");
  apply();
  if (window.ResizeObserver) new ResizeObserver(apply).observe(h);
  addEventListener("orientationchange", () => setTimeout(apply, 150));
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
}

function openAuth(msg) {
  $("#authModal").style.display = "flex";
  const ce = $("#checkEmailBox");
  if (ce) { ce.style.display = "none"; ce.innerHTML = ""; }
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
  const login = ["authUser", "authPass", "authName", "authEmail", "authInvite",
                 "authTermsLine"];
  for (const id of login) {
    const el = $("#" + id);
    if (el) el.style.display = on ? "none" : "";
  }
  $("#authModal .actions").style.display = on ? "none" : "";
  $("#authTitle").textContent = on ? "Восстановление доступа" : "Вход или регистрация";
  const ce = $("#checkEmailBox");
  if (ce && !on) { ce.style.display = "none"; ce.innerHTML = ""; }
  $("#forgotLine").style.display = on ? "none" : "";
  $("#forgotBox").style.display = on ? "" : "none";
  $("#authErr").textContent = "";
  if (on) {
    // перенести уже введённую почту, чтобы не набирать заново
    const typed = ($("#authEmail").value || "").trim();
    if (typed) $("#forgotEmail").value = typed;
    $("#forgotEmail").focus();
  }
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
                    "authInvite", "authTermsLine", "forgotLine"]) {
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
  const line = $("#forgotLine");
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
let deepLinkDone = false;
async function openDeepLink() {
  if (deepLinkDone) return;
  deepLinkDone = true;
  const p = new URLSearchParams(location.search);
  if (p.get("view") === "map") { showView("map"); return; }
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

// ---- tree render
function relLabel(type) {
  return { support: "за", refute: "против", qualify: "уточн.", question: "вопрос",
           proposal: "предл.", exploration: "разбор", atom: "атом",
           root: "обсуждение",
           undercut: "подрыв", attribution: "атрибуция" }[type] || type;
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
  // Корень несёт ОДИН бейдж — свой тип (проблема / предложение / вопрос / …).
  // Раньше их было два: «тема» + тип, и первый ничего не сообщал — корень и так
  // виден отступом, а слово «тема» противоречило единице «проблема».
  row.appendChild(type === "root"
    ? el("span", "rel " + (node.kind === "problem" ? "root" : "question"),
         KIND_RU[node.kind] || "обсуждение")
    : el("span", "rel " + type, relLabel(type)));
  // a topic root shows its own short title; replies fall back to a text excerpt
  const label = type === "root" ? (node.title || shortLabel(node.text)) : shortLabel(node.text);
  const txt = el("span", "txt", label);
  txt.title = node.text;
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
  const unscored = node.author_is_service || node.author_is_seed;
  poi.innerHTML = node.poi_score != null ? "PoI <b>" + node.poi_score + "</b>"
                : unscored ? "" : "…";
  if (unscored && node.poi_score == null)
    poi.title = node.author_is_service
      ? "Служебная публикация платформы — в ранжировании не участвует"
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
        "В графе она остаётся — её видят все и найдёшь на карте";
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
      el("span", "legend-key", "что это:"),
      el("span", "rel root", "проблема"),
      el("span", "rel question", "предложение"),
      el("span", "rel question", "уточнение"),
      el("span", "rel question", "вопрос"),
    );
    const rels = el("div", "legend");
    rels.append(
      el("span", "legend-key", "как связано с тем, к чему прикреплено:"),
      el("span", "rel support", "за"),
      el("span", "rel refute", "против"),
      el("span", "rel qualify", "уточн."),
      el("span", "rel question", "вопрос"),
      el("span", "rel undercut", "подрыв"),
    );
    tree.append(kinds, rels);
  }
  if (!TOPICS.length) {
    tree.appendChild(el("div", "muted",
      "Пока нет проблем. Заведи первую кнопкой «+ Новая проблема» сверху."));
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
  selectedId = id;
  renderTree();
  if (!expanded.has(id)) toggleExpand(id);

  let node;
  try { node = await api(`/api/nodes/${id}`); }
  catch (e) { toast("ошибка: " + e.message); return; }
  const root = ROOT.get(id) ?? id;
  const isRoot = root === id;
  const d = $("#detail");
  d.innerHTML = "";

  // node card — a topic root has a short title (heading) distinct from its
  // body text; a reply has no title, so the heading is its own text
  const card = el("div", "card");
  let textEl;      // основной текст узла — цель выделения для ответа на фрагмент
  if (isRoot && node.title) {
    card.appendChild(el("h2", null, node.title));
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
                    : node.atom_group ? "— (атом разбора, живёт реакциями)"
                    : node.author_is_service ? "— (служебная публикация платформы)"
                    : node.author_is_seed ? "— (посевной довод, PoI не считался)"
                    : "оценивается…"),
    "  ·  тип: " + (KIND_RU[node.kind] || node.kind || "тезис"),
    ...(node.atom_group ? ["  ·  из разбора · " + node.atom_group] : []),
    "  ·  ответов: " + (node.reply_count ?? 0),
    "  ·  #" + node.id
  );
  card.appendChild(meta);
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
  agree.onclick = () => react(id, root, "agree");
  dis.onclick = () => react(id, root, "disagree");
  ract.append(agree, dis);
  rwrap.appendChild(ract);
  card.appendChild(rwrap);
  // примечания автора — единственное, что прирастает к зафиксированному тексту
  if (node.addenda && node.addenda.length)
    card.appendChild(addendaBlock(node.addenda));
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

  // страница проблемы: причины, масштаб, реестр решений, атрибуции
  if (isRoot && node.kind === "problem")
    d.appendChild(await problemCard(id));

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
    const pc = el("div", "card");
    pc.appendChild(el("div", "section-title", "Позиции по проблеме"));
    appendHint(pc, "ИИ сводит близкие доводы в <b>позиции</b> — общую карту " +
      "мнений по проблеме. Позицию можно поддержать, оспорить, развить или выйти из " +
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

async function loadReactions(id, root, target) {
  try {
    const r = await api(`/api/reactions/${id}?topic=${root}`);
    // простые счётчики — формула веса ещё открыта (poi-weight-restored)
    if (!r.agree.count && !r.disagree.count) {
      target.textContent = "Пока нет реакций — будь первым.";
      return;
    }
    target.innerHTML = "";
    const a = el("span"); a.style.color = "var(--green)";
    a.textContent = `▲ ${r.agree.count} согласны`;
    const sep = el("span", "muted", "   ·   ");
    const dsp = el("span"); dsp.style.color = "var(--red)";
    dsp.textContent = `▼ ${r.disagree.count} не согласны`;
    target.append(a, sep, dsp);
  } catch (e) { target.textContent = "ошибка: " + e.message; }
}

// loadTopicPoi (линза «PoI в теме») удалена 2026-07-22: система не показывает
// постоянное «стояние» участника; репутацию люди строят по истории голосований.

async function react(id, root, stance) {
  if (!requireAuth()) return;
  try {
    await api("/api/reactions", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ node_id: id, stance }),
    });
    toast("реакция учтена");
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
                   || (rev.placement && rev.placement !== "here") || !!rev.think);
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

function companionThread(hint, { getText, setText, connectTo, opening }) {
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
  if (opening) { history.push({ role: "companion", text: opening }); say("companion", opening); }

  const ta = el("textarea");
  ta.rows = 2;
  ta.placeholder = "ответить компаньону — или спросить его самому";
  box.appendChild(ta);

  const acts = el("div", "actions");
  const send = el("button", "mini", "ответить");
  const wider = el("button", "mini", "поискать шире по карте");
  wider.title = "Компаньон посмотрит не только эту проблему, но и остальные";
  let scope = "near";

  async function turn(msg) {
    if (!msg) return;
    say("author", msg);
    history.push({ role: "author", text: msg });
    ta.value = "";
    send.disabled = true; send.textContent = "думает…";
    try {
      const out = await companionTurn({
        text: getText(), connect_to: connectTo, history, scope,
      });
      say("companion", out.reply);
      history.push({ role: "companion", text: out.reply });
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

const TYPE_LABEL = { support: "за", refute: "против", qualify: "уточнение",
                     question: "вопрос", proposal: "предложение", exploration: "разбор" };

// Renders the navigator's suggestions into `hint`. The author stays in charge:
// callbacks wire "switch type", "go to the node", "support the position",
// "post as is" and "cancel"; editing the draft and resending re-reviews it.
function renderReview(hint, rev, { root, onSend, onSwitch, onSupport, onSplit,
                                   switchLabel, getText, setText, connectTo }) {
  hint.innerHTML = "";
  hint.style.display = "";
  hint.className = "card";
  hint.style.borderColor = "var(--bronze)";
  hint.appendChild(el("div", "section-title", "ИИ-компаньон — разбор перед публикацией"));
  appendHint(hint, "Опубликованный текст правится <b>только здесь</b>: после " +
    "отправки он неизменен. Возражай компаньону, спрашивай его — он для этого.");

  const actions = el("div", "actions");

  if (!rev.type_ok && rev.suggested_type) {
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

  if (rev.verdict === "answered" || rev.verdict === "countered") {
    hint.appendChild(el("div", "section-title",
      rev.verdict === "answered" ? "на этот вопрос уже есть ответ"
                                 : "на этот тезис уже есть возражение"));
    hint.appendChild(el("b", null, rev.target_text || ""));
    if (rev.note) hint.appendChild(el("div", "muted", rev.note));
    const go = el("button", "mini", "перейти к узлу");
    go.onclick = () => {
      hint.style.display = "none";
      if (root != null) ROOT.set(rev.node_id, root);
      selectNode(rev.node_id);
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
  }

  if (rev.quality_note) {
    const q = el("div");
    q.appendChild(el("b", null, "как усилить: "));
    q.appendChild(document.createTextNode(rev.quality_note));
    hint.appendChild(q);
    hint.appendChild(el("div", "muted", "поправь текст и нажми «отправить» ещё раз — компаньон перечитает"));
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
  if (getText)
    companionThread(hint, {
      getText, setText, connectTo,
      opening: rev.think || null,
    });
}

// ---- ответ на фрагмент: выделение текста → типизированное действие с якорем
const FRAG_ACTIONS = [
  ["refute", "Опровергнуть"], ["undercut", "Подорвать"], ["qualify", "Уточнить"],
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
  const cond = el("input"); cond.placeholder = "От каких условий зависело";
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
    "(за / против / уточнение / вопрос), и напиши её. Перед отправкой " +
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
  // подорвать (undercut) целится в участок — доступно только с якорем (см. ниже)
  for (const [v, l] of [["support", "за"], ["refute", "против"], ["qualify", "уточнение"],
                        ["undercut", "подорвать участок"], ["question", "вопрос"],
                        ["proposal", "предложение"], ["exploration", "разбор"]])
    typeSel.appendChild(new Option(l, v));
  const send = el("button", "primary", "отправить");
  const hint = el("div");                       // the navigator's suggestion box
  hint.style.display = "none";

  // форма показывает якорь и, для «подорвать», требует его
  const showAnchor = (a, type) => {
    anchor = a;
    chip.innerHTML = "";
    chip.append("в ответ на: «" + shortLabel(a.quote, 90) + "»");
    const x = el("span", "x", "✕"); x.title = "убрать привязку к участку";
    x.onclick = () => { clearAnchor(); if (typeSel.value === "undercut") typeSel.value = "refute"; };
    chip.appendChild(x);
    chip.style.display = "";
    if (type) typeSel.value = type;
    ta.focus();
    card.scrollIntoView({ behavior: "smooth", block: "nearest" });
  };
  // хэндл для всплывающего меню выделения
  replyHandle = { parentId, setFragment: showAnchor };

  const doSend = async (text) => {
    if (typeSel.value === "undercut" && !anchor) {
      toast("«подорвать» целится в участок — выдели фрагмент текста"); return;
    }
    if (!await confirmIrreversible()) return;
    try {
      await api("/api/argument", {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({
          text, connect_to: parentId, edge_type: typeSel.value,
          anchor: anchor || undefined,
        }),
      });
      clearAnchor();
      toast("добавлено — PoI оценивается в фоне…");
      expanded.add(parentId);
      await fetchChildren(parentId);   // refresh just this branch
      await loadTopics();              // reply counts on roots may change
      selectNode(parentId);
    } catch (e) { toast("ошибка: " + e.message); }
  };

  const runReview = async () => {
    const text = ta.value.trim();
    if (!text) { toast("напиши ответ"); ta.focus(); return; }
    if (!requireAuth()) return;
    send.disabled = true; send.textContent = "ИИ читает черновик…";
    const root = ROOT.get(parentId) ?? parentId;
    const rev = await reviewDraft({ text, connect_to: parentId, edge_type: typeSel.value });
    send.disabled = false; send.textContent = "отправить";
    // nothing to suggest (or the navigator is down) → publish silently
    if (!reviewHasNotes(rev)) { hint.style.display = "none"; await doSend(text); return; }

    renderReview(hint, rev, {
      root,
      // компаньону нужен ЖИВОЙ текст: автор правит черновик прямо во время
      // разговора, и следующий ход должен читать то, что в поле сейчас
      getText: () => ta.value.trim(),
      setText: (t) => { ta.value = t; },
      connectTo: parentId,
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
function newTopicForm(prefill) {
  selectedId = null;
  renderTree();
  const d = $("#detail");
  d.innerHTML = "";
  const card = el("div", "card");
  card.appendChild(el("div", "section-title", "Новая проблема"));
  const titleIn = el("input");
  titleIn.type = "text";
  titleIn.placeholder = "Проблема — заявленный вред, коротко";
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
    dupes.appendChild(el("div", "section-title", "похожие уже есть — может, сюда?"));
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
  ta.placeholder = "тезис, вопрос, предложение или разбор, открывающий обсуждение…";
  if (prefill) ta.value = prefill;
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
  }).catch(() => { rub.style.display = "none"; });
  fillSubs();

  const act = el("div", "actions");
  const kindSel = el("select");
  kindSel.appendChild(new Option("проблема", "problem"));
  kindSel.appendChild(new Option("тезис", "argument"));
  kindSel.appendChild(new Option("вопрос", "question"));
  kindSel.appendChild(new Option("предложение", "proposal"));
  kindSel.appendChild(new Option("разбор", "exploration"));
  const send = el("button", "primary", "опубликовать");
  // проблема — единица по умолчанию: форма открывается в режиме проблемы
  const syncKind = () => {
    const isProb = kindSel.value === "problem";
    send.textContent = "опубликовать";
    titleIn.placeholder = isProb ? "Проблема — заявленный вред, коротко"
      : "Название — коротко, одним предложением";
    ta.placeholder = isProb ? "Постановка: в чём вред, кого касается, каков масштаб…"
      : "тезис, вопрос, предложение или разбор, открывающий обсуждение…";
  };
  kindSel.onchange = syncKind;
  syncKind();
  const cancel = el("button", "mini", "отмена");
  cancel.onclick = () => { renderEmptyDetail(); };
  const hint = el("div");                       // the navigator's suggestion box
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
      const noun = kindSel.value === "problem" ? "проблема создана" : "опубликовано";
      toast(kindSel.value === "problem" ? noun + " — заполни состояние ниже"
        : domSel.value ? noun + " — PoI оценивается в фоне…"
        : noun + ", но без рубрики — на карте её найдут только поиском");
      ROOT.set(node.id, node.id);
      await loadTopics();
      MapView.reload();                 // карта должна увидеть проблему сразу
      selectNode(node.id);
    } catch (e) {
      toast("ошибка: " + e.message);
      send.disabled = false; send.textContent = "опубликовать";
    }
  };

  const runReview = async () => {
    const text = ta.value.trim();
    // Молчаливый return читался как «кнопка не работает»: название заполнено,
    // жмёшь — ничего. Пустой текст объясняем так же, как пустое название.
    if (!text) { toast("напиши постановку — одним-двумя абзацами"); ta.focus(); return; }
    if (!titleIn.value.trim()) { toast("укажи название"); titleIn.focus(); return; }
    if (!requireAuth()) return;
    // проблема — не черновик, который навигатор классифицирует по типам
    // (тезис/вопрос/…): создаём сразу, состояние заполняется на её странице.
    if (kindSel.value === "problem") { await doCreate(text); return; }
    send.disabled = true; send.textContent = "ИИ читает черновик…";
    const rev = await reviewDraft({ text, kind: kindSel.value });
    send.disabled = false; send.textContent = "опубликовать";
    if (!reviewHasNotes(rev)) { hint.style.display = "none"; await doCreate(text); return; }

    // a root is a thesis, question, proposal or exploration — map onto kinds
    const rootKind = KIND_CHIP[rev.suggested_type] ? rev.suggested_type : "argument";
    renderReview(hint, rev, {
      root: null,
      getText: () => ta.value.trim(),
      setText: (t) => { ta.value = t; },
      connectTo: null,
      switchLabel: KIND_CHIP[rootKind] || "тезис",
      onSend: async () => { await doCreate(ta.value.trim()); },
      // advice on the card already applies to the suggested kind — publish
      onSwitch: async () => {
        kindSel.value = rootKind;
        await doCreate(ta.value.trim());
      },
    });
  };
  send.onclick = runReview;
  act.append(kindSel, send, cancel);
  card.appendChild(act);
  card.appendChild(irreversibleNote());
  card.appendChild(hint);
  d.appendChild(card);
  ta.focus();
}

// ---- positions
async function loadPositions(root, target) {
  try {
    const data = await api(`/api/positions/${root}`);
    target.innerHTML = "";
    if (!data.positions.length) { target.appendChild(el("div", "muted", "нет позиций")); return; }
    const byId = new Map(data.positions.map((p) => [p.id, p]));
    for (const p of data.positions) target.appendChild(positionCard(p, root, byId, data.links || []));
  } catch (e) { target.textContent = "ошибка: " + e.message; }
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
  $("#tagline").textContent = map ? "карта проблем" : "дерево проблем";
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

// Приход с лендинга по кнопке «Войти»: ?login открывает форму сразу. Без этого
// человек, уже нажавший «Войти» на noosphere.live, попадал на дерево и должен
// был нажать «Войти» второй раз — шаг, которого он не просил.
if (new URLSearchParams(location.search).has("login")) {
  openAuth();
  // Параметр убирается из адреса, чтобы «назад» и обновление страницы не
  // открывали форму снова у того, кто её закрыл или уже вошёл.
  history.replaceState(null, "", location.pathname);
}
$("#doLogin").onclick = () => doAuth("/api/auth/login");
$("#doRegister").onclick = () => doAuth("/api/auth/register");
$("#authCancel").onclick = () => closeAuth();
$("#forgotLink").onclick = (e) => { e.preventDefault(); showForgot(true); };
$("#forgotBack").onclick = () => showForgot(false);
$("#doForgot").onclick = () => requestReset();
$("#forgotEmail").onkeydown = (e) => { if (e.key === "Enter") requestReset(); };
$("#authPass").addEventListener("keydown", (e) => {
  if (e.key === "Enter") doAuth("/api/auth/login");
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
  } catch (e) {
    $("#tree").innerHTML = '<div class="muted" style="padding:10px">' +
      "не удалось загрузить: " + e.message + "<br>Postgres запущен? seed выполнен?</div>";
  }
})();
