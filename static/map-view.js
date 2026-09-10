// Карта тем как ВИД внутри основного приложения (index.html), а не отдельная
// страница. Отдельные map-v*.html остаются прототипами для сравнения.
//
// Два режима одним тумблером, потому что они отвечают на разные вопросы:
// «пейзаж» показывает, где густо и где пусто; «каталог» быстрее доводит до
// известной темы. Один вместо двух проиграл бы в одном из этих случаев.
//
// Данные грузятся ЛЕНИВО, при первом открытии вида: 196 стран и список тем не
// нужны тому, кто зашёл почитать одну ветку.

const MapView = (() => {
  // «4 тем» читалось как недоделка ещё и грамматически: слово меняем на
  // «проблема», а раз меняем — сразу со склонением, иначе выйдет «4 проблем».
  const plural = (n) => {
    const d = n % 100, u = n % 10;
    if (d > 10 && d < 20) return n + " проблем";
    if (u === 1) return n + " проблема";
    if (u >= 2 && u <= 4) return n + " проблемы";
    return n + " проблем";
  };
  let host = null, loaded = false, facets = null, hooks = {};
  let WS = new Set();                       // что уже в рабочем дереве
  let mode = localStorage.getItem("mapMode") || "catalog";
  let cv = null, ctx = null, nodes = [], view = { x:0, y:0, k:0.8 };
  let hover = null, dragging = false, last = null;

  const esc = s => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

  function shell() {
    host.innerHTML = `
      <div class="mv-bar">
        <span class="seg mv-mode">
          <b class="seg-i" data-m="landscape">пейзаж</b>
          <b class="seg-i" data-m="catalog">каталог</b>
        </span>
        <span class="btn mv-fx">☰ фильтры<span class="badge mv-fxn" style="display:none"></span></span>
        <input type="search" class="mv-q" placeholder="Поиск по проблемам, тегам и странам…" />
        <select class="mv-sort">
          <option value="nodes">сначала активные</option>
          <option value="people">больше участников</option>
          <option value="poi">выше PoI текста</option>
          <option value="title">по алфавиту</option>
        </select>
        <span class="mv-hits"></span>
      </div>
      <div class="mv-stage">
        <aside class="drawer mv-drawer">
          <div class="drawer-head"><b>Фильтры</b><span class="drawer-close">×</span></div>
          <div class="mv-facets"></div>
        </aside>
        <canvas class="mv-canvas"></canvas>
        <div class="mv-level"></div>
        <div class="mv-tip"></div>
        <div class="mv-rows"></div>
      </div>`;

    const $ = s => host.querySelector(s);
    $(".mv-fx").onclick = () => $(".mv-drawer").classList.toggle("open");
    $(".drawer-close").onclick = () => $(".mv-drawer").classList.remove("open");
    $(".mv-q").addEventListener("input", e => {
      setQuery(e.target.value); facets && facets.render(); refresh();
    });
    $(".mv-sort").addEventListener("change", refresh);
    host.querySelectorAll(".mv-mode .seg-i").forEach(b => {
      b.onclick = () => setMode(b.dataset.m);
    });

    cv = $(".mv-canvas");
    ctx = cv.getContext("2d");
    bindCanvas();
  }

  function setMode(m) {
    mode = m;
    localStorage.setItem("mapMode", m);
    host.querySelectorAll(".mv-mode .seg-i").forEach(b =>
      b.classList.toggle("on", b.dataset.m === m));
    host.classList.toggle("is-landscape", m === "landscape");
    // Сортировка осмысленна только в списке — в пейзаже она ничего не меняет.
    host.querySelector(".mv-sort").style.display = m === "catalog" ? "" : "none";
    if (m === "landscape") { layout(); resize(); }
    refresh();
  }

  // ------------------------------------------------------------ раскладка
  // «Подсолнух» внутри каждого направления: считается сразу, без итераций.
  // Силовая релаксация на сотнях тем перебирает все пары и вешает вкладку.
  function layout() {
    const R = 470, GOLDEN = Math.PI * (3 - Math.sqrt(5));
    const live = DOMAINS.filter(d => MAP_TOPICS.some(t => t.domain === d.id));
    const ring = live.length ? live : DOMAINS;
    ring.forEach((d, i) => {
      const a = (i / ring.length) * Math.PI * 2 - Math.PI / 2;
      d.cx = Math.cos(a) * R; d.cy = Math.sin(a) * R * 0.78;
    });
    nodes = [];
    for (const d of ring) {
      const mine = MAP_TOPICS.filter(t => t.domain === d.id)
                         .sort((a, b) => b.nodes - a.nodes);
      mine.forEach((t, i) => {
        const ang = i * GOLDEN, rad = 12.5 * Math.sqrt(i);
        nodes.push({ ...t, d,
          x: d.cx + Math.cos(ang) * rad, y: d.cy + Math.sin(ang) * rad,
          r: 3.4 + Math.sqrt(Math.max(t.nodes, 1)) * 1.25 });
      });
    }
    host._ring = ring;
  }

  function resize() {
    if (!cv) return;
    const dpr = window.devicePixelRatio || 1, box = cv.parentElement;
    cv.width = box.clientWidth * dpr; cv.height = box.clientHeight * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    draw();
  }

  const toScreen = n => ({
    x: cv.clientWidth / 2 + (n.x + view.x) * view.k,
    y: cv.clientHeight / 2 + (n.y + view.y) * view.k,
  });

  function draw() {
    if (!cv || mode !== "landscape") return;
    ctx.clearRect(0, 0, cv.clientWidth, cv.clientHeight);
    const zoomedOut = view.k < 0.55;
    host.querySelector(".mv-level").innerHTML = zoomedOut
      ? "уровень: <b>направления</b> · приблизь, чтобы увидеть проблемы"
      : "уровень: <b>проблемы</b>";

    for (const d of (host._ring || DOMAINS)) {
      const p = toScreen({ x: d.cx, y: d.cy });
      const rad = (zoomedOut ? 150 : 185) * view.k;
      const g = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, rad);
      g.addColorStop(0, d.color + (zoomedOut ? "26" : "18"));
      g.addColorStop(1, d.color + "00");
      ctx.fillStyle = g; ctx.beginPath(); ctx.arc(p.x, p.y, rad, 0, 7); ctx.fill();
    }

    if (zoomedOut) {
      for (const d of (host._ring || DOMAINS)) {
        const p = toScreen({ x: d.cx, y: d.cy });
        const n = nodes.filter(x => x.domain === d.id && matchTopic(x)).length;
        ctx.textAlign = "center";
        ctx.fillStyle = n ? d.color : "#3a4050";
        ctx.font = "600 15px Inter, sans-serif";
        ctx.fillText(d.name, p.x, p.y - 4);
        ctx.font = "500 12px 'JetBrains Mono', monospace";
        ctx.fillStyle = "#6f7688";
        ctx.fillText(plural(n), p.x, p.y + 15);
      }
      return;
    }

    // Подпись направления НАД скоплением: без неё карта — набор цветных пятен,
    // про которые нельзя сказать, какое из них экономика.
    for (const d of (host._ring || DOMAINS)) {
      const mine = nodes.filter(n => n.domain === d.id);
      if (!mine.length) continue;
      const top = Math.min(...mine.map(n => n.y - n.r));
      const p = toScreen({ x: d.cx, y: top });
      const shown = mine.filter(matchTopic).length;
      ctx.textAlign = "center";
      ctx.font = "600 13px Inter, sans-serif";
      ctx.fillStyle = shown ? d.color : "#3a4050";
      ctx.fillText(d.name, p.x, p.y - 16);
      ctx.font = "500 10.5px 'JetBrains Mono', monospace";
      ctx.fillStyle = "#6f7688";
      ctx.fillText(shown === mine.length ? `${mine.length}` : `${shown} из ${mine.length}`,
                   p.x, p.y - 4);
    }

    for (const n of nodes) {
      const p = toScreen(n), on = matchTopic(n), r = n.r * view.k;
      ctx.globalAlpha = on ? 1 : 0.13;
      ctx.beginPath(); ctx.arc(p.x, p.y, r, 0, 7);
      ctx.fillStyle = n.d.color + "2e"; ctx.fill();
      ctx.lineWidth = hover === n ? 2 : 1.2;
      ctx.strokeStyle = hover === n ? n.d.color : n.d.color + "9a";
      ctx.stroke();
      if (view.k > 1.5 && on && (n.nodes > 25 || n === hover)) {
        ctx.globalAlpha = 1;
        ctx.font = "500 11px Inter, sans-serif";
        ctx.textAlign = "center"; ctx.fillStyle = "#9aa0b0";
        const s = n.title.length > 30 ? n.title.slice(0, 28) + "…" : n.title;
        ctx.fillText(s, p.x, p.y + r + 12);
      }
    }
    ctx.globalAlpha = 1;
  }

  function nodeAt(mx, my) {
    for (let i = nodes.length - 1; i >= 0; i--) {
      const n = nodes[i], p = toScreen(n);
      if (Math.hypot(mx - p.x, my - p.y) <= n.r * view.k + 3 && matchTopic(n)) return n;
    }
    return null;
  }

  function bindCanvas() {
    cv.addEventListener("mousedown", e => { dragging = true; last = e; });
    window.addEventListener("mouseup", () => { dragging = false; });
    cv.addEventListener("mousemove", e => {
      const r = cv.getBoundingClientRect(), mx = e.clientX - r.left, my = e.clientY - r.top;
      if (dragging) {
        view.x += (e.clientX - last.clientX) / view.k;
        view.y += (e.clientY - last.clientY) / view.k;
        last = e; draw(); return;
      }
      const n = view.k < 0.55 ? null : nodeAt(mx, my);
      const tip = host.querySelector(".mv-tip");
      if (n !== hover) { hover = n; draw(); }
      if (n) {
        tip.style.display = "block";
        tip.style.left = Math.min(mx + 16, cv.clientWidth - 290) + "px";
        tip.style.top = (my + 16) + "px";
        tip.innerHTML = `<div class="t">${n.title}</div><div class="m">${n.d.name}` +
          (n.sub ? " · " + n.sub : "") +
          (n.geo.length ? "<br>" + n.geo.slice(0, 3).join(", ") : "") +
          `<br>${n.nodes} узлов · ${n.people} участников</div>`;
      } else tip.style.display = "none";
    });
    cv.addEventListener("wheel", e => {
      e.preventDefault();
      view.k = Math.max(0.3, Math.min(2.6, view.k * (e.deltaY < 0 ? 1.12 : 1 / 1.12)));
      draw();
    }, { passive:false });
    cv.addEventListener("click", e => {
      const r = cv.getBoundingClientRect();
      const n = view.k < 0.55 ? null : nodeAt(e.clientX - r.left, e.clientY - r.top);
      if (n) hooks.onOpenTopic && hooks.onOpenTopic(n.id);
    });
    window.addEventListener("resize", () => { if (mode === "landscape") resize(); });
  }

  // ------------------------------------------------------------- каталог
  function hl(text) {
    if (!QUERY) return text;
    return text.replace(new RegExp("(" + esc(QUERY) + ")", "gi"), "<mark>$1</mark>");
  }

  function renderRows() {
    const box = host.querySelector(".mv-rows");
    const sortBy = host.querySelector(".mv-sort").value;
    const out = MAP_TOPICS.filter(t => matchTopic(t));
    out.sort((a, b) => sortBy === "title"
      ? a.title.localeCompare(b.title, "ru") : b[sortBy] - a[sortBy]);

    if (!MAP_TOPICS.length) {
      // Кнопка называется «+ Создать» с 2026-09-09: наверху может стоять не
      // только проблема. Карта при этом остаётся картой ПРОБЛЕМ — рубрику и
      // географию несут они, — поэтому зовём завести именно проблему.
      box.innerHTML = `<div class="mv-none">Проблем пока нет. Заведи первую — кнопка «+ Создать» сверху, вид «проблема».</div>`;
      return;
    }
    if (!out.length) {
      box.innerHTML = `<div class="mv-none">Ничего не нашлось. Сними часть фильтров или заведи проблему здесь.</div>`;
      return;
    }
    box.innerHTML = out.map(t => {
      const d = DOM_BY_ID[t.domain];
      const meta = [
        ...t.geo.slice(0, 4).map(g => `<span class="mv-tag">📍 ${g}</span>`),
        ...t.tags.map(x => `<span class="mv-tag">#${x}</span>`),
      ].join("");
      // «+ в дерево» — отдельное действие от «открыть». Если бы открытие само
      // добавляло, подборка забилась бы всем, во что человек заглянул.
      const inWs = WS.has(t.id);
      const btn = hooks.onToggleWorkspace && t.id > 0
        ? `<span class="mv-ws${inWs ? " on" : ""}" data-ws="${t.id}"
             title="${inWs ? "Убрать из рабочего дерева" : "Добавить в рабочее дерево"}"
           >${inWs ? "✓ в дереве" : "+ в дерево"}</span>`
        : "";
      return `<div class="mv-card" data-id="${t.id}">
        <div class="mv-top">
          <span class="mv-dot" style="background:${d.color}"></span>
          <span class="mv-dom" style="color:${d.color}">${d.name}</span>
          ${t.sub ? `<span class="mv-sub">· ${t.sub}</span>` : ""}
          ${t.unsorted ? `<span class="mv-warn">без рубрики</span>` : ""}
          ${btn}
        </div>
        <div class="mv-title">${hl(t.title)}</div>
        <div class="mv-bot">${meta}
          <span class="mv-stats"><b>${t.nodes}</b> узлов · <b>${t.people}</b> уч.${
            t.poi ? ` · PoI <b>${t.poi}</b>` : ""}</span>
        </div>
      </div>`;
    }).join("");
    box.querySelectorAll(".mv-card").forEach(c => {
      const id = +c.dataset.id;
      c.onclick = () => hooks.onOpenTopic &&
        hooks.onOpenTopic(id, MAP_TOPICS.find(t => t.id === id));
    });
    box.querySelectorAll("[data-ws]").forEach(el => {
      el.onclick = (e) => {
        e.stopPropagation();               // «+» не должен открывать тему
        const id = +el.dataset.ws;
        hooks.onToggleWorkspace(id, !WS.has(id));
      };
    });
  }

  function refresh() {
    const n = MAP_TOPICS.filter(t => matchTopic(t)).length;
    const filtered = QUERY || selectionCount();
    host.querySelector(".mv-hits").textContent =
      filtered ? `${n} из ${MAP_TOPICS.length}` : plural(MAP_TOPICS.length);
    const badge = host.querySelector(".mv-fxn"), c = selectionCount();
    badge.style.display = c ? "" : "none";
    badge.textContent = c;
    if (mode === "catalog") renderRows(); else draw();
  }

  // ------------------------------------------------------------ публичное
  async function open(el, h) {
    host = el; hooks = h || {};
    if (hooks.workspace) WS = hooks.workspace;
    host.style.display = "";
    if (!loaded) {
      host.innerHTML = `<div class="mv-none">загружаю карту…</div>`;
      try { await loadMapData(); }
      catch (e) {
        host.innerHTML = `<div class="mv-none">карта не загрузилась: ${e.message}</div>`;
        return;
      }
      shell();
      facets = buildFacets(host.querySelector(".mv-facets"), refresh);
      loaded = true;
    }
    setMode(mode);
    if (mode === "landscape") { layout(); resize(); }
  }

  // Пересобрать после создания темы: она должна появиться на карте сразу,
  // иначе человек решит, что тема не создалась, и напишет её второй раз.
  async function reload() {
    if (!loaded) return;
    await loadMapData();
    facets && facets.render();
    if (mode === "landscape") layout();
    refresh();
  }

  // Дерево — источник правды о подборке; карта только перерисовывает кнопки.
  function syncWorkspace(ids) {
    WS = ids || new Set();
    if (loaded && mode === "catalog") renderRows();
  }

  return { open, reload, syncWorkspace,
           // название по id — чтобы сообщение о действии называло проблему,
           // а не говорило безлично «добавлено»
           titleOf: (id) => (MAP_TOPICS.find(t => t.id === id) || {}).title || null,
           close: () => { if (host) host.style.display = "none"; } };
})();
