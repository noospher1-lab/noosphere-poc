// Информационный сектор (vault: drafts/2026-09-28-info-sector).
// Порядок чтения задал Alex 28.09: сначала суть для всех — закон, на котором
// основаны действия стран, и законы о правах и равенстве; потом страна; потом
// город, если там своя практика. Карта — вход в страну.
// Мы не консультируем, а собираем доступное: у сведения нет вердикта, есть то,
// на чём оно держится (сверенная цитата или число независимых подтверждений).
"use strict";

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const $ = (sel, root = document) => root.querySelector(sel);
const qs = new URLSearchParams(location.search);
const SLUG = qs.get("s") || "ua-eu";

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

let ME = null, CFG = {}, VIEW = null, COUNTRIES = [];
const loginUrl = () => "/?login&next=" + encodeURIComponent(location.pathname + location.search + location.hash);

function plural(n, one, few, many) {
  const m10 = n % 10, m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
  return many;
}
const dateRu = (iso) => iso ? new Date(iso).toLocaleDateString("ru-RU") : "";

const KIND = {
  norm: ["норма", "текст закона или официального документа"],
  practice: ["практика", "так было с людьми в ведомстве"],
  unverified: ["не проверено", "без источника или с чужих слов"],
};
const SECTION_ORDER = ["basis", "rights", "protection", "residence", "court", "other"];
const SECTION_HINT = {
  basis: "На чём основаны действия стран: решения ЕС и национальные акты.",
  rights: "Нормы о равенстве и правах, на которые можно ссылаться, если требование кажется дискриминацией.",
  protection: "Как продлить временную защиту и что для этого требуют.",
  residence: "Как перейти с временной защиты на вид на жительство.",
  court: "Как и где оспаривать решения, сроки и примеры дел.",
  other: "",
};

// ------------------------------------------------------------ карточка

function quoteStatus(f) {
  if (!f.source_url) return "";
  if (f.stale) return `<span class="qs stale" title="Давно не сверялось — страница могла измениться">⟳ пора перепроверить (сверено ${dateRu(f.checked_at)})</span>`;
  return {
    verified: `<span class="qs verified" title="Сервер открыл страницу и нашёл выдержку дословно">✓ цитата сверена ${dateRu(f.checked_at)}</span>`,
    mismatch: `<span class="qs mismatch" title="На странице этой выдержки нет — возможно, текст изменился">✗ цитаты на странице нет</span>`,
    unreachable: `<span class="qs unreachable" title="Сайт не пускает автоматическую проверку или это PDF — сверьте сами по ссылке">? сверить автоматически не удалось</span>`,
    unchecked: `<span class="qs unchecked">? ещё не сверено</span>`,
    none: "",
  }[f.quote_status] || "";
}

function factCard(f) {
  const where = [f.country || "ЕС и международное право", f.city, f.office].filter(Boolean).join(" · ");
  const extra = [f.applies_to && "кого касается: " + f.applies_to, f.when_text && "когда: " + f.when_text]
    .filter(Boolean).join(" · ");
  const src = f.source_url
    ? `<a href="${esc(f.source_url)}" target="_blank" rel="noopener nofollow">${esc(f.source_title || new URL(f.source_url).hostname)}</a>`
    : `<span>без ссылки</span>`;
  const own = ME && f.author && f.author.id === ME.id;
  const indNote = (n, ind) => n && ind !== n ? ` <span class="ind" title="Часть отметок похожа на один аккаунт-владелец — считаются за одну">(независимых ${ind})</span>` : "";
  const reports = own ? `<span>своё сведение подтверждают другие</span>` : `
      <button class="rep same" data-v="same" aria-pressed="${f.mine === "same"}" title="Со мной было так же / у меня есть такой же документ">у меня так же · ${f.same}${indNote(f.same, f.same_independent)}</button>
      <button class="rep differs" data-v="differs" aria-pressed="${f.mine === "differs"}" title="У меня было иначе">у меня иначе · ${f.differs}${indNote(f.differs, f.differs_independent)}</button>`;
  const notes = (f.notes || []).length ? `<ul class="notes">${f.notes.map(n =>
      `<li>${n.verdict === "same" ? "так же" : "иначе"}, ${dateRu(n.at)}: ${esc(n.note)}</li>`).join("")}</ul>` : "";
  return `
  <article class="fact" id="f${f.id}" data-id="${f.id}">
    <div class="head">
      <span class="kind ${f.kind}" title="${esc(KIND[f.kind][1])}">${KIND[f.kind][0]}</span>
      <div>
        <div class="title">${esc(f.title)}</div>
        <div class="where">${esc(where)}${extra ? " · " + esc(extra) : ""}</div>
      </div>
    </div>
    ${f.body ? `<div class="body">${esc(f.body)}</div>` : ""}
    ${f.source_quote ? `<blockquote>${esc(f.source_quote)}</blockquote>` : ""}
    <div class="src">источник: ${src} ${quoteStatus(f)}</div>
    <div class="foot">
      ${reports}
      <span class="spacer"></span>
      <span>внёс ${esc(f.author?.name || "—")}, ${dateRu(f.created_at)}</span>
      <a href="#f${f.id}" title="Ссылка на это сведение">#${f.id}</a>
      ${own ? `<button class="linkbtn rm">снять</button>` : ""}
    </div>
    ${notes}
  </article>`;
}

function bySection(facts, withHints) {
  const out = [];
  for (const s of SECTION_ORDER) {
    const list = facts.filter(f => f.section === s);
    if (!list.length) continue;
    const name = VIEW.sections.find(x => x.id === s)?.name || s;
    out.push(`<h3 id="sec-${s}">${esc(name)}</h3>` +
      (withHints && SECTION_HINT[s] ? `<p class="muted">${esc(SECTION_HINT[s])}</p>` : "") +
      list.map(factCard).join(""));
  }
  return out.join("") || `<p class="empty">Пока ничего нет.</p>`;
}

// ------------------------------------------------------------ карта

function mapSvg(counts) {
  if (typeof INFO_MAP === "undefined") return "";
  const max = Math.max(1, ...Object.values(counts));
  const lvl = (n) => n >= max * 0.66 ? " l3" : n >= max * 0.33 ? " l2" : "";
  const parts = [];
  for (const [name, c] of Object.entries(INFO_MAP.countries)) {
    const n = counts[name] || 0;
    const cls = n ? "has" + lvl(n) : "";
    const t = `<title>${esc(name)}${n ? ": " + n + " " + plural(n, "сведение", "сведения", "сведений") : ""}</title>`;
    if (c.d) parts.push(`<path d="${c.d}" class="${cls}" data-c="${esc(name)}">${t}</path>`);
    if (c.dot) parts.push(`<circle cx="${c.c[0]}" cy="${c.c[1]}" r="6" class="${n ? "has" : ""}" data-c="${esc(name)}">${t}</circle>`);
  }
  return `<svg viewBox="0 0 ${INFO_MAP.w} ${INFO_MAP.h}" role="img" aria-label="Карта Европы: где есть сведения">${parts.join("")}</svg>`;
}

// ------------------------------------------------------------ страница

function render() {
  const v = VIEW;
  const counts = Object.fromEntries(v.countries.map(c => [c.country, c.facts]));
  const europe = new Set(Object.keys(INFO_MAP?.countries || {}));
  const listed = [...new Set([...v.countries.map(c => c.country)])].sort((a, b) => a.localeCompare(b, "ru"));
  const outside = listed.filter(c => !europe.has(c));
  const problem = v.sector.problem_id
    ? `<a href="/n/${v.sector.problem_id}">обсуждение: законно ли это требование →</a>` : "";
  $("main").innerHTML = `
    <h1>${esc(v.sector.title)}</h1>
    ${v.sector.intro ? `<p class="lead">${esc(v.sector.intro)}</p>` : ""}
    <div class="note">Здесь не консультируют. Здесь собрано то, что доступно: тексты законов со ссылками
      и что происходит с людьми в ведомствах. Как этим распорядиться — решаете вы. У каждого сведения
      видно, на чём оно держится.
      <div class="legend">
        <span><span class="kind norm">норма</span> текст закона, цитата сверена с источником</span>
        <span><span class="kind practice">практика</span> так было с людьми — смотрите число подтверждений</span>
        <span><span class="kind unverified">не проверено</span> без источника или с чужих слов</span>
      </div>
    </div>
    <nav class="toc">
      <a href="#common">Суть — для всех</a>
      <a href="#countries">По странам</a>
      <a href="#ask">Спросить</a>
      <a href="#add">+ Добавить сведение</a>
      ${problem ? `<a href="/n/${v.sector.problem_id}">Обсуждение</a>` : ""}
    </nav>

    <h2 id="common">Суть — для всех, независимо от страны</h2>
    <p class="muted">Сначала закон, на котором основаны действия стран, и нормы о правах и равенстве;
      потом общие пути. Что делает конкретная страна или город — ниже, по карте.</p>
    ${bySection(v.common, true)}

    <h2 id="countries">По странам</h2>
    <p class="muted">${v.countries.length} ${plural(v.countries.length, "страна", "страны", "стран")} со сведениями · всего ${v.total} ${plural(v.total, "сведение", "сведения", "сведений")}.
      Нажмите на страну на карте или в списке.</p>
    <div class="mapwrap">
      <div class="map">${mapSvg(counts)}
        <div class="maplegend">Чем ярче синий, тем больше сведений. Серым — пока ничего нет: если знаете, добавьте.</div></div>
      <ul class="clist">
        ${listed.map(c => `<li><button data-c="${esc(c)}"><span>${esc(c)}</span><span class="n">${counts[c]}</span></button></li>`).join("")}
        ${outside.length ? "" : ""}
      </ul>
    </div>
    <section id="country-panel"></section>

    <h2 id="ask">Спросить по сведениям</h2>
    <p class="muted">ИИ отвечает только тем, что собрано здесь, и к каждой фразе даёт номер сведения.
      Своих знаний он не добавляет; если сведений нет, так и скажет.</p>
    <div class="card" id="ask-box"></div>

    <h2 id="add">Добавить сведение</h2>
    <p class="muted">Расскажите своими словами, что знаете или что с вами было. Имена, адреса и номера
      документов не пишите. ИИ разложит текст по полям, вы проверите и опубликуете. Ссылку сервер
      откроет сам и проверит, есть ли там ваша выдержка дословно.</p>
    <div class="card" id="add-box"></div>`;

  bindFacts($("main"));
  $("main").querySelectorAll("[data-c]").forEach(el =>
    el.addEventListener("click", () => openCountry(el.dataset.c)));
  renderAsk();
  renderAdd();
  const h = decodeURIComponent(location.hash.slice(1));
  if (h.startsWith("c=")) openCountry(h.slice(2), false);
  else if (h) document.getElementById(h)?.scrollIntoView();
}

async function openCountry(name, scroll = true) {
  const box = $("#country-panel");
  document.querySelectorAll(".map .sel").forEach(e => e.classList.remove("sel"));
  document.querySelectorAll(`.map [data-c="${CSS.escape(name)}"]`).forEach(e => e.classList.add("sel"));
  document.querySelectorAll(".clist button").forEach(b => b.setAttribute("aria-current", b.dataset.c === name));
  history.replaceState(null, "", "#c=" + encodeURIComponent(name));
  let v;
  try { v = await api(`/api/info/${SLUG}/country/${encodeURIComponent(name)}`); }
  catch (e) { box.innerHTML = `<p class="msg err">${esc(e.message)}</p>`; return; }
  const n = v.national.length + v.cities.reduce((s, c) => s + c.facts.length, 0);
  box.innerHTML = `<div class="country">
      <h2>${esc(name)}</h2>
      ${n ? "" : `<p class="empty">По этой стране сведений пока нет. Общие правила ЕС — выше. Если знаете, как здесь продлевают защиту или переходят на ВНЖ, <a href="#add">добавьте</a>.</p>`}
      ${v.national.length ? bySection(v.national, false) : ""}
      ${v.cities.map(c => `<div class="city"><h2>${esc(c.city)}</h2>${bySection(c.facts, false)}</div>`).join("")}
      <div class="btns"><button class="btn" data-add-country="${esc(name)}">+ Добавить сведение по стране «${esc(name)}»</button></div>
    </div>`;
  bindFacts(box);
  box.querySelector("[data-add-country]").onclick = () => {
    const sel = $("#add-box select[name=country]");
    if (sel) sel.value = name;
    $("#add").scrollIntoView({ behavior: "smooth" });
  };
  if (scroll) box.scrollIntoView({ behavior: "smooth" });
}

function bindFacts(root) {
  root.querySelectorAll(".fact").forEach(card => {
    const id = card.dataset.id;
    card.querySelectorAll(".rep").forEach(b => b.addEventListener("click", async () => {
      if (!ME) { location.href = loginUrl(); return; }
      const on = b.getAttribute("aria-pressed") === "true";
      let note = null;
      if (!on) {
        note = prompt(b.dataset.v === "same"
          ? "Коротко: где и когда было так же? (можно пропустить; без имён и номеров документов)"
          : "Коротко: как было у вас? (можно пропустить; без имён и номеров документов)", "");
        if (note === null) return;
      }
      try {
        await api(`/api/info/facts/${id}/report`, { method: "POST",
          body: { verdict: on ? null : b.dataset.v, note: note || null } });
        await reload(card.id);
      } catch (e) { alert(e.message); }
    }));
    const rm = card.querySelector(".rm");
    if (rm) rm.onclick = async () => {
      if (!confirm("Снять сведение? Оно исчезнет для всех.")) return;
      try { await api(`/api/info/facts/${id}`, { method: "DELETE" }); await reload(); }
      catch (e) { alert(e.message); }
    };
  });
}

async function reload(anchor) {
  const hash = location.hash;
  VIEW = await api(`/api/info/${SLUG}`);
  render();
  if (hash.startsWith("#c=")) await openCountry(decodeURIComponent(hash.slice(3)), false);
  if (anchor) document.getElementById(anchor)?.scrollIntoView({ block: "center" });
}

// ------------------------------------------------------------ спросить

function countrySelect(name, value) {
  return `<select name="${name}"><option value="">${name === "country" ? "ЕС / для всех" : "любая страна"}</option>` +
    COUNTRIES.map(c => `<option${c === value ? " selected" : ""}>${esc(c)}</option>`).join("") + `</select>`;
}

function renderAsk() {
  const box = $("#ask-box");
  if (!ME) { box.innerHTML = `<a href="${esc(loginUrl())}">Войдите</a>, чтобы спросить. Читать сведения можно без входа.`; return; }
  box.innerHTML = `
    <label class="f">Ваш вопрос<textarea name="q" placeholder="Например: что нужно, чтобы продлить защиту в Германии, если Резерв+ не обновлён?"></textarea></label>
    <label class="f">Страна${countrySelect("ask_country")}</label>
    <div class="btns"><button class="btn primary" id="ask-go">Спросить</button>
      ${CFG.ai ? "" : `<span class="muted">ИИ на этом стенде выключен — покажем найденные сведения без пересказа.</span>`}</div>
    <div id="ask-out"></div>`;
  $("#ask-go").onclick = async () => {
    const out = $("#ask-out");
    const q = box.querySelector("[name=q]").value.trim();
    out.innerHTML = `<p class="msg">ищу…</p>`;
    try {
      const r = await api(`/api/info/${SLUG}/ask`, { method: "POST",
        body: { question: q, country: box.querySelector("[name=ask_country]").value || null } });
      const ans = r.answer ? esc(r.answer).replace(/\[#(\d+)\]/g, '<a href="#f$1">[#$1]</a>') : "";
      out.innerHTML = (r.ok && ans ? `<div class="answer">${ans}</div>` :
          `<p class="msg">ИИ сейчас недоступен. Вот сведения, которые подходят по словам:</p>`) +
        (r.facts?.length ? `<ul>${r.facts.map(f => `<li><a href="#f${f.id}" data-go="${f.id}">#${f.id}</a> ${esc(f.title)}${f.country ? " — " + esc(f.country) : ""}${f.city ? ", " + esc(f.city) : ""}</li>`).join("")}</ul>` : "") +
        (r.gaps ? `<p class="muted">Чего не хватает: ${esc(r.gaps)}</p>` : "");
      out.querySelectorAll("[data-go]").forEach(a => a.addEventListener("click", async (e) => {
        const f = document.getElementById("f" + a.dataset.go);
        if (!f) {
          e.preventDefault();
          const fact = r.facts.find(x => String(x.id) === a.dataset.go);
          if (fact?.country) { await openCountry(fact.country); document.getElementById("f" + a.dataset.go)?.scrollIntoView({ block: "center" }); }
        }
      }));
    } catch (e) { out.innerHTML = `<p class="msg err">${esc(e.message)}</p>`; }
  };
}

// ------------------------------------------------------------ добавить

function renderAdd() {
  const box = $("#add-box");
  if (!ME) { box.innerHTML = `<a href="${esc(loginUrl())}">Войдите</a>, чтобы добавить сведение.`; return; }
  box.innerHTML = `
    <div id="step1">
      <label class="f">Что вы знаете или что с вами было<textarea name="text" placeholder="Например: 20.09 в ведомстве по делам иностранцев Лейпцига продление защиты оформили только после выписки из Резерв+."></textarea></label>
      <label class="f">Ссылка на источник, если есть<input name="url" placeholder="https://…" /></label>
      <div class="btns">
        <button class="btn primary" id="intake">Разложить по полям</button>
        <button class="btn" id="manual">Заполнить вручную</button>
        ${CFG.ai ? "" : `<span class="muted">ИИ на этом стенде выключен — поля заполните сами.</span>`}
      </div>
    </div>
    <div id="step2" hidden></div>
    <p class="msg" id="add-msg"></p>`;
  $("#intake").onclick = async () => {
    const text = box.querySelector("[name=text]").value.trim();
    const url = box.querySelector("[name=url]").value.trim();
    const msg = $("#add-msg");
    msg.className = "msg"; msg.textContent = "разбираю…";
    try {
      const r = await api(`/api/info/${SLUG}/intake`, { method: "POST", body: { text, url: url || null } });
      msg.textContent = r.ok
        ? "Проверьте поля — ИИ мог ошибиться." + (r.removed_personal ? " Личные данные убраны." : "") +
          (r.page_read === false ? " Страницу по ссылке открыть не удалось — выдержку вставьте сами." : "")
        : "ИИ недоступен — заполните поля сами.";
      fields(r.draft || { body: text, source_url: url });
    } catch (e) { msg.className = "msg err"; msg.textContent = e.message; }
  };
  $("#manual").onclick = () => fields({
    body: box.querySelector("[name=text]").value.trim(),
    source_url: box.querySelector("[name=url]").value.trim() });
}

function fields(d) {
  const s2 = $("#step2");
  const opt = (arr, val) => arr.map(([k, t]) => `<option value="${k}"${k === val ? " selected" : ""}>${esc(t)}</option>`).join("");
  s2.hidden = false;
  s2.innerHTML = `
    <label class="f">Утверждение — одной фразой, что именно известно<input name="title" value="${esc(d.title || "")}" /></label>
    <div class="grid2">
      <label class="f">Раздел<select name="section">${opt(VIEW.sections.map(s => [s.id, s.name]), d.section || "protection")}</select></label>
      <label class="f">Вид<select name="kind">${opt([["practice", "практика — так было со мной/с людьми"], ["norm", "норма — текст закона (нужны ссылка и выдержка)"], ["unverified", "не проверено — с чужих слов"]], d.kind || "practice")}</select></label>
      <label class="f">Страна${countrySelect("country", d.country)}</label>
      <label class="f">Город (если практика городская)<input name="city" value="${esc(d.city || "")}" /></label>
      <label class="f">Ведомство<input name="office" value="${esc(d.office || "")}" placeholder="например, ведомство по делам иностранцев (ABH)" /></label>
      <label class="f">Кого касается<input name="applies_to" value="${esc(d.applies_to || "")}" placeholder="например, мужчины 18–60" /></label>
      <label class="f">Когда<input name="when_text" value="${esc(d.when_text || "")}" placeholder="например, 20.09.2026 или с 01.10.2026" /></label>
      <label class="f">Ссылка<input name="source_url" value="${esc(d.source_url || "")}" /></label>
    </div>
    <label class="f">Выдержка из источника — дословно, на языке оригинала<textarea name="source_quote">${esc(d.source_quote || "")}</textarea></label>
    <label class="f">Пояснение (необязательно)<textarea name="body">${esc(d.body || "")}</textarea></label>
    <div class="btns"><button class="btn primary" id="publish">Опубликовать</button></div>`;
  $("#publish").onclick = async (ev) => {
    const b = ev.currentTarget; b.disabled = true;
    const get = (n) => (s2.querySelector(`[name=${n}]`).value || "").trim() || null;
    const body = Object.fromEntries(["title", "section", "kind", "country", "city", "office",
      "applies_to", "when_text", "source_url", "source_quote", "body"].map(n => [n, get(n)]));
    const msg = $("#add-msg");
    try {
      const r = await api(`/api/info/${SLUG}/facts`, { method: "POST", body });
      const st = { verified: "цитата найдена на странице дословно", mismatch: "выдержки на странице нет — сведение помечено",
        unreachable: "страницу не удалось открыть автоматически", none: "без источника" }[r.quote_status] || "";
      msg.className = "msg ok";
      msg.textContent = `Опубликовано (#${r.id}). ${st}.` + (body.kind === "norm" && r.kind !== "norm" ? " Норма без сверенной цитаты показывается как «не проверено»." : "");
      await reload("f" + r.id);
      if (body.country && !document.getElementById("f" + r.id)) await openCountry(body.country);
      document.getElementById("f" + r.id)?.scrollIntoView({ block: "center" });
    } catch (e) { msg.className = "msg err"; msg.textContent = e.message; b.disabled = false; }
  };
  s2.scrollIntoView({ behavior: "smooth", block: "start" });
}

// ------------------------------------------------------------ старт

(async () => {
  try { CFG = await api("/api/config"); } catch { CFG = {}; }
  try { ME = await api("/api/auth/me"); } catch { ME = null; }
  const who = $("#who");
  if (ME) { who.textContent = ME.name || ME.username; who.href = "/profile.html"; }
  else { who.href = loginUrl(); }
  try { COUNTRIES = (await api("/api/taxonomy")).countries; } catch { COUNTRIES = []; }
  try { VIEW = await api(`/api/info/${SLUG}`); }
  catch (e) { $("main").innerHTML = `<p class="msg err">${esc(e.message)}</p>`; return; }
  document.title = "Noosphere — " + VIEW.sector.title;
  render();
})();
