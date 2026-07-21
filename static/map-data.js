// Данные карты: таксономия и темы приходят с сервера (/api/taxonomy,
// /api/map/topics). Раньше здесь лежала заглушка — теперь она осталась только
// как ДЕМО-режим, потому что живых тем пока две, а навигацию на двух темах
// оценить нельзя. Переключатель в шапке, режим виден в адресе (?demo=1).

let DOMAINS = [], GEO_TREE = [], GEO_UNIONS = [], ALL_COUNTRIES = [];
let TOPICS = [], DOM_BY_ID = {}, ALL_TAGS = [], GEO_PARENT = {};

// Темы без рубрики не прячем: они существуют, их надо видеть и уметь
// рубрицировать. Псевдо-направление живёт только на клиенте — на сервере у
// такой темы просто нет записи в topic_facets.
const UNSORTED = { id:"__none", name:"Без рубрики", color:"#6f7688", subs:[] };

const DEMO = new URLSearchParams(location.search).get("demo") === "1";

async function apiGet(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(await r.text() || r.status);
  return r.json();
}

async function loadMapData() {
  const tax = await apiGet("/api/taxonomy");
  DOMAINS = tax.domains.concat([UNSORTED]);
  GEO_TREE = tax.geo_tree;
  GEO_UNIONS = tax.unions;
  ALL_COUNTRIES = tax.countries;
  DOM_BY_ID = Object.fromEntries(DOMAINS.map(d => [d.id, d]));

  // Страна -> регионы и части света. Множественное родительство: у России
  // их по два, и оба сохраняются — поэтому массивы, а не одно значение.
  GEO_PARENT = {};
  for (const cont of GEO_TREE)
    for (const r of cont.regions)
      for (const c of r.countries) {
        const slot = GEO_PARENT[c] || (GEO_PARENT[c] = { regions:[], continents:[] });
        if (!slot.regions.includes(r.name)) slot.regions.push(r.name);
        if (!slot.continents.includes(cont.name)) slot.continents.push(cont.name);
      }

  TOPICS = DEMO ? demoTopics() : (await apiGet("/api/map/topics")).map(fromApi);
  ALL_TAGS = [...new Set(TOPICS.flatMap(t => t.tags))]
    .sort((a, b) => a.localeCompare(b, "ru"));
  return TOPICS;
}

// Замыкание географии сервер уже посчитал при записи (страна тянет регионы и
// части света), так что здесь достаточно превратить список в множество.
function fromApi(t) {
  return {
    id: t.id,
    title: (t.title || "").trim() || t.text.slice(0, 90),
    text: t.text,
    domain: t.domain || UNSORTED.id,
    sub: t.sub || "",
    geo: t.geo || [],
    tags: t.tags || [],
    nodes: t.nodes || 0,
    people: t.people || 0,
    poi: t.avg_poi != null ? Math.round(t.avg_poi) : (t.poi_score != null ? Math.round(t.poi_score) : 0),
    author: t.author,
    unsorted: !t.domain,
    _geo: new Set(t.geo || []),
  };
}

// ------------------------------------------------------------------ ДЕМО
function mulberry32(a) {
  return function () {
    a |= 0; a = a + 0x6D2B79F5 | 0;
    let t = Math.imul(a ^ a >>> 15, 1 | a);
    t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
    return ((t ^ t >>> 14) >>> 0) / 4294967296;
  };
}

const FRAMES = [
  s => `${s}: нужно ли вмешательство государства?`,
  s => `${s} — кто должен за это платить?`,
  s => `${s}: где проходит граница допустимого?`,
  s => `Регулировать ли ${s.toLowerCase()} жёстче?`,
  s => `${s}: рынок справится сам?`,
  s => `${s} — что считать успехом?`,
  s => `${s}: нужен ли международный стандарт?`,
  s => `${s} — кому принадлежит решение?`,
  s => `${s}: обязательство или добровольный выбор?`,
  s => `${s} — чем платим за отказ действовать?`,
];
const GEO_FRAMES = [
  (s, g) => `${g} — ${s.toLowerCase()}: менять курс?`,
  (s, g) => `${s} — ${g}: что не работает?`,
  (s, g) => `${g}: пример для остальных в теме «${s.toLowerCase()}»?`,
  (s, g) => `${g} — ${s.toLowerCase()}: чей опыт перенимать?`,
];

function demoTopics() {
  const rnd = mulberry32(20260721);
  const pick = a => a[Math.floor(rnd() * a.length)];
  const out = [];
  let id = -1;
  for (const d of DOMAINS) {
    if (d.id === UNSORTED.id) continue;
    for (const sub of d.subs) {
      const count = 3 + Math.floor(rnd() * 3);
      for (let i = 0; i < count; i++) {
        const geoed = rnd() < 0.34;
        let geo = [], title;
        if (geoed) {
          if (rnd() < 0.25) {
            const u = pick(GEO_UNIONS);
            geo = [u.name]; title = pick(GEO_FRAMES)(sub, u.name);
          } else {
            const c = pick(ALL_COUNTRIES), p = GEO_PARENT[c];
            geo = [c, ...p.regions, ...p.continents];   // то же замыкание, что на сервере
            title = pick(GEO_FRAMES)(sub, c);
          }
        } else title = pick(FRAMES)(sub);

        const tags = [sub.toLowerCase()];
        const heavy = rnd() < 0.12;
        out.push({
          id: id--, title, text: title, domain: d.id, sub, geo, tags,
          nodes:  heavy ? 30 + Math.floor(rnd() * 55) : 1 + Math.floor(rnd() * 18),
          people: heavy ? 12 + Math.floor(rnd() * 20) : 1 + Math.floor(rnd() * 9),
          poi: 45 + Math.floor(rnd() * 40),
          unsorted: false, demo: true, _geo: new Set(geo),
        });
      }
    }
  }
  return out;
}

// Ветка обсуждения для панели: в живом режиме подтягивается настоящая.
const ARGS = [];
async function loadBranch(topicId) {
  if (topicId < 0) return [];                    // демо-тема: ветки нет
  try {
    const page = await apiGet(`/api/nodes/${topicId}/children?limit=40`);
    return (page.children || []).map(c => ({
      id: c.id,
      s: c.rel === "refute" ? "refute" : c.rel === "qualify" ? "qualify" : "support",
      t: c.text,
      who: `${c.author || "—"} · PoI ${c.poi_score != null ? Math.round(c.poi_score) : "—"}`,
    }));
  } catch (_) { return []; }
}
