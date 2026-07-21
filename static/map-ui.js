// Управление экраном для всех вариантов карты (map-v1 / v2 / v3).
//
// Два независимых режима, потому что это разные потребности:
//   • полный экран — убрать хром браузера (Fullscreen API);
//   • «чистый режим» — убрать НАШИ панели, отдав максимум площади карте.
// Их часто путают, но нужны они по отдельности: на большом мониторе
// чистый режим полезен и без фуллскрина, а на проекторе — наоборот.
//
// Каждая страница сама решает, что прячет чистый режим: она описывает это
// правилами для body.lean. Здесь только переключение и уведомление о смене
// размера — canvas обязан перерисоваться, иначе останется в старом разрешении.

// ============================================================ ФИЛЬТРЫ
// Общий компонент для всех трёх вариантов: 196 стран и 120 подветвей нельзя
// показать плоским списком чипов — получится стена. Поэтому дерево, свёрнутое
// по умолчанию, с поиском внутри фасета и счётчиками.
//
// Счётчик считается БЕЗ учёта своей же группы: иначе, выбрав «Германия», все
// остальные страны показали бы 0, и фасет стал бы тупиком. Видно, сколько
// будет, ДО клика.

const SEL = { domains:new Set(), subs:new Set(), geo:new Set(), tags:new Set() };

// Пустые рубрики показываем ВСЕГДА. Страна без единой темы — это не мусор,
// а честное «здесь пока не написано»: список стран должен быть полным, иначе
// по нему нельзя понять, чего не хватает, и некуда целиться первым автором.
const showEmpty = true;

function matchTopic(t, skip) {
  if (skip !== "dom" && (SEL.domains.size || SEL.subs.size)) {
    if (!(SEL.domains.has(t.domain) || SEL.subs.has(t.sub))) return false;
  }
  if (skip !== "geo" && SEL.geo.size) {
    let ok = false;
    for (const g of SEL.geo) if (t._geo.has(g)) { ok = true; break; }
    if (!ok) return false;
  }
  if (skip !== "tag" && SEL.tags.size) {
    if (!t.tags.some(x => SEL.tags.has(x))) return false;
  }
  if (typeof QUERY === "string" && QUERY) {
    const hay = (t.title + " " + t.sub + " " + t.tags.join(" ") + " " +
                 t.geo.join(" ") + " " + DOM_BY_ID[t.domain].name).toLowerCase();
    if (!hay.includes(QUERY)) return false;
  }
  return true;
}
let QUERY = "";
function setQuery(q) { QUERY = (q || "").trim().toLowerCase(); }
function selectionCount() {
  return SEL.domains.size + SEL.subs.size + SEL.geo.size + SEL.tags.size;
}
function clearSelection() {
  SEL.domains.clear(); SEL.subs.clear(); SEL.geo.clear(); SEL.tags.clear();
}

function countBy(skip, pred) {
  let n = 0;
  for (const t of MAP_TOPICS) if (matchTopic(t, skip) && pred(t)) n++;
  return n;
}

// Разворот запоминается между перерисовками — иначе клик по стране схлопывал
// бы дерево, и до второй страны пришлось бы идти заново.
const OPEN = new Set();

function buildFacets(host, onChange) {
  host.innerHTML = `
    <div class="fx-head">
      <input type="search" class="fx-find" placeholder="Найти фильтр…" />
      <span class="fx-clear">сбросить</span>
    </div>
    <div class="fx-body"></div>`;

  const find = host.querySelector(".fx-find");
  const body = host.querySelector(".fx-body");
  let needle = "";

  const hit = s => !needle || s.toLowerCase().includes(needle);

  function row({ label, count, on, depth, color, openable, opened, onToggle, onOpen }) {
    const d = document.createElement("div");
    d.className = "fx-row" + (on ? " on" : "") + (count ? "" : " zero");
    d.style.paddingLeft = (8 + depth * 13) + "px";
    d.innerHTML =
      (openable ? `<span class="fx-caret">${opened ? "▾" : "▸"}</span>`
                : `<span class="fx-caret"></span>`) +
      (color ? `<span class="fx-dot" style="background:${color}"></span>` : "") +
      `<span class="fx-nm">${label}</span><span class="fx-ct">${count}</span>`;
    d.querySelector(".fx-caret").onclick = e => {
      if (!openable) return;
      e.stopPropagation(); onOpen();
    };
    d.onclick = onToggle;
    return d;
  }

  function render() {
    body.innerHTML = "";

    // ---- направления: домен → подветви
    const g1 = document.createElement("div");
    g1.className = "fx-group";
    g1.innerHTML = `<h3>Направления</h3>`;
    for (const d of DOMAINS) {
      const n = countBy("dom", t => t.domain === d.id);
      const subsHit = d.subs.filter(s => hit(s));
      const self = hit(d.name);
      if (!self && !subsHit.length) continue;
      if (!n && !showEmpty && !needle) continue;
      const key = "d:" + d.id, opened = OPEN.has(key) || (needle && subsHit.length);
      g1.appendChild(row({
        label:d.name, count:n, on:SEL.domains.has(d.id), depth:0, color:d.color,
        openable:true, opened,
        onToggle:() => { SEL.domains.has(d.id) ? SEL.domains.delete(d.id) : SEL.domains.add(d.id); render(); onChange(); },
        onOpen:() => { OPEN.has(key) ? OPEN.delete(key) : OPEN.add(key); render(); },
      }));
      if (opened) for (const s of (needle ? subsHit : d.subs)) {
        const cn = countBy("dom", t => t.sub === s);
        if (!cn && !showEmpty && !needle) continue;
        g1.appendChild(row({
          label:s, count:cn, on:SEL.subs.has(s), depth:1,
          onToggle:() => { SEL.subs.has(s) ? SEL.subs.delete(s) : SEL.subs.add(s); render(); onChange(); },
        }));
      }
    }
    body.appendChild(g1);

    // ---- география: союзы + часть света → регион → страна
    const g2 = document.createElement("div");
    g2.className = "fx-group";
    g2.innerHTML = `<h3>География</h3>`;
    for (const u of GEO_UNIONS) {
      if (!hit(u.name)) continue;
      const n = countBy("geo", t => t._geo.has(u.name));
      if (!n && !showEmpty && !needle) continue;
      g2.appendChild(row({
        label:u.name, count:n, on:SEL.geo.has(u.name), depth:0,
        onToggle:() => { SEL.geo.has(u.name) ? SEL.geo.delete(u.name) : SEL.geo.add(u.name); render(); onChange(); },
      }));
    }
    for (const c of GEO_TREE) {
      const inner = c.regions.flatMap(r => r.countries);
      const anyHit = hit(c.name) || c.regions.some(r => hit(r.name)) || inner.some(x => hit(x));
      if (!anyHit) continue;
      const n = countBy("geo", t => t._geo.has(c.name));
      if (!n && !showEmpty && !needle) continue;
      const key = "c:" + c.id, opened = OPEN.has(key) || (needle && anyHit && !hit(c.name));
      g2.appendChild(row({
        label:c.name, count:n, on:SEL.geo.has(c.name), depth:0, openable:true, opened,
        onToggle:() => { SEL.geo.has(c.name) ? SEL.geo.delete(c.name) : SEL.geo.add(c.name); render(); onChange(); },
        onOpen:() => { OPEN.has(key) ? OPEN.delete(key) : OPEN.add(key); render(); },
      }));
      if (!opened) continue;
      for (const r of c.regions) {
        const rHit = hit(r.name) || r.countries.some(x => hit(x));
        if (!rHit) continue;
        const rn = countBy("geo", t => t._geo.has(r.name));
        if (!rn && !showEmpty && !needle) continue;
        const rkey = "r:" + r.id, ropen = OPEN.has(rkey) || (needle && r.countries.some(hit));
        g2.appendChild(row({
          label:r.name, count:rn, on:SEL.geo.has(r.name), depth:1, openable:true, opened:ropen,
          onToggle:() => { SEL.geo.has(r.name) ? SEL.geo.delete(r.name) : SEL.geo.add(r.name); render(); onChange(); },
          onOpen:() => { OPEN.has(rkey) ? OPEN.delete(rkey) : OPEN.add(rkey); render(); },
        }));
        if (!ropen) continue;
        for (const cn of r.countries) {
          if (!hit(cn)) continue;
          const k = countBy("geo", t => t._geo.has(cn));
          if (!k && !showEmpty && !needle) continue;
          g2.appendChild(row({
            label:cn, count:k, on:SEL.geo.has(cn), depth:2,
            onToggle:() => { SEL.geo.has(cn) ? SEL.geo.delete(cn) : SEL.geo.add(cn); render(); onChange(); },
          }));
        }
      }
    }
    body.appendChild(g2);

    // ---- теги: их сотни, поэтому только верхние + поиск
    const g3 = document.createElement("div");
    g3.className = "fx-group";
    g3.innerHTML = `<h3>Теги</h3>`;
    const tagCounts = ALL_TAGS
      .filter(hit)
      .map(x => ({ x, n: countBy("tag", t => t.tags.includes(x)) }))
      .filter(o => o.n || showEmpty || needle)
      .sort((a, b) => b.n - a.n);
    (needle ? tagCounts : tagCounts.slice(0, 14)).forEach(o => {
      g3.appendChild(row({
        label:"#" + o.x, count:o.n, on:SEL.tags.has(o.x), depth:0,
        onToggle:() => { SEL.tags.has(o.x) ? SEL.tags.delete(o.x) : SEL.tags.add(o.x); render(); onChange(); },
      }));
    });
    if (!needle && tagCounts.length > 14) {
      const more = document.createElement("div");
      more.className = "fx-more";
      more.textContent = `ещё ${tagCounts.length - 14} тегов — ищи по названию`;
      g3.appendChild(more);
    }
    body.appendChild(g3);
  }

  find.addEventListener("input", e => { needle = e.target.value.trim().toLowerCase(); render(); });
  host.querySelector(".fx-clear").onclick = () => {
    clearSelection(); find.value = ""; needle = ""; render(); onChange();
  };

  render();
  return { render };
}

// ============================================================ ПЕРЕКЛЮЧАТЕЛЬ
// Переход между вариантами прямо из шапки, без возврата на map.html.
// Режим данных переносится вместе с переходом: демо, потерянное на первом же
// переключении, сделало бы сравнение вариантов невозможным — они показывали бы
// разное, и разница читалась бы как разница навигации.

const SCREENS = [
  { file:"map-v1.html", short:"пейзаж",  title:"Карта по направлениям" },
  { file:"map-v2.html", short:"каталог", title:"Список с фильтрами" },
  { file:"map-v3.html", short:"связи",   title:"Граф связей по тегам" },
];

function mountNav(host) {
  const here = location.pathname.split("/").pop() || "map-v1.html";
  const q = DEMO ? "?demo=1" : "";
  host.innerHTML =
    `<span class="seg">` +
    SCREENS.map(s =>
      s.file === here
        ? `<b class="seg-i on" title="${s.title}">${s.short}</b>`
        : `<a class="seg-i" href="/${s.file}${q}" title="${s.title}">${s.short}</a>`
    ).join("") +
    `</span><a class="seg-out" href="/" title="Дерево обсуждений — там пишут">обсуждения →</a>`;
}

// ============================================================ СОЗДАНИЕ ТЕМЫ
// Форма живёт прямо на карте: человек видит, где пусто, и заводит тему туда же.
// Рубрика выбирается ЗДЕСЬ, а не после публикации — иначе тема рождается вне
// навигации, и её потом никто не находит.

function mountComposer(host, { onCreated } = {}) {
  host.innerHTML = `
    <div class="cmp-back"></div>
    <div class="cmp">
      <div class="cmp-head"><b>Новая тема</b><span class="cmp-x">×</span></div>
      <div class="cmp-body">
        <label>Заголовок <span class="req">обязательно</span></label>
        <input class="cmp-title" maxlength="120" placeholder="Коротко, одной строкой" />

        <label>Утверждение или вопрос <span class="req">обязательно</span></label>
        <textarea class="cmp-text" rows="4"
          placeholder="С чего начинается обсуждение — тезис, который можно поддержать или оспорить"></textarea>

        <div class="cmp-row">
          <div>
            <label>Направление</label>
            <select class="cmp-dom"></select>
          </div>
          <div>
            <label>Подветвь</label>
            <select class="cmp-sub"></select>
          </div>
        </div>

        <label>География <span class="opt">необязательно</span></label>
        <input class="cmp-geo-find" placeholder="Начни печатать страну, регион или союз…" />
        <div class="cmp-geo-hits"></div>
        <div class="cmp-chosen cmp-geo-chosen"></div>

        <label>Теги <span class="opt">через запятую, до 8</span></label>
        <input class="cmp-tags" placeholder="климат, атом" />

        <div class="cmp-err"></div>
      </div>
      <div class="cmp-foot">
        <button class="cmp-cancel">Отмена</button>
        <button class="cmp-send">Создать тему</button>
      </div>
    </div>`;

  const $ = s => host.querySelector(s);
  const geoChosen = new Set();

  const domSel = $(".cmp-dom"), subSel = $(".cmp-sub");
  DOMAINS.filter(d => d.subs.length).forEach(d => {
    const o = document.createElement("option");
    o.value = d.id; o.textContent = d.name; domSel.appendChild(o);
  });
  function fillSubs() {
    const d = DOM_BY_ID[domSel.value];
    subSel.innerHTML = `<option value="">— не уточнять —</option>`;
    (d ? d.subs : []).forEach(s => {
      const o = document.createElement("option");
      o.value = s; o.textContent = s; subSel.appendChild(o);
    });
  }
  domSel.onchange = fillSubs;
  fillSubs();

  // Гео — поиск, а не выпадающий список: в нём 196 стран плюс регионы и союзы.
  const geoAll = () => [
    ...GEO_UNIONS.map(u => u.name),
    ...GEO_TREE.map(c => c.name),
    ...GEO_TREE.flatMap(c => c.regions.map(r => r.name)),
    ...ALL_COUNTRIES,
  ];
  function renderGeoHits(q) {
    const box = $(".cmp-geo-hits");
    q = q.trim().toLowerCase();
    if (!q) { box.innerHTML = ""; return; }
    const hits = [...new Set(geoAll())]
      .filter(n => n.toLowerCase().includes(q) && !geoChosen.has(n)).slice(0, 8);
    box.innerHTML = hits.map(n => `<span class="cmp-hit">${n}</span>`).join("");
    box.querySelectorAll(".cmp-hit").forEach(el => {
      el.onclick = () => {
        geoChosen.add(el.textContent);
        $(".cmp-geo-find").value = ""; box.innerHTML = ""; renderChosen();
      };
    });
  }
  function renderChosen() {
    $(".cmp-geo-chosen").innerHTML = [...geoChosen]
      .map(n => `<span class="cmp-chip">${n}<i>×</i></span>`).join("");
    $(".cmp-geo-chosen").querySelectorAll(".cmp-chip i").forEach(el => {
      el.onclick = () => { geoChosen.delete(el.parentElement.firstChild.textContent); renderChosen(); };
    });
  }
  $(".cmp-geo-find").addEventListener("input", e => renderGeoHits(e.target.value));

  const close = () => host.classList.remove("open");
  $(".cmp-x").onclick = close;
  $(".cmp-cancel").onclick = close;
  $(".cmp-back").onclick = close;

  $(".cmp-send").onclick = async () => {
    const err = $(".cmp-err");
    const title = $(".cmp-title").value.trim();
    const text = $(".cmp-text").value.trim();
    if (!title || !text) { err.textContent = "нужны и заголовок, и текст"; return; }
    const tags = $(".cmp-tags").value.split(",").map(s => s.trim()).filter(Boolean);
    const btn = $(".cmp-send");
    btn.disabled = true; err.textContent = "";
    try {
      const r = await fetch("/api/argument", {
        method:"POST", headers:{ "content-type":"application/json" },
        body: JSON.stringify({ text, title, connect_to:null, kind:"argument",
                               domain: domSel.value, sub: subSel.value || null,
                               geo:[...geoChosen], tags }),
      });
      if (!r.ok) {
        const body = await r.text();
        // 401 здесь — самая частая причина, и «ошибка сервера» тут бесполезна:
        // человек должен понять, что нужно просто войти.
        err.textContent = r.status === 401
          ? "нужно войти — открой обсуждения и залогинься"
          : (body || "не получилось");
        btn.disabled = false; return;
      }
      const node = await r.json();
      close();
      onCreated && onCreated(node);
    } catch (e) {
      err.textContent = String(e && e.message || e);
    }
    btn.disabled = false;
  };

  return { open: () => { host.classList.add("open"); $(".cmp-title").focus(); } };
}

function setupScreen({ onResize } = {}) {
  const inFs = () => !!(document.fullscreenElement || document.webkitFullscreenElement);

  const fsBtn = document.getElementById("fsBtn");
  const leanBtn = document.getElementById("leanBtn");

  function syncLabels() {
    if (fsBtn) {
      fsBtn.textContent = inFs() ? "⤡ свернуть" : "⤢ во весь экран";
      fsBtn.classList.toggle("on", inFs());
    }
    if (leanBtn) {
      const lean = document.body.classList.contains("lean");
      leanBtn.textContent = lean ? "▣ вернуть панели" : "▢ скрыть панели";
      leanBtn.classList.toggle("on", lean);
    }
  }

  // Размер меняется не мгновенно: браузер перекладывает layout уже после
  // события. Двойной прогон через rAF надёжнее одного setTimeout наугад.
  function afterLayout() {
    requestAnimationFrame(() => requestAnimationFrame(() => onResize && onResize()));
  }

  async function toggleFullscreen() {
    try {
      if (inFs()) {
        await (document.exitFullscreen?.() ?? document.webkitExitFullscreen?.());
      } else {
        const el = document.documentElement;
        await (el.requestFullscreen?.() ?? el.webkitRequestFullscreen?.());
      }
    } catch (_) {
      // Фуллскрин могут запретить политикой или отсутствием жеста —
      // не повод ронять страницу: чистый режим остаётся рабочим запасом.
    }
    syncLabels();
    afterLayout();
  }

  function toggleLean() {
    document.body.classList.toggle("lean");
    syncLabels();
    afterLayout();
  }

  fsBtn && (fsBtn.onclick = toggleFullscreen);
  leanBtn && (leanBtn.onclick = toggleLean);

  // Выход по Esc и системным способом происходит мимо нашей кнопки —
  // подписка обязательна, иначе подпись врёт о текущем состоянии.
  document.addEventListener("fullscreenchange", () => { syncLabels(); afterLayout(); });
  document.addEventListener("webkitfullscreenchange", () => { syncLabels(); afterLayout(); });

  document.addEventListener("keydown", e => {
    const el = document.activeElement, tag = el && el.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || (el && el.isContentEditable)) return;
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    const k = e.key.toLowerCase();
    if (k === "f" || k === "а") { e.preventDefault(); toggleFullscreen(); }   // f / ф-раскладка
    if (k === "h" || k === "р") { e.preventDefault(); toggleLean(); }
  });

  syncLabels();
}
