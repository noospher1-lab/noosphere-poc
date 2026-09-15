// Каталог обсуждений как ВИД внутри основного приложения (index.html), а не
// отдельная страница: поиск, фильтры по направлениям, странам и тегам,
// сортировка.
//
// Раньше здесь был второй режим — «пейзаж» (цветные пятна направлений с
// кружками тем на холсте). Убран 15.09 (vault: decisions/2026-09-15-catalog-only):
// при двух обсуждениях он показывал пустые пятна, держался на свечениях и
// приближении — ровно то, что Alex убрал из графа как нечитаемое, — а его
// работу уже делают другие экраны: стрелки «порождает» есть на карте проблем в
// графе, найти тему быстрее здесь. Единственное, что стоило сохранить — где
// обсуждений нет совсем, — осталось нулями у направлений в фильтрах.
//
// Данные грузятся ЛЕНИВО, при первом открытии вида: 196 стран и список тем не
// нужны тому, кто зашёл почитать одну ветку.

const MapView = (() => {
  // Считаем ОБСУЖДЕНИЯ, а не проблемы: запрос (db.map_topics) фильтра по виду не
  // имеет и отдаёт любой корень — вопрос, предложение, тезис, разбор.
  // Склонение обязательно, иначе выйдет «4 обсуждений».
  const plural = (n) => {
    const d = n % 100, u = n % 10;
    if (d > 10 && d < 20) return n + " обсуждений";
    if (u === 1) return n + " обсуждение";
    if (u >= 2 && u <= 4) return n + " обсуждения";
    return n + " обсуждений";
  };
  let host = null, loaded = false, facets = null, hooks = {};
  let WS = new Set();                       // что уже в рабочем дереве

  const esc = s => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

  function shell() {
    host.innerHTML = `
      <div class="mv-bar">
        <span class="btn mv-fx">☰ фильтры<span class="badge mv-fxn" style="display:none"></span></span>
        <input type="search" class="mv-q" placeholder="Поиск по обсуждениям, тегам и странам…" />
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
        <div class="mv-rows"></div>
      </div>`;

    const $ = s => host.querySelector(s);
    $(".mv-fx").onclick = () => $(".mv-drawer").classList.toggle("open");
    $(".drawer-close").onclick = () => $(".mv-drawer").classList.remove("open");
    $(".mv-q").addEventListener("input", e => {
      setQuery(e.target.value); facets && facets.render(); refresh();
    });
    $(".mv-sort").addEventListener("change", refresh);
  }

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
      // Ни проблем, ни вопросов, ни чего-либо ещё: каталог отдаёт любые корни.
      box.innerHTML = `<div class="mv-none">Здесь пока пусто — ни одного обсуждения. Заведи первое кнопкой «+ Создать» сверху.</div>`;
      return;
    }
    if (!out.length) {
      box.innerHTML = `<div class="mv-none">Ничего не нашлось. Сними часть фильтров — или заведи своё кнопкой «+ Создать».</div>`;
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
        ${(t.causes.length || t.effects.length) ? `<div class="mv-links">${[
          ...t.causes.map(c => `<span class="mv-link up" data-go="${c.id}">↑ причина: ${c.title}</span>`),
          ...t.effects.map(c => `<span class="mv-link" data-go="${c.id}">↓ порождает: ${c.title}</span>`),
        ].join("")}</div>` : ""}
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
    box.querySelectorAll("[data-go]").forEach(el => {
      el.onclick = (e) => {
        e.stopPropagation();               // ссылка ведёт к той проблеме, не к этой
        const id = +el.dataset.go;
        hooks.onOpenTopic && hooks.onOpenTopic(id, MAP_TOPICS.find(t => t.id === id));
      };
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
    renderRows();
  }

  // ------------------------------------------------------------ публичное
  async function open(el, h) {
    host = el; hooks = h || {};
    if (hooks.workspace) WS = hooks.workspace;
    host.style.display = "";
    if (!loaded) {
      host.innerHTML = `<div class="mv-none">загружаю каталог…</div>`;
      try { await loadMapData(); }
      catch (e) {
        host.innerHTML = `<div class="mv-none">каталог не загрузился: ${e.message}</div>`;
        return;
      }
      shell();
      facets = buildFacets(host.querySelector(".mv-facets"), refresh);
      loaded = true;
    }
    refresh();
  }

  // Пересобрать после создания темы: она должна появиться в каталоге сразу,
  // иначе человек решит, что тема не создалась, и напишет её второй раз.
  async function reload() {
    if (!loaded) return;
    await loadMapData();
    facets && facets.render();
    refresh();
  }

  // Дерево — источник правды о подборке; каталог только перерисовывает кнопки.
  function syncWorkspace(ids) {
    WS = ids || new Set();
    if (loaded) renderRows();
  }

  return { open, reload, syncWorkspace,
           // название по id — чтобы сообщение о действии называло проблему,
           // а не говорило безлично «добавлено»
           titleOf: (id) => (MAP_TOPICS.find(t => t.id === id) || {}).title || null,
           close: () => { if (host) host.style.display = "none"; } };
})();
