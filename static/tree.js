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
  if (!r.ok) throw new Error((await r.text()) || r.status);
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
    "<p>Каждая ветка — тема. Разворачивай её, чтобы читать аргументы за и " +
    "против, задавать вопросы и добавлять свои.</p>" +
    "<p class='muted'>Или начни своё — кнопка «Новая тема» сверху. " +
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
      "Верхние ветки — темы, вложенные — ответы. Цветная метка показывает связь " +
      "с родителем: <b>за</b>, <b>против</b>, <b>уточнение</b>, <b>вопрос</b>."],
    ["2", "PoI — это качество довода",
      "PoI оценивает, насколько аргумент проработан: логика, полнота, работа с " +
      "неопределённостью и с возражениями. Проработанные доводы весят в " +
      "голосованиях больше — влияние зарабатывается качеством мысли, а не " +
      "капиталом и не громкостью. Высокий PoI не значит «прав», значит " +
      "«сделан добросовестно»."],
    ["3", "Реагируй: ▲ согласен / ▼ не согласен",
      "Так ты показываешь своё отношение к доводу. Согласие и несогласие — это " +
      "не оценка качества: сильный довод остаётся сильным, даже когда с ним " +
      "не согласны."],
    ["4", "Отвечай или начни тему",
      "Выбери тип ответа (за/против/уточнение/вопрос) и напиши. Перед публикацией " +
      "ИИ-навигатор мягко подскажет тип и есть ли уже похожее — но решаешь ты."],
    ["5", "Позиции — общая карта по теме",
      "ИИ группирует близкие аргументы в позиции. Их можно поддержать, оспорить, " +
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
  foot.innerHTML = "Выбери тему слева, чтобы начать. Эти подсказки можно " +
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

async function loadMe() {
  try { ME = await api("/api/auth/me"); } catch (_) { ME = null; }
  renderAuthUI();
}

function openAuth(msg) {
  $("#authErr").textContent = msg || "";
  $("#authModal").style.display = "flex";
  $("#authUser").focus();
}
function closeAuth() { $("#authModal").style.display = "none"; }

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
  }
  try {
    ME = await api(path, {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    renderAuthUI();
    closeAuth();
    toast("привет, " + ME.name);
  } catch (e) {
    let msg = e.message;
    try { msg = JSON.parse(msg).detail || msg; } catch (_) { /* raw */ }
    $("#authErr").textContent = msg;
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
  try {
    await api(`/api/workspace/${id}`, { method: want ? "POST" : "DELETE" });
    if (want) { WS_IDS.add(id); if (PREVIEW && PREVIEW.id === id) PREVIEW = null; }
    else WS_IDS.delete(id);
    await loadTopics();
    if (typeof MapView !== "undefined") MapView.syncWorkspace(WS_IDS);
    toast(want ? "тема в рабочем дереве" : "убрал из дерева");
  } catch (e) { toast("не вышло: " + e.message); }
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

// ---- tree render
function relLabel(type) {
  return { support: "за", refute: "против", qualify: "уточн.", question: "вопрос",
           proposal: "предл.", exploration: "разбор", atom: "атом", root: "тема",
           undercut: "подрыв", attribution: "атрибуция" }[type] || type;
}
const KIND_CHIP = { question: "вопрос", proposal: "предложение", exploration: "разбор" };
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
  row.appendChild(el("span", "rel " + type, relLabel(type)));
  if (type === "root" && KIND_CHIP[node.kind])
    row.appendChild(el("span", "rel question", KIND_CHIP[node.kind]));
  // a topic root shows its own short title; replies fall back to a text excerpt
  const label = type === "root" ? (node.title || shortLabel(node.text)) : shortLabel(node.text);
  const txt = el("span", "txt", label);
  txt.title = node.text;
  row.appendChild(txt);
  if (hasKids) row.appendChild(el("span", "poi", "(" + node.reply_count + ")"));
  const poi = el("span", "poi");
  poi.innerHTML = node.poi_score != null ? "PoI <b>" + node.poi_score + "</b>" : "…";
  row.appendChild(poi);
  const who = el("span", "who", node.author || "—");
  if (node.author_color) who.style.color = node.author_color;
  else who.classList.add("muted");
  row.appendChild(who);

  // Управление подборкой — только у корней и только у вошедшего.
  if (type === "root" && ME) {
    if (PREVIEW && PREVIEW.id === node.id) {
      row.classList.add("preview");
      const keep = el("span", "ws keep", "оставить у себя");
      keep.title = "Сейчас тема открыта на просмотр и исчезнет при перезагрузке";
      keep.onclick = (e) => { e.stopPropagation(); workspaceToggle(node.id, true); };
      row.appendChild(keep);
    } else {
      const drop = el("span", "ws", "−");
      drop.title = "Убрать из рабочего дерева";
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
    const lg = el("div", "legend");
    lg.append(
      el("span", "rel root", "тема"),
      el("span", "rel support", "за"),
      el("span", "rel refute", "против"),
      el("span", "rel qualify", "уточн."),
      el("span", "rel question", "вопрос"),
      el("span", null, "— как ответ связан с тем, к чему прикреплён"),
    );
    tree.appendChild(lg);
  }
  if (!TOPICS.length) {
    tree.appendChild(el("div", "muted",
      "Пока нет тем. Создай первую кнопкой «Новая тема» сверху."));
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
  const meta = el("div", "muted");
  // имя автора ведёт на его публичный профиль — историю голосований, из которой
  // люди сами строят транзакционную репутацию
  let authorEl = node.author || "—";
  if (node.author_id) {
    authorEl = el("a", null, node.author || "—");
    authorEl.href = "/profile.html?id=" + node.author_id;
    authorEl.title = "профиль и история голосований";
  }
  // an atom of an exploration is a point under investigation: it carries no
  // LLM base score (the разбор was scored as a whole) and no taken position
  meta.append(
    "автор: ", authorEl,
    "  ·  PoI: " + (node.poi_score != null ? node.poi_score
                    : node.atom_group ? "— (атом разбора, живёт реакциями)" : "оценивается…"),
    "  ·  тип: " + (node.kind || "argument"),
    ...(node.atom_group ? ["  ·  из разбора · " + node.atom_group] : []),
    "  ·  ответов: " + (node.reply_count ?? 0),
    "  ·  #" + node.id
  );
  card.appendChild(meta);
  appendHint(card, "<b>PoI</b> — насколько довод проработан, а не «правота». " +
    "Проработанные доводы весят в голосованиях больше.");
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
  // принадлежность нескольким проблемам (домашняя + принесённые) и маркеры
  // оспоренных участков — до реакций визуально не мешают, кладём в конец карточки
  if (node.belongings && node.belongings.length > 1)
    card.appendChild(belongingBar(node.belongings));
  d.appendChild(card);
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
    pc.appendChild(el("div", "section-title", "Позиции по теме"));
    appendHint(pc, "ИИ сводит близкие аргументы в <b>позиции</b> — общую карту " +
      "мнений по теме. Позицию можно поддержать, оспорить, развить или выйти из " +
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
    box.appendChild(el("span", "muted", k));
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
    own ? "твоя отдельная позиция" : "позиция, в которую вошёл аргумент"));
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
      "это ИИ-композиция пула, включающая твой аргумент. Если она искажает " +
      "твою мысль — вынеси аргумент в отдельную позицию (дословно)."));
    const act = el("div", "actions");
    const btn = el("button", "mini", "не согласен с трактовкой — выйти");
    btn.onclick = async () => {
      if (!confirm("Вынести твой аргумент в отдельную позицию? Пул будет " +
                   "перекомпонован без него.")) return;
      btn.disabled = true; btn.textContent = "выношу…";
      try {
        await api(`/api/nodes/${node.id}/dissent`, { method: "POST" });
        toast("аргумент вынесен в собственную позицию");
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
      toast("ИИ-бюджет исчерпан — навигатор отключён, публиковать можно");
    return null;
  }
}

function reviewHasNotes(rev) {
  return !!rev && (!rev.type_ok || !!rev.quality_note || rev.verdict !== "new");
}

const TYPE_LABEL = { support: "за", refute: "против", qualify: "уточнение",
                     question: "вопрос", proposal: "предложение", exploration: "разбор" };

// Renders the navigator's suggestions into `hint`. The author stays in charge:
// callbacks wire "switch type", "go to the node", "support the position",
// "post as is" and "cancel"; editing the draft and resending re-reviews it.
function renderReview(hint, rev, { root, onSend, onSwitch, onSupport, onSplit, switchLabel }) {
  hint.innerHTML = "";
  hint.style.display = "";
  hint.className = "card";
  hint.style.borderColor = "var(--bronze)";
  hint.appendChild(el("div", "section-title", "ИИ-навигатор — подсказка перед публикацией"));

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

  if (rev.quality_note) {
    const q = el("div");
    q.appendChild(el("b", null, "как усилить: "));
    q.appendChild(document.createTextNode(rev.quality_note));
    hint.appendChild(q);
    hint.appendChild(el("div", "muted", "поправь текст и нажми «отправить» ещё раз — навигатор перечитает"));
  }

  const anyway = el("button", "mini", "отправить как есть");
  anyway.onclick = () => { hint.style.display = "none"; onSend(); };
  const cancel = el("button", "mini", "отмена");
  cancel.onclick = () => { hint.style.display = "none"; };
  actions.append(anyway, cancel);
  hint.appendChild(actions);
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
  if (p.scale_note || p.scale_url) {
    const s = el("div", "prob-block");
    s.appendChild(el("div", "section-title", "Масштаб · данные извне"));
    if (p.scale_note) s.appendChild(el("div", "causes", p.scale_note));
    if (p.scale_url) {
      const src = el("div", "scale-src");
      if (p.scale_excerpt) src.appendChild(el("span", "q", "«" + p.scale_excerpt + "» "));
      const a = el("a", null, "источник ↗");
      a.href = p.scale_url; a.target = "_blank"; a.rel = "noopener";
      src.appendChild(a);
      s.appendChild(src);
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
    "пока нет записей — пустая проблема хуже пустой темы, внеси первую попытку"));
  for (const iv of ivs) reg.appendChild(await interventionCard(iv));
  body.appendChild(reg);

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
    foot.append(editBtn, ivBtn);
    card.appendChild(foot);
  }
  return card;
}

// правка состояния проблемы: причины + масштаб (данные извне с выдержкой)
function problemEditForm(nodeId, p, rerender) {
  if (!requireAuth()) return null;
  const f = el("div", "pedit prob-block");
  f.appendChild(el("div", "section-title", "Состояние проблемы"));
  const causes = el("textarea"); causes.placeholder = "Причины и составные части";
  causes.value = p.causes || "";
  const note = el("input"); note.placeholder = "Масштаб — где, сколько (своими словами)";
  note.value = p.scale_note || "";
  const url = el("input"); url.placeholder = "Ссылка на данные (внешняя)";
  url.value = p.scale_url || "";
  const exc = el("textarea"); exc.placeholder = "Сохранённая выдержка из источника (текст)";
  exc.value = p.scale_excerpt || "";
  f.append(causes, note, url, exc);
  const act = el("div", "actions");
  const save = el("button", "primary mini", "сохранить состояние");
  save.onclick = async () => {
    try {
      await api(`/api/problems/${nodeId}`, {
        method: "PUT", headers: { "content-type": "application/json" },
        body: JSON.stringify({
          causes: causes.value.trim() || null, scale_note: note.value.trim() || null,
          scale_url: url.value.trim() || null, scale_excerpt: exc.value.trim() || null,
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
    if (!text) return;
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
  appendHint(card, "Выбери, как твоя реплика относится к этому доводу " +
    "(за / против / уточнение / вопрос), и напиши её. Перед публикацией " +
    "ИИ-навигатор мягко подскажет — но решаешь ты.");
  const ta = el("textarea");
  ta.placeholder = "Твой аргумент…";
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
    if (!text) return;
    if (!requireAuth()) return;
    send.disabled = true; send.textContent = "ИИ читает черновик…";
    const root = ROOT.get(parentId) ?? parentId;
    const rev = await reviewDraft({ text, connect_to: parentId, edge_type: typeSel.value });
    send.disabled = false; send.textContent = "отправить";
    // nothing to suggest (or the navigator is down) → publish silently
    if (!reviewHasNotes(rev)) { hint.style.display = "none"; await doSend(text); return; }

    renderReview(hint, rev, {
      root,
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
  card.appendChild(hint);
  return card;
}

// ---- new topic: a root node, opened straight from the header
function newTopicForm() {
  selectedId = null;
  renderTree();
  const d = $("#detail");
  d.innerHTML = "";
  const card = el("div", "card");
  card.appendChild(el("div", "section-title", "Новая тема"));
  const titleIn = el("input");
  titleIn.type = "text";
  titleIn.placeholder = "Название темы — коротко, одним предложением";
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
  const send = el("button", "primary", "создать тему");
  // проблема — единица по умолчанию: форма открывается в режиме проблемы
  const syncKind = () => {
    const isProb = kindSel.value === "problem";
    send.textContent = isProb ? "создать проблему" : "создать тему";
    titleIn.placeholder = isProb ? "Проблема — заявленный вред, коротко"
      : "Название темы — коротко, одним предложением";
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
    if (!title) { toast("укажи название темы"); titleIn.focus(); return; }
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
      const noun = kindSel.value === "problem" ? "проблема создана" : "тема создана";
      toast(kindSel.value === "problem" ? noun + " — заполни состояние ниже"
        : domSel.value ? noun + " — PoI оценивается в фоне…"
        : noun + ", но без рубрики — на карте её найдут только поиском");
      ROOT.set(node.id, node.id);
      await loadTopics();
      MapView.reload();                 // карта должна увидеть тему сразу
      selectNode(node.id);
    } catch (e) {
      toast("ошибка: " + e.message);
      send.disabled = false; send.textContent = "создать тему";
    }
  };

  const runReview = async () => {
    const text = ta.value.trim();
    if (!text) return;
    if (!titleIn.value.trim()) { toast("укажи название темы"); titleIn.focus(); return; }
    if (!requireAuth()) return;
    // проблема — не черновик, который навигатор классифицирует по типам
    // (тезис/вопрос/…): создаём сразу, состояние заполняется на её странице.
    if (kindSel.value === "problem") { await doCreate(text); return; }
    send.disabled = true; send.textContent = "ИИ читает черновик…";
    const rev = await reviewDraft({ text, kind: kindSel.value });
    send.disabled = false; send.textContent = "создать тему";
    if (!reviewHasNotes(rev)) { hint.style.display = "none"; await doCreate(text); return; }

    // a root is a thesis, question, proposal or exploration — map onto kinds
    const rootKind = KIND_CHIP[rev.suggested_type] ? rev.suggested_type : "argument";
    renderReview(hint, rev, {
      root: null,
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
  head.append(el("span", "pill stance-" + (p.stance || "mixed"), p.stance || "mixed"), " ");
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
  opp.onclick = () => positionText(p.id, root, "oppose", "контр-аргумент:");
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
    pl.textContent = "спутники: " + p.planets.map((x) => `${x.kind}·PoI${x.poi ?? "—"}`).join(", ");
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
  $("#tagline").textContent = map ? "карта тем" : "дерево обсуждений";
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
$("#doLogin").onclick = () => doAuth("/api/auth/login");
$("#doRegister").onclick = () => doAuth("/api/auth/register");
$("#authCancel").onclick = () => closeAuth();
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
    // Гостю показываем карту: рабочего дерева у него нет, а карта честно
    // отвечает «вот что здесь есть» — это лучший первый экран.
    // Явная ссылка (?topic= / ?view=) сильнее умолчания и уже отработала.
    if (!ME && !location.search) showView("map");
  } catch (e) {
    $("#tree").innerHTML = '<div class="muted" style="padding:10px">' +
      "не удалось загрузить: " + e.message + "<br>Postgres запущен? seed выполнен?</div>";
  }
})();
