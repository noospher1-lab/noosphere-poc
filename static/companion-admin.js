// Вынесено из companion-admin.html: CSP script-src 'self' не допускает встроенных скриптов.
const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];

// Токен тот же, что у страницы тренажёрных диалогов: один ADMIN_TOKEN сервера.
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
  if (r.status === 403) localStorage.removeItem("admin_token");
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || String(r.status));
  return r.json();
}

let selectedAuthor = null;   // null = общая лента

function fmtDate(iso) {
  if (!iso) return "";
  return new Date(iso).toLocaleString("ru-RU", { day:"2-digit", month:"2-digit",
    hour:"2-digit", minute:"2-digit", second:"2-digit" });
}

const KIND_RU = { review: "разбор", companion: "компаньон", precheck: "проверка места" };

async function loadAuthors() {
  const box = $("#sidebar");
  try {
    const items = await api("/api/dev/companion/authors");
    box.innerHTML = "";
    const all = document.createElement("div");
    all.className = "drow" + (selectedAuthor === null ? " sel" : "");
    all.innerHTML = '<div class="u">Все, вперемешку</div>'
      + '<div class="meta">общая лента по времени</div>';
    all.onclick = () => { selectedAuthor = null; loadAuthors(); loadFeed(); };
    box.appendChild(all);
    if (!items.length) {
      box.appendChild(Object.assign(document.createElement("div"),
        { className: "empty", textContent: "с ИИ ещё никто не говорил" }));
      return;
    }
    for (const it of items) {
      const row = document.createElement("div");
      row.className = "drow" + (selectedAuthor === it.author_id ? " sel" : "");
      row.innerHTML = '<div class="u"></div><div class="meta"></div>';
      row.querySelector(".u").textContent =
        it.author || it.username || ("#" + it.author_id);
      row.querySelector(".meta").textContent =
        `обращений ${it.entries} · ${fmtDate(it.last_at)}`;
      row.onclick = () => { selectedAuthor = it.author_id; loadAuthors(); loadFeed(); };
      box.appendChild(row);
    }
  } catch (e) {
    box.innerHTML = `<div class="empty err">ошибка: ${escHtml(e.message)}</div>`;
  }
}

function entryCard(e) {
  const card = document.createElement("div");
  card.className = "entry";
  const head = document.createElement("div");
  head.className = "entry-head";
  const k = document.createElement("span");
  k.className = "kind " + e.kind;
  k.textContent = KIND_RU[e.kind] || e.kind;
  head.appendChild(k);
  const who = document.createElement("span");
  who.className = "who";
  who.textContent = e.author || e.username || ("#" + e.author_id);
  head.appendChild(who);
  head.appendChild(Object.assign(document.createElement("span"),
    { textContent: fmtDate(e.created_at) }));
  head.appendChild(Object.assign(document.createElement("span"),
    { textContent: e.connect_to ? ("в ответ на #" + e.connect_to)
                                : ("новый корень" + (e.root_kind ? " · " + e.root_kind : "")) }));
  card.appendChild(head);

  card.appendChild(Object.assign(document.createElement("div"),
    { className: "label", textContent: "черновик на этот ход" }));
  card.appendChild(Object.assign(document.createElement("div"),
    { className: "draft", textContent: e.draft || "—" }));

  const hist = e.history || [];
  if (hist.length) {
    card.appendChild(Object.assign(document.createElement("div"),
      { className: "label", textContent: `разговор до этого хода (${hist.length})` }));
    for (const h of hist) {
      const t = document.createElement("div");
      t.className = "turn " + (h.role === "author" ? "author" : "companion");
      t.textContent = (h.role === "author" ? "человек: " : "ИИ: ") + (h.text || "");
      card.appendChild(t);
    }
  }

  const r = e.result || {};
  card.appendChild(Object.assign(document.createElement("div"),
    { className: "label", textContent: "что ответил ИИ" }));
  // Показываем разобранные поля, а не сырой JSON: смотрят сюда, чтобы понять,
  // где человек споткнулся, а не чтобы читать схему ответа.
  const kv = document.createElement("div");
  kv.className = "kv";
  const flags = [
    r.type_ok === false && ("вид не подошёл → " + (r.suggested_type || "—")),
    r.verdict && r.verdict !== "new" && ("вердикт: " + r.verdict),
    r.placement && r.placement !== "here" && ("место: " + r.placement),
    r.split && "предложил разрезать на два",
  ].filter(Boolean);
  for (const f of flags)
    kv.appendChild(Object.assign(document.createElement("span"), { textContent: f }));
  if (flags.length) card.appendChild(kv);

  for (const [field, label] of [["type_note", "про вид"], ["quality_note", "как усилить"],
                                ["place_note", "про место"], ["note", "про совпадение"],
                                ["think", "вопрос автору"], ["reply", "реплика"],
                                ["suggestion", "предложенная формулировка"]]) {
    const v = r[field];
    if (!v) continue;
    const d = document.createElement("div");
    d.className = "note";
    d.appendChild(Object.assign(document.createElement("b"), { textContent: label + ": " }));
    d.appendChild(document.createTextNode(String(v)));
    card.appendChild(d);
  }
  if (!flags.length && !Object.keys(r).some((k) => r[k] && typeof r[k] === "string"))
    card.appendChild(Object.assign(document.createElement("div"),
      { className: "empty", textContent: "замечаний не было" }));
  return card;
}

async function loadFeed() {
  const main = $("#main");
  main.innerHTML = '<div class="empty">загрузка…</div>';
  const kind = $("#kindFilter").value;
  const q = new URLSearchParams({ limit: "300" });
  if (selectedAuthor !== null) q.set("author_id", String(selectedAuthor));
  try {
    let items = await api("/api/dev/companion?" + q.toString());
    if (kind) items = items.filter((e) => e.kind === kind);
    main.innerHTML = "";
    if (!items.length) {
      main.innerHTML = '<div class="empty">ничего нет</div>';
      return;
    }
    for (const e of items) main.appendChild(entryCard(e));
  } catch (e) {
    main.innerHTML = `<div class="empty err">ошибка: ${escHtml(e.message)}</div>`;
  }
}

$("#reload").onclick = () => { loadAuthors(); loadFeed(); };
$("#kindFilter").onchange = loadFeed;
loadAuthors();
loadFeed();
