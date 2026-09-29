// Сведения обсуждения (vault: decisions/2026-09-28-info-sector) — слой обсуждения,
// как реестр: законы, цифры, сообщения и опыт людей по этому вопросу.
// Порядок чтения задал Alex 28.09: сначала общее для всех — закон, на котором
// основаны действия стран, и нормы о правах и равенстве; потом страна; потом
// город, если там своя практика. Карта — вход в страну.
// Мы не консультируем, а собираем доступное: у сведения нет вердикта, есть то,
// на что оно опирается (сверенная цитата, источник или опыт людей).
//
// Правки по проходу студии 28.09 (reports/2026-09-28-info-*): общая часть —
// списком заголовков, раскрываются по нажатию (иначе карта была на 9-м экране);
// ссылки #f<id> и #c=<страна> открывают нужное; отметка — в самой карточке,
// без системных окон; поиск по всем странам, не только со сведениями.
"use strict";

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const $ = (sel, root = document) => root.querySelector(sel);
const qs = new URLSearchParams(location.search);
// Сведения — слой обсуждения: адрес — корень обсуждения (?root=128). Старый
// адрес со словом (?s=ua-eu) сервер ещё понимает.
const ROOT = qs.get("root") || qs.get("s") || "";

async function api(path, opts = {}) {
  let r;
  try {
    r = await fetch(path, {
      credentials: "same-origin",
      headers: opts.body ? { "Content-Type": "application/json" } : {},
      ...opts,
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
  } catch {
    throw new Error("Нет связи с сервером — проверьте интернет и попробуйте ещё раз");
  }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    // ошибка проверки полей приходит списком объектов — показывать его как
    // «[object Object]» нельзя (UX 28.09, М2)
    let msg = data.detail;
    if (Array.isArray(msg)) msg = "Проверьте поля формы — какое-то заполнено не так";
    throw Object.assign(new Error(msg || `Ошибка ${r.status}`), { status: r.status });
  }
  return data;
}

let ME = null, CFG = {}, VIEW = null, COUNTRIES = [], CURRENT = null, PRESET_COUNTRY = null;
let FLASH = null;           // сообщение, которое должно пережить перерисовку
const loginUrl = () => "/?login&next=" + encodeURIComponent(location.pathname + location.search + location.hash);

function plural(n, one, few, many) {
  const m10 = n % 10, m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
  return many;
}
const dateRu = (iso) => iso ? new Date(iso).toLocaleDateString("ru-RU") : "";
const hostOf = (u) => { try { return new URL(u).hostname; } catch { return "источник"; } };

const KIND = {
  norm: ["норма", "текст закона или официальное разъяснение ведомства"],
  report: ["сообщают", "пересказ источника: СМИ, юристы, помогающие организации"],
  experience: ["опыт людей", "так было с людьми — опирается на число подтверждений"],
};
// подсказки к привычным разделам; раздел — подпись обсуждения, у других свои
const SECTION_HINT = {
  "Закон-основание": "На чём основаны действия: законы и решения. Страны применяют их с разных дат — смотрите свою страну.",
  "Права и равенство": "Нормы о равенстве и запрете дискриминации.",
  "Масштаб": "Где встречается и в каких объёмах.",
  "Продление защиты": "Продление временной защиты: сроки и что требуют.",
  "Переход на вид на жительство": "Переход с временной защиты на вид на жительство: какие есть пути.",
  "Оспаривание в суде": "Где и в какие сроки обжалуют решения.",
};

// ------------------------------------------------------------ карточка

function quoteStatus(f) {
  if (!f.source_url) return "";
  if (f.stale) return `<span class="qs stale">⟳ давно не сверялось (последний раз ${dateRu(f.checked_at)})</span>`;
  return {
    verified: `<span class="qs verified">✓ цитата найдена на странице ${dateRu(f.checked_at)}</span>`,
    mismatch: `<span class="qs mismatch">✗ такой цитаты на странице нет — возможно, текст изменился</span>`,
    unreachable: `<span class="qs unreachable">? сайт не пустил автоматическую проверку — сверьте сами по ссылке</span>`,
    unchecked: `<span class="qs unchecked">? цитата ещё не сверена</span>`,
    none: "",
  }[f.quote_status] || "";
}

function factCard(f, ctx) {
  // в общей части не повторять «ЕС и международное право», в стране — её имя
  const where = [ctx === "country" ? null : (f.country || null), f.city, f.office, f.place_note].filter(Boolean).join(" · ");
  const extra = [f.applies_to && "кого касается: " + f.applies_to, f.when_text && "когда: " + f.when_text]
    .filter(Boolean).join(" · ");
  const meta = [where, extra].filter(Boolean).join(" · ");
  const src = f.source_url
    ? `<a href="${esc(f.source_url)}" target="_blank" rel="noopener nofollow">${esc(f.source_title || hostOf(f.source_url))}</a>`
    : `<span>без ссылки</span>`;
  const own = ME && f.author && f.author.id === ME.id;
  const n = (v, ind) => v ? ` · ${v}${ind !== v ? ` <span class="ind">(независимых ${ind})</span>` : ""}` : "";
  let reports = "";
  if (f.kind !== "norm") {
    reports = own ? `<span class="muted">ваше сведение отмечают другие люди</span>` : `
      <button class="rep same" data-v="same" aria-pressed="${f.mine === "same"}">у меня так же${n(f.same, f.same_independent)}</button>
      <button class="rep differs" data-v="differs" aria-pressed="${f.mine === "differs"}">у меня иначе${n(f.differs, f.differs_independent)}</button>`;
  }
  const notes = (f.notes || []).length ? `<ul class="notes">${f.notes.map(x =>
      `<li>${x.verdict === "same" ? "так же" : "иначе"}, ${dateRu(x.at)}: ${esc(x.note)}</li>`).join("")}</ul>` : "";
  return `
  <article class="fact" id="f${f.id}" data-id="${f.id}">
    <div class="head">
      <span class="kind ${f.kind}" title="${esc(KIND[f.kind]?.[1] || "")}">${KIND[f.kind]?.[0] || f.kind}</span>
      <div class="title">${esc(f.title)}</div>
    </div>
    ${meta ? `<div class="where">${esc(meta)}</div>` : ""}
    ${f.body ? `<div class="body">${esc(f.body)}</div>` : ""}
    ${f.source_quote ? `<blockquote>${esc(f.source_quote)}</blockquote>` : ""}
    <div class="src">источник: ${src} ${quoteStatus(f)}</div>
    <div class="foot">
      ${reports}
      <span class="spacer"></span>
      <span>внёс ${esc(f.author?.name || "—")}, ${dateRu(f.created_at)}</span>
      <a class="idlink" href="#f${f.id}" title="Чтобы сослаться на это сведение в доводе, напишите в тексте #с${f.id}">#с${f.id}</a>
      ${own ? `<button class="linkbtn rm">снять</button>` : ""}
    </div>
    <div class="repform" hidden></div>
    ${notes}
  </article>`;
}

// общая часть: заголовки, раскрываются по нажатию
function factRow(f) {
  return `<details class="frow" id="f${f.id}" data-id="${f.id}">
    <summary><span class="kind ${f.kind}">${KIND[f.kind]?.[0] || f.kind}</span>
      <span class="rt">${esc(f.title)}</span><span class="chev" aria-hidden="true">›</span></summary>
    <div class="fbody"></div>
  </details>`;
}

function bySection(facts, ctx) {
  const out = [];
  const order = VIEW.sections.concat([...new Set(facts.map(f => f.section))].filter(x => !VIEW.sections.includes(x)));
  for (const s of order) {
    const list = facts.filter(f => f.section === s);
    if (!list.length) continue;
    out.push(`<h3>${esc(s)}</h3>` +
      (ctx === "common" && SECTION_HINT[s] ? `<p class="muted hint">${esc(SECTION_HINT[s])}</p>` : "") +
      (ctx === "common" ? `<div class="rows">${list.map(factRow).join("")}</div>` : list.map(f => factCard(f, ctx)).join("")));
  }
  return out.join("") || `<p class="empty">Пока ничего нет.</p>`;
}

// ------------------------------------------------------------ карта

// ступени постоянные, а не от максимума: иначе одна страна с 10 сведениями
// перекрашивала бы всю карту (дизайн 28.09)
const lvl = (n) => n >= 4 ? " l3" : n >= 2 ? " l2" : "";

function mapSvg(counts) {
  if (typeof INFO_MAP === "undefined") return "";
  const known = new Set(COUNTRIES);
  const parts = [], dots = [];
  for (const [name, c] of Object.entries(INFO_MAP.countries)) {
    const n = counts[name] || 0;
    const ok = known.has(name);
    const cls = (n ? "has" + lvl(n) : "") + (ok ? " pick" : " off");
    const t = `<title>${esc(name)}${n ? ": " + n + " " + plural(n, "сведение", "сведения", "сведений") : ""}</title>`;
    const data = ok ? ` data-c="${esc(name)}"` : "";
    if (c.d) parts.push(`<path d="${c.d}" class="${cls}"${data}>${t}</path>`);
    if (c.dot) {
      // точка микрогосударства видна маленькой, но нажимается широким кругом
      dots.push(`<g class="dot ${cls}"${data}>${t}<circle class="hit" cx="${c.c[0]}" cy="${c.c[1]}" r="26"/>` +
        `<circle class="mark" cx="${c.c[0]}" cy="${c.c[1]}" r="8"/></g>`);
    }
  }
  return `<svg viewBox="0 0 ${INFO_MAP.w} ${INFO_MAP.h}" role="img" aria-label="Карта Европы: где есть сведения">${parts.join("")}${dots.join("")}</svg>`;
}

// ------------------------------------------------------------ страница

function render() {
  const v = VIEW;
  const counts = Object.fromEntries(v.countries.map(c => [c.country, c.facts]));
  const listed = v.countries.map(c => c.country).sort((a, b) => a.localeCompare(b, "ru"));
  const rel = (v.related || []).map(r => `<li><a href="/info.html?root=${r.id}">${r.rel === "cause" ? "↑ причина" : "↓ порождает"}: ${esc(r.title || "#" + r.id)}</a> <span class="muted">· сведений: ${r.facts}</span></li>`).join("");
  $("main").innerHTML = `
    <p class="crumbs"><a href="/n/${v.topic.id}">← К обсуждению</a></p>
    <h1><span class="muted small-h">Сведения ·</span> ${esc(v.topic.title)}</h1>
    <nav class="toc toc-top">
      ${v.countries.length ? `<a class="primary" href="#countries">Моя страна →</a>` : ""}
      ${v.common.length ? `<a href="#common">Общее для всех</a>` : ""}
      <a href="#ask">Спросить</a>
      <a href="#add">+ Добавить сведение</a>
    </nav>
    <div class="note">Здесь не консультируют. Здесь собрано то, что доступно: тексты законов и разъяснения
      ведомств со ссылками, сообщения СМИ и юристов, опыт людей. Как этим распорядиться — решаете вы. У каждого
      сведения видно, на что оно опирается.
      <div class="legend">
        <span><span class="kind norm">норма</span> текст закона или разъяснение ведомства; сервер проверяет, есть ли цитата на странице</span>
        <span><span class="kind report">сообщают</span> пересказ СМИ, юристов, организаций — со ссылкой</span>
        <span><span class="kind experience">опыт людей</span> так было с людьми; «независимых» — отметки, похожие на одного человека, считаются за одну</span>
      </div>
      <p class="muted small">Сослаться на сведение в доводе обсуждения — написать его номер, например <b>#с${(v.common[0] || {}).id || 12}</b>: под доводом появится карточка сведения.</p>
    </div>

    ${v.total ? "" : `<p class="empty">У этого обсуждения сведений пока нет: законов, цифр, сообщений,
      опыта людей. Если знаете — <a href="#add">добавьте первое</a>.</p>`}
    ${v.common.length ? `<h2 id="common">Общее для всех, независимо от страны</h2>
    <p class="muted">Сначала закон и нормы о правах, потом остальное. Нажмите на строку, чтобы раскрыть.
      Что делает конкретная страна — ниже.</p>
    ${bySection(v.common, "common")}` : ""}

    ${v.countries.length ? `<h2 id="countries">По странам</h2>
    <p class="muted">${v.countries.length} ${plural(v.countries.length, "страна", "страны", "стран")} со сведениями,
      всего сведений: ${v.total}. Выберите страну в списке или на карте.</p>
    <div class="mapwrap">
      <div class="side">
        <input type="search" class="csearch" placeholder="Найти страну…" aria-label="Найти страну" />
        <ul class="clist">${countryItems(listed, counts)}</ul>
      </div>
      <div class="map">${mapSvg(counts)}
        <div class="maplegend"><span class="sw"></span>нет сведений <span class="sw has"></span>1
          <span class="sw has l2"></span>2–3 <span class="sw has l3"></span>4 и больше</div></div>
    </div>
    <section id="country-panel"></section>` : `<section id="country-panel"></section>`}
    ${rel ? `<h2>Связанные обсуждения</h2><ul class="related">${rel}</ul>` : ""}

    <h2 id="ask">Спросить по сведениям</h2>
    <p class="muted">ИИ отвечает только по собранным здесь сведениям и ставит их номера. Если сведений нет — так и скажет.</p>
    <div class="card" id="ask-box"></div>

    <h2 id="add">Добавить сведение</h2>
    <p class="muted">${CFG.ai
      ? "Расскажите своими словами, что знаете или что с вами было. ИИ разложит текст по полям, вы проверите и опубликуете."
      : "ИИ сейчас выключен — заполните поля сами: одной фразой, что известно, и где это было."}
      Имена, адреса и номера документов не пишите. Если дадите ссылку, сервер откроет страницу и
      проверит, есть ли там ваша выдержка дословно.</p>
    <div class="card" id="add-box"></div>`;

  bindFacts($("main"));
  bindRows($("main"));
  $("main").querySelectorAll(".map [data-c]").forEach(el =>
    el.addEventListener("click", () => openCountry(el.dataset.c)));
  if (v.countries.length) bindCountryList(counts, listed);
  renderAsk();
  renderAdd();
}

function countryItems(names, counts) {
  if (!names.length) return `<li class="none"><span>Ничего не нашлось</span></li>`;
  return names.map(c => `<li><button data-c="${esc(c)}" aria-current="${c === CURRENT}">` +
    `<span>${esc(c)}</span><span class="n">${counts[c] || "—"}</span></button></li>`).join("");
}

function bindCountryList(counts, listed) {
  const list = $(".clist"), input = $(".csearch");
  const bind = () => list.querySelectorAll("[data-c]").forEach(b =>
    b.addEventListener("click", () => openCountry(b.dataset.c)));
  bind();
  // поиск идёт по ВСЕМ странам, не только со сведениями: иначе Литву можно
  // было найти только на карте (UX 28.09, М7)
  input.addEventListener("input", () => {
    const q = input.value.trim().toLowerCase();
    const names = q ? COUNTRIES.filter(c => c.toLowerCase().includes(q))
      .sort((a, b) => (counts[b] ? 1 : 0) - (counts[a] ? 1 : 0) || a.localeCompare(b, "ru")).slice(0, 30)
      : listed;
    list.innerHTML = countryItems(names, counts);
    bind();
  });
}

async function openCountry(name, scroll = true) {
  const box = $("#country-panel");
  CURRENT = name;
  document.querySelectorAll(".map .sel").forEach(e => e.classList.remove("sel"));
  document.querySelectorAll(`.map [data-c="${CSS.escape(name)}"]`).forEach(e => e.classList.add("sel"));
  document.querySelectorAll(".clist button").forEach(b => b.setAttribute("aria-current", b.dataset.c === name));
  if (!location.hash.startsWith("#f")) history.replaceState(null, "", "#c=" + encodeURIComponent(name));
  let v;
  try { v = await api(`/api/info/${ROOT}/country/${encodeURIComponent(name)}`); }
  catch (e) { box.innerHTML = `<p class="msg err">${esc(e.message)}</p>`; return; }
  const n = v.national.length + v.cities.reduce((s, c) => s + c.facts.length, 0);
  box.innerHTML = `<div class="country">
      <h2>${esc(name)}</h2>
      ${n ? "" : `<p class="empty">По этой стране сведений пока нет. Общие правила — выше, в «Общее для всех». Если знаете, как здесь продлевают защиту или переходят на вид на жительство, добавьте.</p>`}
      ${v.national.length ? bySection(v.national, "country") : ""}
      ${v.cities.map(c => `<div class="city"><h3 class="cityname">${esc(c.city)}</h3>${bySection(c.facts, "country")}</div>`).join("")}
      <div class="btns"><button class="btn" data-add-country="${esc(name)}">+ Добавить сведение по стране «${esc(name)}»</button></div>
    </div>`;
  bindFacts(box);
  box.querySelector("[data-add-country]").onclick = () => {
    // страна подставляется и в уже открытую форму, и в будущую (UX 28.09, Б1)
    PRESET_COUNTRY = name;
    const sel = $("#add-box select[name=country]");
    if (sel) sel.value = name;
    const hint = $("#add-country-hint");
    if (hint) hint.textContent = `Страна: ${name}`;
    $("#add").scrollIntoView({ behavior: "smooth" });
  };
  if (scroll) box.scrollIntoView({ behavior: "smooth" });
}

function bindRows(root) {
  root.querySelectorAll("details.frow").forEach(d => d.addEventListener("toggle", () => {
    if (!d.open || d.dataset.filled) return;
    const f = VIEW.common.find(x => String(x.id) === d.dataset.id);
    if (!f) return;
    d.querySelector(".fbody").innerHTML = factCard(f, "common");
    d.dataset.filled = "1";
    bindFacts(d);
  }));
}

function bindFacts(root) {
  root.querySelectorAll(".fact").forEach(card => {
    if (card.dataset.bound) return;
    card.dataset.bound = "1";
    const id = card.dataset.id;
    const form = card.querySelector(".repform");
    card.querySelectorAll(".rep").forEach(b => b.addEventListener("click", async () => {
      if (!ME) {
        form.hidden = false;
        form.innerHTML = `<p class="muted">Чтобы отметить, <a href="${esc("/?login&next=" + encodeURIComponent(location.pathname + location.search + "#f" + id))}">войдите</a> — после входа вернётесь к этому сведению.</p>`;
        return;
      }
      const on = b.getAttribute("aria-pressed") === "true";
      if (on) { return sendReport(card, id, null, null); }
      // отметка — в самой карточке, без системного окна: там «Отмена»
      // отменяла саму отметку, а о том, что заметку увидят все, не говорилось
      form.hidden = false;
      form.innerHTML = `
        <label class="f">${b.dataset.v === "same" ? "Где и когда было так же?" : "Как было у вас?"} (можно не писать)
          <textarea name="note" maxlength="500" placeholder="Например: 20.09, ведомство в Лейпциге"></textarea></label>
        <p class="muted small">Заметку увидят все. Без имён и номеров документов.</p>
        <div class="btns"><button class="btn primary" data-go>Отметить</button><button class="btn" data-cancel>Отмена</button></div>`;
      form.querySelector("[data-cancel]").onclick = () => { form.hidden = true; form.innerHTML = ""; };
      form.querySelector("[data-go]").onclick = (ev) => {
        ev.currentTarget.disabled = true;
        sendReport(card, id, b.dataset.v, form.querySelector("[name=note]").value.trim() || null);
      };
    }));
    const rm = card.querySelector(".rm");
    if (rm) rm.onclick = async () => {
      if (!confirm("Снять сведение? Оно исчезнет для всех.")) return;
      try { await api(`/api/info/facts/${id}`, { method: "DELETE" }); await reload(); }
      catch (e) { alert(e.message); }
    };
  });
}

async function sendReport(card, id, verdict, note) {
  try {
    await api(`/api/info/facts/${id}/report`, { method: "POST", body: { verdict, note } });
    await reload("f" + id);
  } catch (e) {
    const form = card.querySelector(".repform");
    form.hidden = false;
    form.innerHTML = `<p class="msg err">${esc(e.message)}</p>`;
  }
}

async function reload(anchor) {
  VIEW = await api(`/api/info/${ROOT}`);
  render();
  if (CURRENT) await openCountry(CURRENT, false);
  if (anchor) reveal(anchor.replace(/^f/, ""), false);
}

// ссылка на сведение: общая часть — раскрыть строку; страна — открыть страну
async function reveal(id, fetchPlace = true) {
  let el = document.getElementById("f" + id);
  if (!el && fetchPlace) {
    try {
      const place = await api(`/api/info/fact/${id}`);
      if (place.country) await openCountry(place.country, false);
      el = document.getElementById("f" + id);
    } catch { /* сведения нет — остаёмся наверху */ }
  }
  if (!el) return;
  if (el.tagName === "DETAILS") el.open = true;
  el.scrollIntoView({ block: "center" });
  el.classList.add("flash");
  setTimeout(() => el.classList.remove("flash"), 1600);
}

async function followHash() {
  const h = decodeURIComponent(location.hash.slice(1));
  if (h.startsWith("c=")) await openCountry(h.slice(2));
  else if (/^f\d+$/.test(h)) await reveal(h.slice(1));
  else if (h) document.getElementById(h)?.scrollIntoView();
}

// ------------------------------------------------------------ спросить

function countrySelect(name, value, emptyLabel) {
  return `<select name="${name}"><option value="">${emptyLabel}</option>` +
    COUNTRIES.map(c => `<option${c === value ? " selected" : ""}>${esc(c)}</option>`).join("") + `</select>`;
}

let ASK_BUSY = false;
function renderAsk() {
  const box = $("#ask-box");
  if (!ME) { box.innerHTML = `<a href="${esc(loginUrl())}">Войдите</a>, чтобы спросить. Читать сведения можно без входа.`; return; }
  box.innerHTML = `
    <label class="f">Ваш вопрос<textarea name="q" placeholder="Например: что нужно, чтобы продлить защиту в Германии?"></textarea></label>
    <label class="f">Страна${countrySelect("ask_country", CURRENT, "любая страна")}</label>
    <div class="btns"><button class="btn primary" id="ask-go">Спросить</button>
      ${CFG.ai ? "" : `<span class="muted">ИИ сейчас выключен — покажем сведения, где есть слова из вопроса.</span>`}</div>
    <div id="ask-out"></div>`;
  $("#ask-go").onclick = async () => {
    // двойной клик отправлял два запроса — на проде это два платных вызова (QA 28.09)
    if (ASK_BUSY) return;
    ASK_BUSY = true;
    $("#ask-go").disabled = true;
    const out = $("#ask-out");
    const q = box.querySelector("[name=q]").value.trim();
    out.innerHTML = `<p class="msg">ищу…</p>`;
    try {
      const r = await api(`/api/info/${ROOT}/ask`, { method: "POST",
        body: { question: q, country: box.querySelector("[name=ask_country]").value || null } });
      const ans = r.answer ? esc(r.answer).replace(/\[#(\d+)\]/g, '<a href="#f$1" data-go="$1">[#$1]</a>') : "";
      const list = r.facts?.length
        ? `<ul>${r.facts.map(f => `<li><a href="#f${f.id}" data-go="${f.id}">#${f.id}</a> ${esc(f.title)}${f.country ? " — " + esc(f.country) : ""}${f.city ? ", " + esc(f.city) : ""}</li>`).join("")}</ul>`
        : "";
      out.innerHTML = r.ok && ans
        ? `<div class="answer">${ans}</div>` + (list ? `<p class="muted">Сведения, по которым искали:</p>${list}` : "")
        : (list ? `<p class="msg">ИИ сейчас недоступен. Сведения, где есть слова из вашего вопроса:</p>${list}`
                : `<p class="msg">Таких сведений не нашлось. Если знаете ответ — <a href="#add">добавьте сведение</a>.</p>`);
      if (r.gaps) out.insertAdjacentHTML("beforeend", `<p class="muted">Чего не хватает: ${esc(r.gaps)}</p>`);
      out.querySelectorAll("[data-go]").forEach(a => a.addEventListener("click", (e) => {
        e.preventDefault();
        history.replaceState(null, "", "#f" + a.dataset.go);
        reveal(a.dataset.go);
      }));
    } catch (e) { out.innerHTML = `<p class="msg err">${esc(e.message)}</p>`; }
    finally { ASK_BUSY = false; $("#ask-go") && ($("#ask-go").disabled = false); }
  };
}

// ------------------------------------------------------------ добавить

function renderAdd() {
  const box = $("#add-box");
  if (!ME) { box.innerHTML = `<a href="${esc(loginUrl())}">Войдите</a>, чтобы добавить сведение.`; return; }
  box.innerHTML = `
    <div id="step1">
      <p class="muted" id="add-country-hint">${PRESET_COUNTRY ? "Страна: " + esc(PRESET_COUNTRY) : ""}</p>
      <label class="f">Что вы знаете или что с вами было<textarea name="text" placeholder="Например: 20.09 в ведомстве по делам иностранцев Лейпцига продление оформили только после е-ВОД из Резерв+."></textarea></label>
      <label class="f">Ссылка на источник, если есть<input name="url" inputmode="url" placeholder="https://…" /></label>
      <div class="btns">
        <button class="btn primary" id="intake"${CFG.ai ? "" : " hidden"}>Разложить по полям</button>
        <button class="btn${CFG.ai ? "" : " primary"}" id="manual">Заполнить вручную</button>
      </div>
      <p class="msg" id="add-msg"></p>
    </div>
    <div id="step2" hidden></div>
    <p class="msg" id="pub-msg"></p>`;
  if (FLASH) { const m = $("#pub-msg"); m.className = "msg " + FLASH.cls; m.innerHTML = FLASH.html; FLASH = null; }
  // ИИ выключен — свободный рассказ разложить некому: сразу поля (текст над
  // формой говорит то же самое)
  if (!CFG.ai) {
    $("#step1").hidden = true;
    fields({}, false);
    return;
  }
  $("#intake").onclick = async (ev) => {
    const b = ev.currentTarget; b.disabled = true;
    const text = box.querySelector("[name=text]").value.trim();
    const url = box.querySelector("[name=url]").value.trim();
    const msg = $("#add-msg");
    msg.className = "msg"; msg.textContent = "разбираю…";
    try {
      const r = await api(`/api/info/${ROOT}/intake`, { method: "POST", body: { text, url: url || null } });
      msg.textContent = r.ok
        ? "Проверьте поля — ИИ мог ошибиться." + (r.removed_personal ? " Личные данные убраны." : "") +
          (r.page_read === false ? " Страницу по ссылке открыть не удалось — выдержку вставьте сами." : "")
        : "ИИ недоступен — заполните поля сами.";
      fields(r.draft || { body: text, source_url: url });
    } catch (e) { msg.className = "msg err"; msg.textContent = e.message; }
    finally { b.disabled = false; }
  };
  $("#manual").onclick = () => fields({
    body: box.querySelector("[name=text]").value.trim(),
    source_url: box.querySelector("[name=url]").value.trim() });
}

function fields(d, scroll = true) {
  const s2 = $("#step2");
  const opt = (arr, val) => arr.map(([k, t]) => `<option value="${k}"${k === val ? " selected" : ""}>${esc(t)}</option>`).join("");
  const kind = d.kind || (d.source_url ? "report" : "experience");
  s2.hidden = false;
  s2.innerHTML = `
    <label class="f">Утверждение — одной фразой, что именно известно<input name="title" maxlength="200" value="${esc(d.title || "")}" /></label>
    <div class="grid2">
      <label class="f">Вид<select name="kind">${opt([
        ["experience", "опыт людей — так было со мной или со знакомыми"],
        ["report", "сообщают — пересказ СМИ, юриста, организации (нужна ссылка)"],
        ["norm", "норма — текст закона или ведомства (нужны ссылка и выдержка)"]], kind)}</select></label>
      <label class="f">Раздел — выберите или впишите свой<input name="section" list="info-sections" maxlength="60" value="${esc(d.section || "")}" placeholder="например, ${esc(VIEW.suggested_sections.find(x => !/^Закон|^Права/.test(x)) || "Масштаб")}" />
        <datalist id="info-sections">${VIEW.suggested_sections.map(x => `<option value="${esc(x)}">`).join("")}</datalist></label>
      <label class="f">Страна (для опыта людей — обязательно)${countrySelect("country", d.country || PRESET_COUNTRY || CURRENT, "— для всех (ЕС и международное право)")}</label>
      <label class="f">Город (если так только в этом городе)<input name="city" maxlength="80" value="${esc(d.city || "")}" /></label>
      <label class="f">Ведомство<input name="office" maxlength="200" value="${esc(d.office || "")}" placeholder="например, ведомство по делам иностранцев (ABH)" /></label>
      <label class="f">Кого касается<input name="applies_to" maxlength="200" value="${esc(d.applies_to || "")}" placeholder="например, мужчины 18–60" /></label>
      <label class="f">Когда<input name="when_text" maxlength="100" value="${esc(d.when_text || "")}" placeholder="например, 20.09.2026 или с 01.10.2026" /></label>
      <label class="f">Ссылка<input name="source_url" inputmode="url" value="${esc(d.source_url || "")}" /></label>
    </div>
    <label class="f">Выдержка из источника — дословно, на языке оригинала<textarea name="source_quote" maxlength="2000">${esc(d.source_quote || "")}</textarea></label>
    <label class="f">Пояснение (необязательно)<textarea name="body" maxlength="4000">${esc(d.body || "")}</textarea></label>
    <div class="btns"><button class="btn primary" id="publish">Опубликовать</button></div>
    <p class="msg" id="fields-msg"></p>`;
  $("#publish").onclick = async (ev) => {
    const b = ev.currentTarget;
    const get = (n) => (s2.querySelector(`[name=${n}]`).value || "").trim() || null;
    const body = Object.fromEntries(["title", "section", "kind", "country", "city", "office",
      "applies_to", "when_text", "source_url", "source_quote", "body"].map(n => [n, get(n)]));
    const msg = $("#fields-msg");
    if (!body.title) { msg.className = "msg err"; msg.textContent = "Напишите утверждение — одной фразой, что известно."; return; }
    if (body.kind === "experience" && !body.country) { msg.className = "msg err"; msg.textContent = "Опыт людей всегда в какой-то стране — выберите страну."; return; }
    b.disabled = true;
    try {
      const r = await api(`/api/info/${ROOT}/facts`, { method: "POST", body });
      const st = { verified: "Цитата найдена на странице дословно.",
        mismatch: "Такой выдержки на странице нет — это видно на карточке.",
        unreachable: "Сайт не пустил автоматическую проверку — это видно на карточке.",
        unchecked: "Цитата ещё не сверена.", none: "" }[r.quote_status] || "";
      const downgraded = body.kind === "norm" && r.kind !== "norm"
        ? " Цитату не удалось сверить, поэтому сведение показано как «сообщают»." : "";
      // сообщение переживает перерисовку страницы (UX 28.09, М3)
      FLASH = { cls: "ok", html: `Опубликовано: <a href="#f${r.id}" data-go="${r.id}">#${r.id}</a>. ${esc(st + downgraded)}` };
      PRESET_COUNTRY = null;
      await reload();
      await reveal(String(r.id));
      $("#pub-msg [data-go]")?.addEventListener("click", (e) => { e.preventDefault(); reveal(String(r.id)); });
    } catch (e) { msg.className = "msg err"; msg.textContent = e.message; b.disabled = false; }
  };
  if (scroll) s2.scrollIntoView({ behavior: "smooth", block: "start" });
}

// ------------------------------------------------------------ старт

(async () => {
  try { CFG = await api("/api/config"); } catch { CFG = {}; }
  try { ME = await api("/api/auth/me"); } catch { ME = null; }
  const who = $("#who");
  if (ME) { who.textContent = ME.name || ME.username; who.href = "/profile.html"; }
  else { who.href = loginUrl(); }
  try { COUNTRIES = (await api("/api/taxonomy")).countries; } catch { COUNTRIES = []; }
  try { VIEW = await api(`/api/info/${ROOT}`); }
  catch (e) { $("main").innerHTML = `<p class="msg err">${esc(e.message)}</p>`; return; }
  document.title = "Noosphere — сведения: " + VIEW.topic.title;
  render();
  await followHash();
  // ссылки [#id] и «назад/вперёд» внутри страницы (QA 28.09)
  window.addEventListener("hashchange", followHash);
})();
