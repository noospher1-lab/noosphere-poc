// Плоский граф (vault: decisions/2026-09-15-flat-graph).
//
// Прежний 3D-граф ложился каждый раз по-разному, светился и двигался — и не
// читался. Здесь всё наоборот: SVG без библиотек, раскладка вычисляется из
// данных (одни данные — одна картинка), ничего не движется само, двигает и
// приближает только человек. Два вида:
//   карта проблем — уровни «порождает»: причина выше, следствие ниже;
//   ветка        — сверху вниз: строка = глубина ответа, у каждой ветки своя
//                  колонка, цвет линии = вид связи (Alex 25.09: так читабельнее).
// Подсвечено своим цветом: возражение без ответа, открытый вопрос, довод,
// который держится, ответ, который признаёт часть. Остальное — приглушённо.
(() => {
  "use strict";
  const NS = "http://www.w3.org/2000/svg";
  const $ = (s) => document.querySelector(s);

  const REL_WORD = {
    support: "за", refute: "против", undercut: "не доказывает", qualify: "уточнение",
    restate: "пересказ",
    question: "вопрос", proposal: "предложение", exploration: "разбор", atom: "атом",
  };
  // под вопросом ответ — «да / нет», а не «за / против» (как в дереве, 25.09)
  const ANSWER_WORD = { support: "да", refute: "нет" };
  const KIND_BY_ID = new Map();
  const relWord = (n) => (KIND_BY_ID.get(n.parent) === "question" && ANSWER_WORD[n.rel])
    || REL_WORD[n.rel] || n.rel || "";
  const KIND_WORD = {
    problem: "проблема", question: "вопрос", proposal: "предложение",
    exploration: "разбор", argument: "тезис", atom: "атом разбора",
  };
  const VERDICT_WORD = {
    untested: "не оспаривался", holds: "держится",
    weakened: "ослаблен", shaken: "сильно ослаблен",
  };
  // Одна отметка на заливку, по важности: куда стоит ответить — раньше того,
  // что уже выстояло. «Признаёт» не заливка, а пунктирная рамка: она бывает
  // вместе с любой из них.
  const FILL_ORDER = [["unanswered", "без ответа"], ["open", "открытый вопрос"],
                      ["holds", "держится"]];

  // ------------------------------------------------------------ svg helpers
  function svgEl(name, attrs, parent) {
    const e = document.createElementNS(NS, name);
    for (const k in attrs || {}) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }
  function text(parent, x, y, str, cls) {
    const t = svgEl("text", { x, y, class: cls || "" }, parent);
    t.textContent = str;
    return t;
  }

  const measureCtx = document.createElement("canvas").getContext("2d");
  const FONT_LABEL = "500 12.5px Inter, system-ui, sans-serif";
  const FONT_TITLE = "600 14px Inter, system-ui, sans-serif";
  const FONT_SMALL = "500 11px Inter, system-ui, sans-serif";
  function width(str, font) { measureCtx.font = font; return measureCtx.measureText(str).width; }

  // Перенос по словам в заданную ширину и число строк; не влезло — многоточие.
  function wrap(str, font, maxW, maxLines) {
    const words = String(str || "").split(/\s+/).filter(Boolean);
    const lines = [];
    let cur = "", used = 0;
    for (const w of words) {
      const t = cur ? cur + " " + w : w;
      if (!cur || width(t, font) <= maxW) { cur = t; used++; continue; }
      lines.push(cur);
      if (lines.length === maxLines) { cur = ""; break; }
      cur = w; used++;
    }
    if (cur && lines.length < maxLines) lines.push(cur);
    const cut = used < words.length;
    if (lines.length && (cut || width(lines[lines.length - 1], font) > maxW)) {
      let last = lines[lines.length - 1];
      while (last.length > 1 && width(last + "…", font) > maxW) last = last.slice(0, -1);
      lines[lines.length - 1] = last.replace(/[\s,.;:—-]+$/, "") + "…";
    }
    return lines;
  }

  // ------------------------------------------------------------ pan & zoom
  // Только по действию человека и без плавных переходов: картинка стоит, пока
  // её не тронули.
  const svg = $("#canvas");
  const scene = svgEl("g", {}, svg);
  const view = { x: 0, y: 0, k: 1 };
  let content = { w: 0, h: 0 };
  function applyView() {
    scene.setAttribute("transform", `translate(${view.x},${view.y}) scale(${view.k})`);
  }
  function fit() {
    const r = svg.getBoundingClientRect();
    const pad = 28;
    const k = Math.min(1, (r.width - pad * 2) / Math.max(1, content.w));
    view.k = Math.max(0.3, k);
    view.x = Math.max(pad, (r.width - content.w * view.k) / 2);
    view.y = pad;
    applyView();
  }
  // Доска ветки помещается по ширине экрана и растёт вниз: масштаб обычный,
  // начало сверху. «Вписать» по кнопке по-прежнему ужимает всё.
  let lastStart = () => fit();
  function startBoard() {
    const r = svg.getBoundingClientRect();
    view.k = 1;
    view.x = Math.max(28, (r.width - content.w) / 2);
    view.y = 28;
    applyView();
  }
  function zoomAt(px, py, f) {
    const k = Math.min(2.5, Math.max(0.2, view.k * f));
    view.x = px - (px - view.x) * (k / view.k);
    view.y = py - (py - view.y) * (k / view.k);
    view.k = k;
    applyView();
  }
  let drag = null, moved = false;
  svg.addEventListener("pointerdown", (e) => {
    drag = { x: e.clientX, y: e.clientY, vx: view.x, vy: view.y };
    moved = false;
  });
  addEventListener("pointermove", (e) => {
    if (!drag) return;
    const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
    if (Math.abs(dx) + Math.abs(dy) > 4) moved = true;
    if (!moved) return;
    view.x = drag.vx + dx; view.y = drag.vy + dy;
    applyView();
  });
  addEventListener("pointerup", () => { drag = null; });
  svg.addEventListener("wheel", (e) => {
    e.preventDefault();
    const r = svg.getBoundingClientRect();
    // доска ветки — как страница: колесо листает, масштаб — Ctrl+колесо (так же
    // приходит щипок на тачпаде). На карте проблем колесо по-прежнему масштабирует.
    if (lastStart === startBoard && !e.ctrlKey && !e.metaKey) {
      const minY = Math.min(28, r.height - content.h * view.k - 28);
      view.y = Math.min(28, Math.max(minY, view.y - e.deltaY));
      view.x -= e.deltaX;
      applyView();
      return;
    }
    zoomAt(e.clientX - r.left, e.clientY - r.top, Math.exp(-e.deltaY * 0.0015));
  }, { passive: false });
  // клик по карточке не должен срабатывать, если человек тащил картинку
  function onActivate(g, fn) {
    g.setAttribute("tabindex", "0");
    g.setAttribute("role", "button");
    g.addEventListener("click", (e) => { if (!moved) fn(e); });
    g.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fn(e); } });
  }
  $("#zoom-in").onclick = () => { const r = svg.getBoundingClientRect(); zoomAt(r.width / 2, r.height / 2, 1.25); };
  $("#zoom-out").onclick = () => { const r = svg.getBoundingClientRect(); zoomAt(r.width / 2, r.height / 2, 0.8); };
  $("#zoom-fit").onclick = fit;

  function clearScene() {
    while (scene.firstChild) scene.removeChild(scene.firstChild);
    closePanel();
  }
  function message(str) {
    clearScene();
    lastStart = fit;
    content = { w: 400, h: 60 };
    text(scene, 0, 30, str, "msg");
    fit();
  }

  async function getJSON(url) {
    const r = await fetch(url);
    if (!r.ok) throw new Error(r.status + " " + (await r.text()).slice(0, 200));
    return r.json();
  }

  // ------------------------------------------------------------ карта проблем
  const M = { cw: 290, ch: 92, gx: 36, gy: 76, pad: 0 };

  function statChips(g, x, y, maxW, s) {
    const chips = [];
    if (s.unanswered) chips.push(["без ответа " + s.unanswered, "chip unanswered"]);
    if (s.open) chips.push(["открытых вопросов " + s.open, "chip open"]);
    if (s.holds) chips.push(["держатся " + s.holds, "chip holds"]);
    chips.push([s.replies ? "ответов " + s.replies : "ответов нет", "chip dim"]);
    let cx = x;
    for (const [label, cls] of chips) {
      const w = width(label, FONT_SMALL);
      if (cx + w > x + maxW) break;
      text(g, cx, y, label, cls);
      cx += w + 12;
    }
  }

  function problemCard(parent, p, x, y) {
    const g = svgEl("g", { class: "pcard", transform: `translate(${x},${y})` }, parent);
    svgEl("rect", { width: M.cw, height: M.ch, rx: 8, class: "pbox" + (p.kind === "problem" ? "" : " other") }, g);
    text(g, 14, 20, KIND_WORD[p.kind] || "обсуждение", "tag kind");
    wrap(p.title, FONT_TITLE, M.cw - 28, 2).forEach((line, i) => text(g, 14, 42 + i * 18, line, "ptitle"));
    statChips(g, 14, M.ch - 12, M.cw - 28, p.summary);
    const t = svgEl("title", {}, g);
    t.textContent = p.title + " — открыть обсуждение";
    onActivate(g, () => go({ problem: p.id }));
    return g;
  }

  function renderMap(data) {
    clearScene();
    setCrumbs(null);
    setLegend("map");
    const probs = data.problems || [];
    const others = data.others || [];
    if (!probs.length && !others.length) {
      message("Пока пусто. Проблемы заводятся в дереве обсуждений.");
      return;
    }
    // уровни сверху вниз; внутри уровня — под своими причинами (среднее по x
    // причин), при равенстве — по номеру. Детерминированно.
    const byLevel = new Map();
    for (const p of probs) {
      if (!byLevel.has(p.level)) byLevel.set(p.level, []);
      byLevel.get(p.level).push(p);
    }
    const levels = [...byLevel.keys()].sort((a, b) => a - b);
    const pos = new Map();
    let maxRow = 0;
    levels.forEach((lv) => {
      const row = byLevel.get(lv);
      const bary = (p) => {
        const xs = p.causes.map((c) => pos.get(c)).filter(Boolean).map((q) => q.i);
        return xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : Infinity;
      };
      row.sort((a, b) => (bary(a) - bary(b)) || (a.id - b.id));
      row.forEach((p, i) => pos.set(p.id, { i, lv }));
      maxRow = Math.max(maxRow, row.length);
    });
    const rowW = (n) => n * M.cw + (n - 1) * M.gx;
    const W = Math.max(rowW(maxRow), rowW(Math.min(3, others.length || 1)));
    const at = new Map();
    levels.forEach((lv, li) => {
      const row = byLevel.get(lv);
      const off = (W - rowW(row.length)) / 2;
      row.forEach((p, i) => at.set(p.id, { x: off + i * (M.cw + M.gx), y: li * (M.ch + M.gy) }));
    });

    // стрелки «порождает» — под карточками
    const edges = svgEl("g", { class: "links" }, scene);
    for (const l of data.links || []) {
      const a = at.get(l.cause_id), b = at.get(l.effect_id);
      if (!a || !b) continue;
      const cls = "plink" + (l.verdict ? " v-" + l.verdict : "");
      let d;
      if (b.y > a.y) {
        const x1 = a.x + M.cw / 2, y1 = a.y + M.ch, x2 = b.x + M.cw / 2, y2 = b.y;
        const my = (y1 + y2) / 2;
        d = `M${x1},${y1} C${x1},${my} ${x2},${my} ${x2},${y2 - 6}`;
      } else {
        // круг причин или связь внутри уровня — обходим сбоку
        const x1 = a.x + M.cw, y1 = a.y + M.ch / 2, x2 = b.x + M.cw, y2 = b.y + M.ch / 2;
        const bx = Math.max(x1, x2) + 40;
        d = `M${x1},${y1} C${bx},${y1} ${bx},${y2} ${x2 + 6},${y2}`;
      }
      const path = svgEl("path", { d, class: cls, "marker-end": "url(#arrow)" }, edges);
      const t = svgEl("title", {}, path);
      t.textContent = "порождает" + (l.verdict && l.verdict !== "untested"
        ? " · связь " + ({ holds: "держится", weakened: "ослаблена", shaken: "сильно ослаблена" }[l.verdict] || "")
          + (l.unanswered ? " · без ответа " + l.unanswered : "")
        : "");
    }
    const cards = svgEl("g", {}, scene);
    for (const p of probs) { const q = at.get(p.id); problemCard(cards, p, q.x, q.y); }

    let H = levels.length ? levels.length * (M.ch + M.gy) - M.gy : 0;
    if (others.length) {
      const top = H + (H ? 64 : 0);
      text(scene, 0, top, "Другие обсуждения — не проблемы: вопросы, тезисы, предложения, разборы", "section");
      const perRow = Math.max(1, Math.floor((W + M.gx) / (M.cw + M.gx)));
      others.forEach((p, i) => {
        const x = (i % perRow) * (M.cw + M.gx);
        const y = top + 18 + Math.floor(i / perRow) * (M.ch + 22);
        problemCard(cards, p, x, y);
      });
      H = top + 18 + Math.ceil(others.length / perRow) * (M.ch + 22);
    }
    content = { w: W, h: H };
    lastStart = fit;
    fit();
  }

  // ------------------------------------------------------------ ветка
  // Ветка растёт СВЕРХУ ВНИЗ (Alex 25.09: «так будет читабельнее»): уровень —
  // строка, ответы одного родителя — рядом по горизонтали. gx — зазор между
  // соседями, gy — между уровнями (там идут связи).
  const T = { cw: 236, ch: 70, gx: 16, gy: 48 };
  let topicData = null;
  let selected = null;
  const cardEls = new Map();

  function fillOf(n) {
    for (const [k] of FILL_ORDER) if (n.flags[k]) return k;
    return null;
  }

  function nodeCard(parent, n, x, y, w = T.cw) {
    const fill = fillOf(n);
    const cls = ["ncard", fill ? "hl-" + fill : "", n.flags.concedes ? "concede" : "",
                 n.retracted ? "retracted" : "", n.parent == null ? "root" : ""].join(" ");
    const g = svgEl("g", { class: cls, transform: `translate(${x},${y})` }, parent);
    svgEl("rect", { width: w, height: T.ch, rx: 7, class: "nbox" }, g);
    // верхняя строка: вид связи (или вид корня) и отметки словами — цвет не
    // единственный носитель смысла
    let tx = 12;
    const tag = (label, c) => { text(g, tx, 18, label, "tag " + c); tx += width(label, FONT_SMALL) + 10; };
    if (n.parent == null) tag(KIND_WORD[n.kind] || "обсуждение", "kind");
    else tag(relWord(n), "rel-" + (n.rel || "none"));
    if (fill) tag(FILL_ORDER.find(([k]) => k === fill)[1], "flag-" + fill);
    if (n.flags.concedes && tx < w - 60) tag("признаёт", "flag-concede");
    if (n.retracted && tx < w - 60) tag("отозвано", "dim");
    if (n.poi_score != null) {
      const s = "PoI " + Math.round(n.poi_score);
      text(g, w - 12 - width(s, FONT_SMALL), 18, s, "poi");
    }
    wrap(n.label, n.parent == null ? FONT_TITLE : FONT_LABEL, w - 24, 2)
      .forEach((line, i) => text(g, 12, 38 + i * 17, line, n.parent == null ? "ptitle" : "nlabel"));
    const t = svgEl("title", {}, g);
    t.textContent = n.text;
    onActivate(g, () => selectNode(n.id));
    cardEls.set(n.id, g);
  }

  function renderTopic(data) {
    clearScene();
    topicData = data;
    cardEls.clear();
    setCrumbs(data);
    setLegend("topic");
    const list = data.nodes || [];
    const byId = new Map(list.map((n) => [n.id, { ...n, kids: [] }]));
    for (const n of list) KIND_BY_ID.set(n.id, n.kind);
    let root = null;
    for (const n of list) {                          // порядок сервера = по времени
      const me = byId.get(n.id);
      if (n.parent == null) root = me;
      else if (byId.has(n.parent)) byId.get(n.parent).kids.push(me);
    }
    if (!root) { message("Ветка пуста."); return; }
    // ДОСКА (Alex 25.09: «не видно всего» — в одну строку 13 вопросов при
    // «вписать» превращались в нечитаемую полоску). Проблема наверху; её прямые
    // ответы — колонками, сколько влезает по ширине экрана, дальше новый ряд;
    // под каждым — его ветка стопкой со сдвигом по глубине, как в дереве.
    // Растёт вниз, листается вниз, текст в натуральную величину.
    const r = svg.getBoundingClientRect();
    const COL = 260, GAP = 22, STEP = T.ch + 10, INDENT = 18, TRUNK = 16;
    const kids1 = root.kids;
    const cols = Math.max(1, Math.min(kids1.length || 1,
      Math.floor((r.width - 56 - TRUNK + GAP) / (COL + GAP))));
    const boardW = cols * (COL + GAP) - GAP;
    const rootW = Math.min(boardW, 2 * COL);
    root.x = TRUNK + (boardW - rootW) / 2; root.y = 0; root.w = rootW;
    const colTops = [];
    let rowTop = T.ch + 44, maxDepth = 0;
    for (let i = 0; i < kids1.length; i += cols) {
      let rowH = 0;
      kids1.slice(i, i + cols).forEach((top, j) => {
        const cx = TRUNK + j * (COL + GAP);
        let yy = rowTop;
        const stack = (n, d) => {
          n.x = cx + d * INDENT; n.y = yy; n.w = Math.max(150, COL - d * INDENT);
          maxDepth = Math.max(maxDepth, d);
          yy += STEP;
          n.kids.forEach((k) => stack(k, d + 1));
        };
        stack(top, 0);
        colTops.push({ n: top, row: rowTop });
        rowH = Math.max(rowH, yy - rowTop);
      });
      rowTop += rowH + 40;
    }

    const edges = svgEl("g", { class: "edges" }, scene);
    const cards = svgEl("g", {}, scene);
    const line = (d, cls) => svgEl("path", { d, class: "edge " + cls }, edges);
    // от проблемы: вниз, влево к стволу; ствол идёт по левому краю до каждого
    // ряда, по ряду — шина, от шины вниз к каждой колонке
    const rootCx = root.x + rootW / 2, bus0 = T.ch + 22;
    if (colTops.length) {
      const lastBus = colTops[colTops.length - 1].row - 22;
      line(`M${rootCx},${T.ch} V${bus0} H4 V${lastBus}`, "rel-none");
      const rows = [...new Set(colTops.map((c) => c.row))];
      for (const rt of rows) {
        const inRow = colTops.filter((c) => c.row === rt);
        const last = inRow[inRow.length - 1].n;
        line(`M4,${rt - 22} H${last.x + COL / 2}`, "rel-none");
        for (const c of inRow)
          line(`M${c.n.x + COL / 2},${rt - 22} V${rt}`,
               "rel-" + (c.n.rel || "none") + (c.n.retracted ? " retracted" : ""));
      }
    }
    // внутри колонки: ствол родителя по его левому краю, к ответу — вправо
    const walk = (n) => {
      for (const k of n.kids) {
        if (n !== root)
          line(`M${n.x + 9},${n.y + T.ch} V${k.y + T.ch / 2} H${k.x}`,
               "rel-" + (k.rel || "none") + (k.retracted ? " retracted" : ""));
        walk(k);
      }
      nodeCard(cards, n, n.x, n.y, n.w);
    };
    walk(root);
    content = { w: TRUNK + boardW, h: rowTop - 40 };
    lastStart = startBoard;
    lastStart();
    const want = Number(new URLSearchParams(location.search).get("node"));
    if (want && byId.has(want)) selectNode(want, { center: true });
  }

  // ------------------------------------------------------------ панель узла
  function closePanel() {
    $("#panel").hidden = true;
    if (selected != null && cardEls.get(selected)) cardEls.get(selected).classList.remove("sel");
    selected = null;
  }
  $("#panel-close").onclick = closePanel;
  addEventListener("keydown", (e) => { if (e.key === "Escape") closePanel(); });

  // Выбранная карточка должна быть видна рядом с панелью, а не под ней: на
  // широком экране панель справа, на телефоне — снизу на пол-экрана. Сдвиг
  // только если карточка правда не видна, и сразу, без плавной прокрутки.
  function ensureVisible(id) {
    const g = cardEls.get(id);
    if (!g) return;
    const s = svg.getBoundingClientRect(), c = g.getBoundingClientRect();
    const p = $("#panel").hidden ? null : $("#panel").getBoundingClientRect();
    const sheet = p && p.width >= s.width - 40;
    const area = {
      left: s.left, top: s.top,
      right: p && !sheet ? p.left : s.right,
      bottom: p && sheet ? p.top : s.bottom,
    };
    if (c.left >= area.left && c.right <= area.right
        && c.top >= area.top && c.bottom <= area.bottom) return;
    view.x += (area.left + area.right) / 2 - (c.left + c.right) / 2;
    view.y += (area.top + area.bottom) / 2 - (c.top + c.bottom) / 2;
    applyView();
  }

  function selectNode(id, opts) {
    const n = (topicData && topicData.nodes || []).find((x) => x.id === id);
    if (!n) return;
    if (selected != null && cardEls.get(selected)) cardEls.get(selected).classList.remove("sel");
    selected = id;
    if (cardEls.get(id)) cardEls.get(id).classList.add("sel");

    const p = $("#panel-body");
    p.textContent = "";
    const add = (tag, cls, str) => {
      const e = document.createElement(tag);
      if (cls) e.className = cls;
      if (str != null) e.textContent = str;
      p.appendChild(e);
      return e;
    };
    add("div", "eyebrow", n.parent == null ? (KIND_WORD[n.kind] || "обсуждение")
        : relWord(n) + " · " + (KIND_WORD[n.kind] || "тезис"));
    add("div", "ptext", n.text);
    const meta = add("div", "pmeta");
    meta.append("автор: " + (n.author || "—"));
    if (n.poi_score != null) meta.append(" · PoI " + Math.round(n.poi_score));
    meta.append(" · #" + n.id);
    if (n.value) add("div", "pmeta", "на что опирается: " + n.value_name
      + (n.value_phrase ? " · «" + n.value_phrase + "»" : ""));
    if (n.retracted) add("div", "pnote", "Автор отозвал этот довод — больше на нём не настаивает.");
    if (n.flags.concedes) {
      add("div", "pnote concede", n.concede_quote
        ? "Признаёт: «" + n.concede_quote + "»" : "Признаёт часть того, на что отвечает.");
    }
    if (n.flags.open) add("div", "pnote open", "Открытый вопрос: ответа пока нет.");
    if (n.flags.unanswered) add("div", "pnote unanswered", "Возражение без ответа: на него пока никто, кроме автора, не ответил.");
    // «в споре» — только когда спор был: у неоспоренного возражения эта строка
    // лишь повторяла отметку «без ответа» другими словами
    if (n.parent != null && !["question", "exploration"].includes(n.kind)
        && (n.attacks || n.supports)) {
      const line = add("div", "pmeta");
      line.append("в споре: ");
      const w = document.createElement("b");
      w.className = "v-" + n.verdict;
      w.textContent = VERDICT_WORD[n.verdict] || n.verdict;
      line.appendChild(w);
      if (n.attacks) line.append(" · возражений: " + n.attacks);
      if (n.supports) line.append(" · за: " + n.supports);
      if (n.unanswered_ids.length) {
        line.append(" · без ответа: ");
        n.unanswered_ids.forEach((uid, i) => {
          if (i) line.append(", ");
          const a = document.createElement("a");
          a.href = "#"; a.textContent = "#" + uid;
          a.onclick = (e) => { e.preventDefault(); selectNode(uid, { center: true }); };
          line.appendChild(a);
        });
      }
    }
    const open = add("a", "pbtn", "открыть в дереве и ответить →");
    open.href = "/n/" + n.id;
    $("#panel").hidden = false;
    ensureVisible(id);
  }

  // ------------------------------------------------------------ шапка вида
  function setCrumbs(topic) {
    const c = $("#crumbs");
    c.textContent = "";
    if (!topic) {
      const s = document.createElement("span");
      s.className = "here";
      s.textContent = "Карта проблем";
      c.appendChild(s);
      c.append(" · причина выше, следствие ниже · клик по карточке — её обсуждение");
      return;
    }
    const back = document.createElement("a");
    back.href = "/graph.html"; back.textContent = "← Карта проблем";
    back.onclick = (e) => { e.preventDefault(); go({}); };
    c.appendChild(back);
    c.append(" › ");
    const s = document.createElement("span");
    s.className = "here";
    s.textContent = topic.title;
    c.appendChild(s);
    const sm = topic.summary || {};
    const bits = [];
    if (sm.unanswered) bits.push("без ответа: " + sm.unanswered);
    if (sm.open) bits.push("открытых вопросов: " + sm.open);
    if (sm.holds) bits.push("держатся: " + sm.holds);
    if (sm.concedes) bits.push("признаёт: " + sm.concedes);
    if (bits.length) c.append(" · " + bits.join(" · "));
    const tree = document.createElement("a");
    tree.href = "/n/" + topic.root; tree.className = "totree";
    tree.textContent = "в дереве →";
    c.append(" ");
    c.appendChild(tree);
  }

  function setLegend(mode) {
    $("#legend-topic").hidden = mode !== "topic";
    $("#legend-map").hidden = mode !== "map";
  }

  // ------------------------------------------------------------ навигация
  async function load() {
    const q = new URLSearchParams(location.search);
    const pid = Number(q.get("problem"));
    try {
      if (pid) renderTopic(await getJSON("/api/graph/topic/" + pid));
      else renderMap(await getJSON("/api/graph/map"));
    } catch (e) {
      message("Не загрузилось: " + e.message);
    }
  }
  function go(params) {
    const q = new URLSearchParams();
    if (params.problem) q.set("problem", params.problem);
    history.pushState(null, "", "/graph.html" + (q.toString() ? "?" + q : ""));
    load();
  }
  addEventListener("popstate", load);
  $("#reload").onclick = load;
  // размер окна меняет вписывание; у доски ветки — и раскладку (колонки)
  let rt = null;
  addEventListener("resize", () => {
    clearTimeout(rt);
    rt = setTimeout(() => (lastStart === startBoard && topicData
      ? renderTopic(topicData) : lastStart()), 150);
  });

  (document.fonts && document.fonts.ready ? document.fonts.ready : Promise.resolve()).then(load);
})();
