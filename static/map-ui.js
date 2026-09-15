// Фильтры и поиск каталога обсуждений (index.html, вид «каталог»): совпадение
// темы с запросом и выбранными фасетами, счётчики, панель фильтров.
// Раньше здесь же жили форма создания темы на карте и полноэкранный режим для
// прототипов map-v1…v3 — удалены вместе с ними 15.09
// (vault: decisions/2026-09-15-catalog-only).

// ============================================================ ФИЛЬТРЫ
// Панель фильтров каталога: 196 стран и 120 подветвей нельзя
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
    // Направления видны ВСЕ, и пустые тоже (серым, с нулём): это единственное,
    // что осталось от «пейзажа» (vault: decisions/2026-09-15-catalog-only) —
    // видно, где обсуждений пока нет совсем, а это подсказка, что засевать.
    g1.innerHTML = `<h3>Направления</h3>` +
      `<div class="fx-note" style="font-size:11.5px;color:var(--dim2,#6f7688);margin:-2px 0 6px 8px">` +
      `0 — по направлению пока нет ни одного обсуждения</div>`;
    for (const d of DOMAINS) {
      const n = countBy("dom", t => t.domain === d.id);
      const subsHit = d.subs.filter(s => hit(s));
      const self = hit(d.name);
      if (!self && !subsHit.length) continue;
      // «без рубрики» с нулём — не направление, а служебная корзина: её прячем
      if (!n && !needle && d === UNSORTED) continue;
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
