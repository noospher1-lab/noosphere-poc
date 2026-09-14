import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const REL_COLOR = {
  SUPPORTS:  0x57d98a,
  REBUTS:    0xe25b56,
  UNDERCUTS: 0xe2933f,
  QUALIFIES: 0x5aa9e6,
  QUESTION:  0xb98cff,
  CONCLUDES: 0xd8af6e,
  CAUSES:    0xe0b878,  // «порождает»: связь между ПРОБЛЕМАМИ, корень → корень
};
const TYPE_COLOR = {
  proposal: 0x7fe3d4, // teal — central
  argument: 0x6f8bff, // indigo
  evidence: 0xd8af6e, // bronze
};

// Map the Python backend's lowercase edge types onto the viz' relations.
const REL_FROM_TYPE = { support: 'SUPPORTS', refute: 'REBUTS', qualify: 'QUALIFIES', question: 'QUESTION' };
const CRITERIA = ['clarity', 'depth', 'counterargument', 'evidence', 'awareness_of_limits'];
// Русские подписи критериев — те же, что в дереве (tree.js CRIT_RU). Дублируются,
// а не импортируются: app.js — ES-модуль, tree.js — обычный скрипт другой страницы.
const CRIT_RU = {
  clarity: 'ясность', depth: 'глубина', counterargument: 'работа с возражением',
  evidence: 'обоснованность', awareness_of_limits: 'видит свои границы',
};

// ---- time axis -----------------------------------------------------------
// The dialogue has an order (turn). We lay it along X: left = early, right =
// late. But edges (springs) pull harder than the time bias, so an argument
// attached to an existing node hugs that node regardless of when it arrived —
// "attachments stay visible at their node, not pushed down the timeline".
const AXIS_LEFT = -60, AXIS_RIGHT = 60;
let TURN_MAX = 1;
function timeX(turn) {
  const t = (turn || 0) / (TURN_MAX || 1);
  return AXIS_LEFT + t * (AXIS_RIGHT - AXIS_LEFT);
}
const SOURCE_TINT = { gemini: 0x6f8bff, claude: 0xb98cff, alex: 0xd8af6e, live: 0x57d98a, context: 0x7fe3d4 };

// ---------------------------------------------------------------- scene setup
const app = document.getElementById('app');
const scene = new THREE.Scene();
// No distance fog: zooming out must NOT dim the graph. (FogExp2 faded everything
// to the background colour as the camera pulled back.)

// far-plane 6000, а не 2000: при отъезде дальше двух тысяч единиц дальний край
// графа начинал обрезаться плоскостью отсечения — узлы просто исчезали
const camera = new THREE.PerspectiveCamera(55, innerWidth / innerHeight, 0.1, 6000);
camera.position.set(0, 18, 122); // pulled back so the full time axis fits in frame

const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.setSize(innerWidth, innerHeight);
app.appendChild(renderer.domElement);

const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.dampingFactor = 0.08;
controls.rotateSpeed = 0.6;
controls.minDistance = 12;
// 1200 вместо 220: обсуждение растёт в ширину, и на прежнем пределе ветки
// уходили за экран — отъехать, чтобы увидеть форму спора целиком, было нельзя.
controls.maxDistance = 1200;
controls.enablePan = true;
controls.screenSpacePanning = true; // pan moves along screen plane (incl. the time axis)

scene.add(new THREE.AmbientLight(0x4a5070, 0.9));
const key = new THREE.PointLight(0xffffff, 1.1, 0, 0); key.position.set(40, 60, 50); scene.add(key);
const rim = new THREE.PointLight(0xd8af6e, 0.5, 0, 0); rim.position.set(-50, -20, -40); scene.add(rim);

// faint starfield
(() => {
  const g = new THREE.BufferGeometry();
  const n = 900, pos = new Float32Array(n * 3);
  for (let i = 0; i < n; i++) {
    const r = 300 + Math.random() * 500;
    const t = Math.random() * Math.PI * 2, p = Math.acos(2 * Math.random() - 1);
    pos[i*3] = r*Math.sin(p)*Math.cos(t); pos[i*3+1] = r*Math.sin(p)*Math.sin(t); pos[i*3+2] = r*Math.cos(p);
  }
  g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  scene.add(new THREE.Points(g, new THREE.PointsMaterial({ color: 0x3a4366, size: 1.1, sizeAttenuation: true, transparent: true, opacity: 0.55 })));
})();

// ---------------------------------------------------------------- data + state
// All rebuildable graph content lives in this group, so switching variants is
// a clean teardown + rebuild.
const graphGroup = new THREE.Group();
scene.add(graphGroup);

// Layout cooling: the force sim runs hot on load then settles to a full stop,
// so nodes don't jitter forever. Reheated on variant switch / live add.
let simAlpha = 1, simFrozen = false;
function reheat() { simAlpha = 1; simFrozen = false; }

let NODES = [], EDGES = [];
// One discussion (topic) is shown at a time. liveNorm holds the FULL live graph;
// discussions are its connected components; currentTopicId is the root node of
// the visible one. topicPoi maps author name -> their PoI in THIS topic (lens).
let liveNorm = null, discussions = [], currentTopicId = null, currentSource = 'live';
// «Все ветви»: одна сцена со всеми обсуждениями, деревья соединены связями
// «порождает». Это и есть граф проблем — по умолчанию он, а не одна ветка.
let viewAll = true;
let topicPoi = {}, topicPoiRows = [], lensOn = false, poolsOn = false;
const nodeById = new Map();
const nodeMeshes = new Map();   // id -> { mesh, glow, baseScale, labelEl, node }
const edgeObjs = [];            // { pipe, labelEl, a, b, relation }
const axisLabels = [];          // { el, world } — time-axis phase markers
const raycaster = new THREE.Raycaster();
const pointer = new THREE.Vector2();
let selectedId = null;

const glowTex = makeGlowTexture();

// Normalize the FastAPI /api/graph payload into the shape the viz expects.
// Backend nodes: { id, text, poi_score (0..100), poi_breakdown }
// Backend links: { source, target, type (support|refute|qualify) }
function normalize(data) {
  const rawNodes = data.nodes || [];
  const rawLinks = data.links || data.edges || [];

  const indeg = new Map();
  const outdeg = new Map();
  for (const l of rawLinks) {
    outdeg.set(l.source, (outdeg.get(l.source) || 0) + 1);
    indeg.set(l.target, (indeg.get(l.target) || 0) + 1);
  }
  // The most-referenced claim is treated as the central proposal.
  let proposalId = null, maxIn = 0;
  for (const n of rawNodes) {
    const i = indeg.get(n.id) || 0;
    if (i > maxIn) { maxIn = i; proposalId = n.id; }
  }

  const nodes = rawNodes.map((n) => {
    const inD = indeg.get(n.id) || 0;
    const outD = outdeg.get(n.id) || 0;
    let type;
    if (n.id === proposalId) type = 'proposal';
    else if (inD === 0 && outD > 0) type = 'evidence'; // leaf claims that only support others
    else type = 'argument';
    const score01 = n.poi_score == null ? 0 : n.poi_score / 100;
    const bd = n.poi_breakdown || null;
    return {
      id: n.id,
      label: n.text,
      type,
      // у корня-проблемы заголовок лежит в title — им и подписываем узел
      topic: n.title || n.topic || (bd && bd.topic) || '', // short theme, shown on the node
      turn: n.turn ?? (bd && bd.turn) ?? 0,              // dialogue order -> X position
      source: n.source || (bd && bd.source) || 'context', // alex / gemini / claude / live
      poi_score: score01,                       // 0..1 for the HUD bar
      confidence: maxIn > 0 ? inD / maxIn : 0,   // centrality proxy (in-degree)
      author: n.author || null,
      author_id: n.author_id ?? null,           // для ссылки на профиль автора в HUD
      authorColor: n.author_color || null,
      breakdown: bd,
    };
  });

  const edges = rawLinks.map((l, i) => ({
    id: i + 1,
    source: l.source,
    target: l.target,
    relation: REL_FROM_TYPE[l.type] || 'SUPPORTS',
    thesis: l.thesis || '',                    // the main thesis carried on the edge
  }));

  // связи между проблемами: причина → следствие, оба — корни обсуждений
  const links = (data.problem_links || []).map((l) => ({
    cause: l.cause_id, effect: l.effect_id, node: l.node_id ?? null,
  }));

  return { nodes, edges, links };
}

// Position a set of nodes/edges and build the scene for them.
// fixed=true  -> nodes already have final x/y/z (deterministic tree); freeze sim.
// fixed=false -> seed X by turn, scatter Y/Z, run the force sim (demo variants).
function installGraph(nodes, edges, fixed = false) {
  clearGraph();
  NODES = nodes; EDGES = edges;
  TURN_MAX = NODES.reduce((m, n) => Math.max(m, n.turn || 0), 1);

  NODES.forEach((n) => {
    if (!fixed) {                    // force-sim variants: seed positions here
      n.x = timeX(n.turn);
      n.y = (Math.random() - 0.5) * 50;
      n.z = (Math.random() - 0.5) * 50;
    }
    // fixed: x/y/z were set by the layout function (layoutTree / radialLayout)
    n.vx = n.vy = n.vz = 0;
    nodeById.set(n.id, n);
  });

  buildAxis();
  buildNodeMeshes();
  buildEdges();

  if (fixed) { simFrozen = true; simAlpha = 0; }   // tree layout is final
  else reheat();
}

// Tidy left->right tree layout — "a pipeline with branches". X = depth from the
// root along reply edges (a reply sits one step right of the node it answers),
// so siblings share an X and fan out vertically, each starting from their
// parent. Parents are centered over their children. Time flows left->right.
function layoutTree(nodes, edges, rootId) {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const children = new Map(nodes.map((n) => [n.id, []]));
  // edge: source = the reply, target = the node it answers -> target is parent
  for (const e of edges) {
    if (byId.has(e.source) && byId.has(e.target)) children.get(e.target).push(e.source);
  }
  let root = byId.has(rootId) ? rootId : (nodes[0] && nodes[0].id);
  if (root == null) return;

  // depth via BFS from the root
  const depth = new Map([[root, 0]]);
  const q = [root];
  while (q.length) {
    const p = q.shift();
    for (const c of children.get(p) || []) {
      if (!depth.has(c)) { depth.set(c, depth.get(p) + 1); q.push(c); }
    }
  }
  for (const n of nodes) if (!depth.has(n.id)) depth.set(n.id, 0);

  // lanes: each branch is its own horizontal line. A node's FIRST child stays on
  // the parent's lane (the thread runs straight to the right); every additional
  // child forks onto a fresh lane. So parallel discussions read as parallel rows.
  const lane = new Map();
  let nextLane = 0;
  const visited = new Set();
  const dfs = (id, myLane) => {
    if (visited.has(id)) return;
    visited.add(id);
    lane.set(id, myLane);
    const ch = (children.get(id) || []).filter((c) => !visited.has(c)).sort((a, b) => a - b);
    ch.forEach((c, i) => dfs(c, i === 0 ? myLane : ++nextLane));
  };
  dfs(root, 0);
  for (const n of nodes) if (!lane.has(n.id)) lane.set(n.id, ++nextLane);

  const maxLane = Math.max(1, nextLane);
  const maxDepth = Math.max(1, ...[...depth.values()]);
  const LANE_GAP = 16, X0 = -60, X1 = 60;
  for (const n of nodes) {
    const d = depth.get(n.id) || 0;
    n.turn = d;
    n.x = X0 + (d / maxDepth) * (X1 - X0);               // time left->right
    n.y = (lane.get(n.id) - maxLane / 2) * LANE_GAP;     // each branch on its own row
    n.z = 0;
  }
}

// Radial layout for the positions view: stars on the time spine (left->right),
// planets (questions/details) orbiting their star like planets around a sun;
// sub-branches orbit their parent planet recursively.
function radialLayout(nodes, edges, rootId) {
  const stars = nodes.filter((n) => !n.isPlanet);
  const starIds = new Set(stars.map((n) => n.id));
  const starEdges = edges.filter((e) => starIds.has(e.source) && starIds.has(e.target));
  const childrenS = new Map(stars.map((n) => [n.id, []]));
  for (const e of starEdges) if (childrenS.has(e.target)) childrenS.get(e.target).push(e.source);

  const depth = new Map([[rootId, 0]]), lane = new Map();
  let nextLane = 0; const visited = new Set();
  const dfs = (id, d, myLane) => {
    if (visited.has(id)) return; visited.add(id);
    depth.set(id, d); lane.set(id, myLane);
    const ch = (childrenS.get(id) || []).filter((c) => !visited.has(c)).sort();
    ch.forEach((c, i) => dfs(c, d + 1, i === 0 ? myLane : ++nextLane));
  };
  dfs(rootId, 0, 0);
  for (const n of stars) if (!visited.has(n.id)) { depth.set(n.id, 1); lane.set(n.id, ++nextLane); }

  const maxDepth = Math.max(1, ...[...depth.values()]);
  const maxLane = Math.max(1, nextLane);
  const X0 = -64, X1 = 64, LANE_GAP = 50;
  for (const n of stars) {
    n.x = X0 + (depth.get(n.id) / maxDepth) * (X1 - X0);
    n.y = (lane.get(n.id) - maxLane / 2) * LANE_GAP;
    n.z = 0;
  }

  // planets orbit in 3D — distributed on a forward-facing CONE around the star
  // (using y AND z, not one plane), growing outward; sub-branches continue in
  // their parent's outward direction. cone half-angle < 90° keeps x>0 (forward
  // in time), so branches never fold backwards.
  const norm = (a) => { const l = Math.hypot(a.x, a.y, a.z) || 1; return { x: a.x / l, y: a.y / l, z: a.z / l }; };
  const cross = (a, b) => ({ x: a.y * b.z - a.z * b.y, y: a.z * b.x - a.x * b.z, z: a.x * b.y - a.y * b.x });

  const place = (parent, dirIn, coneHalf, radius) => {
    const kids = nodes.filter((n) => n.isPlanet && n.parentId === parent.id);
    const k = kids.length;
    if (!k) return;
    const d = norm(dirIn);
    const up = Math.abs(d.y) < 0.9 ? { x: 0, y: 1, z: 0 } : { x: 1, y: 0, z: 0 };
    const u = norm(cross(d, up));
    const v = cross(d, u);                 // unit, perpendicular to d and u
    kids.forEach((kid, i) => {
      const psi = (i + 0.5) * (2 * Math.PI / k);          // azimuth around d
      const phi = k === 1 ? coneHalf * 0.35 : coneHalf;   // cone half-angle
      const c = Math.cos(phi), s = Math.sin(phi);
      const dir = {
        x: c * d.x + s * (Math.cos(psi) * u.x + Math.sin(psi) * v.x),
        y: c * d.y + s * (Math.cos(psi) * u.y + Math.sin(psi) * v.y),
        z: c * d.z + s * (Math.cos(psi) * u.z + Math.sin(psi) * v.z),
      };
      kid.x = parent.x + dir.x * radius;
      kid.y = parent.y + dir.y * radius;
      kid.z = parent.z + dir.z * radius;
      place(kid, dir, coneHalf * 0.85, radius * 0.82);     // grow outward in 3D
    });
  };
  for (const s of stars) place(s, { x: 1, y: 0, z: 0 }, 1.0, 16);  // forward +x, ~57° cone
}

async function loadGraph(source) {
  currentSource = source;
  if (source === 'live') return loadLive();
  let data;
  try { data = await (await fetch(`/variants/${source}.json`)).json(); }
  catch (e) { console.error('graph load failed:', source, e); return; }
  const norm = normalize(data);
  installGraph(norm.nodes, norm.edges);
}

// Live graph: load the whole thing, split into discussions, show only one.
async function loadLive() {
  let data;
  try { data = await (await fetch('/api/graph')).json(); }
  catch (e) { console.error('live load failed', e); return; }
  liveNorm = normalize(data);
  discussions = computeDiscussions(liveNorm);
  if (!discussions.some((d) => d.rootId === currentTopicId)) {
    currentTopicId = discussions.length ? discussions[0].rootId : null;
  }
  populateTopicMenu();
  await renderCurrent();
}

async function renderCurrent() {
  if (poolsOn) return buildPoolGraph(false);
  if (viewAll) return renderAll();
  return renderDiscussion();
}

// Корень обсуждения, в котором стоит узел, — в режиме «все ветви»
// currentTopicId не отвечает на этот вопрос.
function rootOf(nodeId) {
  const d = discussions.find((x) => x.ids.has(nodeId));
  return d ? d.rootId : currentTopicId;
}

// Connected components of the (undirected) graph. The root of each is the node
// that never appears as an edge SOURCE (it supports nothing) — i.e. the
// proposal everything points to. That root id identifies the topic.
function computeDiscussions(norm) {
  const adj = new Map();
  for (const n of norm.nodes) adj.set(n.id, []);
  for (const e of norm.edges) {
    if (adj.has(e.source) && adj.has(e.target)) {
      adj.get(e.source).push(e.target);
      adj.get(e.target).push(e.source);
    }
  }
  const seen = new Set(), comps = [];
  for (const n of norm.nodes) {
    if (seen.has(n.id)) continue;
    const ids = new Set(), stack = [n.id];
    while (stack.length) {
      const x = stack.pop();
      if (ids.has(x)) continue;
      ids.add(x); seen.add(x);
      for (const y of adj.get(x)) if (!ids.has(y)) stack.push(y);
    }
    const srcSet = new Set(norm.edges.filter((e) => ids.has(e.source)).map((e) => e.source));
    let rootId = [...ids].find((id) => !srcSet.has(id));
    if (rootId == null) rootId = [...ids][0];
    const rootNode = norm.nodes.find((x) => x.id === rootId);
    const title = (rootNode && (rootNode.topic || truncate(rootNode.label, 26))) || ('Проблема ' + rootId);
    comps.push({ rootId, title, ids });
  }
  return comps;
}

function populateTopicMenu() {
  const sel = document.getElementById('topicsel');
  if (!sel) return;
  sel.innerHTML = `<option value="all">Все ветви · ${discussions.length}</option>` +
    discussions.map((d) =>
      `<option value="${d.rootId}">${d.title} · ${d.ids.size}</option>`).join('');
  sel.value = viewAll ? 'all' : String(currentTopicId);
}

// ВСЕ ветви в одной сцене. Каждое обсуждение раскладывается своим деревом
// (layoutTree: время слева направо, ветки по строкам), а деревья ставятся
// друг относительно друга по связям «порождает»: причина ЛЕВЕЕ следствия —
// причина появляется раньше, следствие после, та же ось, что и время внутри
// дерева. Одна причинная цепочка — одна плоскость; несвязанные цепочки
// уходят вглубь по Z, одиночные обсуждения без связей — общей плоскостью
// сзади. Уровень проблемы нигде не хранится: он здесь ровно столько, сколько
// шагов «порождает» отделяет её от самой левой.
const BAND = 170, TREE_GAP_Y = 34, PLANE_GAP_Z = 60;
async function renderAll() {
  if (!liveNorm) { installGraph([], []); return; }
  const trees = new Map();                       // rootId -> {nodes, edges, minY, maxY}
  for (const d of discussions) {
    const nodes = liveNorm.nodes.filter((n) => d.ids.has(n.id)).map((n) => ({ ...n }));
    const edges = liveNorm.edges.filter((e) => d.ids.has(e.source) && d.ids.has(e.target)).map((e) => ({ ...e }));
    layoutTree(nodes, edges, d.rootId);
    const ys = nodes.map((n) => n.y);
    trees.set(d.rootId, { nodes, edges, minY: Math.min(0, ...ys), maxY: Math.max(0, ...ys) });
  }
  // связи только между живыми корнями
  const links = (liveNorm.links || []).filter((l) => trees.has(l.cause) && trees.has(l.effect));
  const causesOf = new Map(), effectsOf = new Map();
  for (const l of links) {
    (causesOf.get(l.effect) || causesOf.set(l.effect, []).get(l.effect)).push(l.cause);
    (effectsOf.get(l.cause) || effectsOf.set(l.cause, []).get(l.cause)).push(l.effect);
  }
  // цепочки = связные компоненты по связям (без направления)
  const seen = new Set(), chains = [];
  for (const rootId of trees.keys()) {
    if (seen.has(rootId)) continue;
    const ids = [], stack = [rootId];
    while (stack.length) {
      const x = stack.pop();
      if (seen.has(x)) continue;
      seen.add(x); ids.push(x);
      for (const y of [...(causesOf.get(x) || []), ...(effectsOf.get(x) || [])]) if (!seen.has(y)) stack.push(y);
    }
    chains.push(ids);
  }
  // глубина = самый длинный путь от корней без причин (при цикле — как дошли)
  const depth = new Map();
  for (const ids of chains) {
    const q = ids.filter((id) => !(causesOf.get(id) || []).length);
    for (const id of q) depth.set(id, 0);
    let guard = ids.length * ids.length + 1;
    while (q.length && guard--) {
      const x = q.shift();
      for (const y of effectsOf.get(x) || []) {
        const dY = depth.get(x) + 1;
        if ((depth.get(y) ?? -1) < dY && dY <= ids.length) { depth.set(y, dY); q.push(y); }
      }
    }
    for (const id of ids) if (!depth.has(id)) depth.set(id, 0);
  }
  // расстановка: связанные цепочки — каждая своей плоскостью спереди, все
  // одиночки — одной плоскостью сзади (иначе десять проблем — десять слоёв)
  const linked = chains.filter((c) => c.length > 1).sort((a, b) => b.length - a.length);
  const singles = chains.filter((c) => c.length === 1).flat();
  const planes = [...linked, ...(singles.length ? [singles] : [])];
  const allNodes = [], allEdges = [];
  planes.forEach((ids, pi) => {
    // каждая следующая плоскость глубже И выше: при взгляде спереди слои
    // читаются лесенкой, а не накладываются друг на друга
    const z = -pi * PLANE_GAP_Z, yPlane = pi * PLANE_GAP_Z * 0.6;
    const cols = new Map();                      // depth -> [rootId]
    for (const id of ids) (cols.get(depth.get(id)) || cols.set(depth.get(id), []).get(depth.get(id))).push(id);
    for (const [dep, roots] of cols) {
      roots.sort((a, b) => a - b);
      const heights = roots.map((r) => trees.get(r).maxY - trees.get(r).minY);
      const total = heights.reduce((s, h) => s + h, 0) + TREE_GAP_Y * (roots.length - 1);
      let yTop = total / 2;
      roots.forEach((r, i) => {
        const t = trees.get(r);
        const dy = yTop - t.maxY + yPlane;       // верх дерева на текущей высоте
        for (const n of t.nodes) { n.x += dep * BAND; n.y += dy; n.z = z; allNodes.push(n); }
        allEdges.push(...t.edges);
        yTop -= heights[i] + TREE_GAP_Y;
      });
    }
  });
  // ОБОСНОВАНИЕ связи — ответ в обсуждении следствия, из которого связь
  // родилась. Раскладчик ставит его веткой справа от следствия, и рядом с
  // проблемой-причиной слева это читается как одна и та же запись дважды.
  // По смыслу же это аргумент САМОЙ связи, поэтому оно встаёт НА связь:
  // причина —порождает→ обоснование —за→ следствие. Свою ветку (спор о
  // связи) обоснование уводит за собой.
  const byId = new Map(allNodes.map((n) => [n.id, n]));
  const kids = new Map();
  for (const e of allEdges) (kids.get(e.target) || kids.set(e.target, []).get(e.target)).push(e.source);
  links.forEach((l, i) => {
    const c = byId.get(l.cause), ef = byId.get(l.effect), j = l.node != null ? byId.get(l.node) : null;
    if (j && j !== ef && j !== c) {
      const tx = (c.x + ef.x) / 2, ty = ef.y, tz = ef.z;
      const dx = tx - j.x, dy = ty - j.y, dz = tz - j.z;
      const stack = [j.id], moved = new Set();
      while (stack.length) {
        const id = stack.pop();
        if (moved.has(id)) continue;
        moved.add(id);
        const n = byId.get(id); n.x += dx; n.y += dy; n.z += dz;
        for (const k of kids.get(id) || []) stack.push(k);
      }
      allEdges.push({ id: 'pl' + i, source: l.cause, target: j.id, relation: 'CAUSES', thesis: '' });
    } else {
      allEdges.push({ id: 'pl' + i, source: l.cause, target: l.effect, relation: 'CAUSES', thesis: '' });
    }
  });
  installGraph(allNodes, allEdges, true);
  fitCamera(allNodes);
}

// Камера отъезжает так, чтобы вся сцена была в кадре; иначе после первой
// ширины экрана правые цепочки просто не видны.
function fitCamera(nodes) {
  if (!nodes.length) return;
  const xs = nodes.map((n) => n.x), ys = nodes.map((n) => n.y), zs = nodes.map((n) => n.z);
  const cx = (Math.min(...xs) + Math.max(...xs)) / 2, cy = (Math.min(...ys) + Math.max(...ys)) / 2;
  const cz = (Math.min(...zs) + Math.max(...zs)) / 2;
  const w = Math.max(...xs) - Math.min(...xs), h = Math.max(...ys) - Math.min(...ys);
  const dist = Math.max(122, w * 0.75, h * 1.3) + (Math.max(...zs) - cz);
  controls.target.set(cx, cy, cz);
  camera.position.set(cx, cy + 18, cz + dist);
  controls.update();
}

// Filter the full live graph down to the current discussion and build it.
async function renderDiscussion() {
  if (!liveNorm || currentTopicId == null) { installGraph([], []); return; }
  const d = discussions.find((x) => x.rootId === currentTopicId);
  if (!d) return;
  const nodes = liveNorm.nodes.filter((n) => d.ids.has(n.id)).map((n) => ({ ...n }));
  const edges = liveNorm.edges.filter((e) => d.ids.has(e.source) && d.ids.has(e.target)).map((e) => ({ ...e }));
  // Deterministic tree: a reply branches off the node it answers (X = depth).
  layoutTree(nodes, edges, currentTopicId);
  installGraph(nodes, edges, true);
}

// Tear down all rebuildable content (meshes, pipes, DOM labels, pulses).
function clearGraph() {
  for (const nm of nodeMeshes.values()) nm.labelEl && nm.labelEl.remove();
  for (const e of edgeObjs) e.labelEl && e.labelEl.remove();
  for (const a of axisLabels) a.el.remove();
  for (const p of pulses) scene.remove(p.mesh);
  pulses.length = 0;
  while (graphGroup.children.length) {
    const o = graphGroup.children[0];
    graphGroup.remove(o);
    o.traverse && o.traverse((c) => { if (c.material) c.material.dispose(); });
    if (o.geometry && o.geometry !== sphereGeo && o.geometry !== pipeGeo) o.geometry.dispose();
  }
  nodeMeshes.clear(); nodeById.clear();
  edgeObjs.length = 0; axisLabels.length = 0;
  NODES = []; EDGES = [];
  selectedId = null;
  hud.classList.remove('open');
}

async function boot() {
  await loadGraph('live');
  startStream();
  connectEvents();
  animate();

  document.querySelectorAll('#variants button').forEach((btn) => {
    btn.onclick = async () => {
      if (btn.classList.contains('active')) return;
      document.querySelectorAll('#variants button').forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      await loadGraph(btn.dataset.src);
      // the topic switcher only applies to the live graph
      document.getElementById('topics').style.display = btn.dataset.src === 'live' ? 'flex' : 'none';
    };
  });
}

// ---------------------------------------------------------------- build meshes
const sphereGeo = new THREE.SphereGeometry(1, 32, 32);
// Unit pipe (open-ended cylinder along +Y, height 1) reused for every edge.
const pipeGeo = new THREE.CylinderGeometry(0.34, 0.34, 1, 10, 1, true);

function nodeColor(n) {
  // Author tint wins: nodes are coloured by who posted them (test personas),
  // so you can see at a glance which user an argument came from.
  if (n.authorColor) return new THREE.Color(n.authorColor);
  return new THREE.Color(SOURCE_TINT[n.source] ?? TYPE_COLOR[n.type] ?? 0x8899aa);
}

function addNodeMesh(n) {
  const isProp = n.type === 'proposal';
  const base = isProp ? 3.0 : (n.isPlanet ? 0.9 : 1.5 + n.confidence * 1.2);
  const color = nodeColor(n);

  const mat = new THREE.MeshStandardMaterial({
    color, roughness: 0.45, metalness: 0.1,
    emissive: color.clone().multiplyScalar(0.35), emissiveIntensity: 1.0,
  });
  const mesh = new THREE.Mesh(sphereGeo, mat);
  mesh.scale.setScalar(base);
  mesh.userData.id = n.id;
  graphGroup.add(mesh);

  const glow = new THREE.Sprite(new THREE.SpriteMaterial({
    map: glowTex, color, transparent: true, opacity: isProp ? 0.85 : 0.55,
    blending: THREE.AdditiveBlending, depthWrite: false,
  }));
  glow.scale.setScalar(base * 4.2);
  mesh.add(glow);

  // Short thesis/topic shown at the node; full text appears in the HUD on click.
  const labelEl = document.createElement('div');
  labelEl.className = 'node-label' + (isProp ? ' prop' : '');
  labelEl.textContent = nodeLabelText(n);
  // клик по подписи — то же, что клик по шару: открыть узел в панели
  labelEl.addEventListener('click', () => {
    const node = nodeById.get(n.id);
    if (!node) return;
    openHud(node);
    triggerNodePulse(n.id, 'SUPPORTS');
  });
  document.body.appendChild(labelEl);

  nodeMeshes.set(n.id, { mesh, glow, baseScale: base, pulse: 0, labelEl, node: n });
}

// Подпись узла — ЦЕЛИКОМ: заголовок (у проблемы — title, у довода — краткий
// тезис от модели), а без него — первое предложение текста, как в дереве.
// Обрезка на 28 знаках делала заголовки нечитаемыми; блок переносит строки.
function nodeLabelText(n) {
  if (n.topic && n.topic.trim()) return n.topic.trim();
  const t = (n.label || '').trim();
  const m = t.match(/^.*?[.!?](?=\s|$)/);
  const s = m ? m[0] : t;
  return s.length > 160 ? s.slice(0, 159).trimEnd() + '…' : s;
}

function addEdgeObj(e) {
  const a = nodeById.get(e.source), b = nodeById.get(e.target);
  if (!a || !b) return;
  const col = new THREE.Color(REL_COLOR[e.relation] ?? 0x888888);
  // Edges are pipes: a metallic tube the relation's colour. Pulses run through
  // them like flow, so the graph reads as a pipeline carrying the argument.
  const mat = new THREE.MeshStandardMaterial({
    color: col, roughness: 0.3, metalness: 0.75,
    emissive: col.clone().multiplyScalar(0.28), emissiveIntensity: 1.0,
    transparent: true, opacity: 0.52,
  });
  const pipe = new THREE.Mesh(pipeGeo, mat);
  graphGroup.add(pipe);

  // The pipe's COLOR encodes the relation (support/refute/qualify). No text label
  // on the edge — it duplicated the node's topic and cluttered the view.
  edgeObjs.push({ pipe, labelEl: null, a, b, relation: e.relation, color: col });
}

function buildNodeMeshes() { for (const n of NODES) addNodeMesh(n); }
function buildEdges() { for (const e of EDGES) addEdgeObj(e); }

// A faint baseline running along the time axis, with phase markers projected
// as DOM labels (GEMINI review · CLAUDE stress-test · time →).
function buildAxis() {
  // «все ветви»: ось тянется под всей сценой и подписана как причинная —
  // причина раньше следствия, и это та же ось, что время внутри дерева
  const all = viewAll && currentSource === 'live' && !poolsOn && NODES.length;
  const y = all ? Math.min(...NODES.map((n) => n.y)) - 14 : -28;
  const x0 = all ? Math.min(...NODES.map((n) => n.x)) : timeX(0);
  const x1 = all ? Math.max(...NODES.map((n) => n.x), x0 + 1) : timeX(TURN_MAX);
  if (all) {
    const base = new THREE.Mesh(
      new THREE.CylinderGeometry(0.1, 0.1, x1 - x0, 6, 1, true),
      new THREE.MeshBasicMaterial({ color: 0x3a4366, transparent: true, opacity: 0.45 }),
    );
    base.position.set((x0 + x1) / 2, y, 0);
    base.rotation.z = Math.PI / 2;
    graphGroup.add(base);
    const el = document.createElement('div');
    el.className = 'axis-label t';
    el.textContent = 'причины → следствия · время →';
    document.body.appendChild(el);
    // ниже линии, а не на высоте узлов — иначе наезжает на крайний правый узел
    axisLabels.push({ el, world: new THREE.Vector3(x1, y - 12, 0) });
    return;
  }
  const base = new THREE.Mesh(
    new THREE.CylinderGeometry(0.1, 0.1, x1 - x0, 6, 1, true),
    new THREE.MeshBasicMaterial({ color: 0x3a4366, transparent: true, opacity: 0.45 }),
  );
  base.position.set((x0 + x1) / 2, y, 0);
  base.rotation.z = Math.PI / 2;
  graphGroup.add(base);

  const mk = (text, turn, cls) => {
    const el = document.createElement('div');
    el.className = 'axis-label' + (cls ? ' ' + cls : '');
    el.textContent = text;
    document.body.appendChild(el);
    axisLabels.push({ el, world: new THREE.Vector3(timeX(turn), y - 4, 0) });
  };
  // phase markers placed at the average turn of each AI's nodes
  const mid = (src) => {
    const ts = NODES.filter((n) => n.source === src).map((n) => n.turn);
    return ts.length ? ts.reduce((a, b) => a + b, 0) / ts.length : null;
  };
  const gm = mid('gemini'), cm = mid('claude');
  if (gm != null) mk('GEMINI · обзор', gm, 'g');
  if (cm != null) mk('CLAUDE · стресс-тест', cm, 'c');
  mk('время →', TURN_MAX, 't');
}

// ---------------------------------------------------------------- force sim (3D)
// Simple velocity-Verlet-ish: repulsion (Coulomb) + spring on edges + centering.
function tickForces(dt, alpha) {
  // SPRING (edges) is intentionally strong vs TIME_PULL so that arguments
  // attached to a node hug that node instead of sliding to their own time slot.
  const REP = 1700, SPRING = 0.035, REST = 17, CENTER = 0.012, TIME_PULL = 0.014, DAMP = 0.86;
  for (let i = 0; i < NODES.length; i++) {
    const a = NODES[i];
    for (let j = i + 1; j < NODES.length; j++) {
      const b = NODES[j];
      let dx = a.x-b.x, dy = a.y-b.y, dz = a.z-b.z;
      let d2 = dx*dx + dy*dy + dz*dz + 0.01;
      const f = REP / d2, d = Math.sqrt(d2);
      const ux = dx/d, uy = dy/d, uz = dz/d;
      a.vx += ux*f*dt; a.vy += uy*f*dt; a.vz += uz*f*dt;
      b.vx -= ux*f*dt; b.vy -= uy*f*dt; b.vz -= uz*f*dt;
    }
  }
  for (const e of EDGES) {
    const a = nodeById.get(e.source), b = nodeById.get(e.target);
    if (!a || !b) continue;
    let dx = b.x-a.x, dy = b.y-a.y, dz = b.z-a.z;
    const d = Math.sqrt(dx*dx+dy*dy+dz*dz) + 0.001;
    const f = (d - REST) * SPRING;
    const ux = dx/d, uy = dy/d, uz = dz/d;
    a.vx += ux*f; a.vy += uy*f; a.vz += uz*f;
    b.vx -= ux*f; b.vy -= uy*f; b.vz -= uz*f;
  }
  for (const n of NODES) {
    // X is LOCKED to the time axis — readability comes from everything being
    // anchored to time. Only Y,Z settle (repulsion spreads forks; edge springs
    // pull a reply near its parent vertically).
    n.x = timeX(n.turn); n.vx = 0;
    if (n.pinned) { n.vy = n.vz = 0; n.y = n.z = 0; continue; }
    n.vy -= n.y*CENTER; n.vz -= n.z*CENTER;
    n.vy *= DAMP; n.vz *= DAMP;
    n.y += n.vy*dt*60*alpha; n.z += n.vz*dt*60*alpha;
  }
}

// ---------------------------------------------------------------- pulses
const pulses = []; // { a, b, color, t, speed, relation }
const pulseGeo = new THREE.SphereGeometry(0.7, 16, 16);

function spawnPulse(srcId, dstId, relation) {
  const a = nodeById.get(srcId), b = nodeById.get(dstId);
  if (!a || !b) return;
  const color = new THREE.Color(REL_COLOR[relation] ?? 0xffffff);
  const mat = new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 1, blending: THREE.AdditiveBlending, depthWrite: false });
  const mesh = new THREE.Mesh(pulseGeo, mat);
  scene.add(mesh);
  const halo = new THREE.Sprite(new THREE.SpriteMaterial({ map: glowTex, color, transparent: true, opacity: 0.9, blending: THREE.AdditiveBlending, depthWrite: false }));
  halo.scale.setScalar(5); mesh.add(halo);
  pulses.push({ a, b, mesh, t: 0, speed: 1.1 + Math.random()*0.4, relation });
}

function updatePulses(dt) {
  for (let i = pulses.length - 1; i >= 0; i--) {
    const p = pulses[i];
    p.t += dt * p.speed;
    const k = Math.min(p.t, 1);
    p.mesh.position.set(
      p.a.x + (p.b.x - p.a.x) * k,
      p.a.y + (p.b.y - p.a.y) * k,
      p.a.z + (p.b.z - p.a.z) * k
    );
    if (p.t >= 1) {
      triggerNodePulse(p.b.id, p.relation);
      scene.remove(p.mesh); p.mesh.material.dispose();
      pulses.splice(i, 1);
    }
  }
}

function triggerNodePulse(id, relation) {
  const nm = nodeMeshes.get(id);
  if (!nm) return;
  nm.pulse = 1; nm.pulseColor = new THREE.Color(REL_COLOR[relation] ?? 0xffffff);
}

// ---------------------------------------------------------------- live pulses
// The FastAPI backend has no SSE stream, so the "live" feel is produced here:
// real edges from the graph fire decorative pulses. No score is mutated — the
// PoI numbers shown are exactly what the LLM assigned.
function startStream() {
  const feed = document.getElementById('feed');
  if (!EDGES.length) return;
  const tick = () => {
    const e = EDGES[Math.floor(Math.random() * EDGES.length)];
    spawnPulse(e.source, e.target, e.relation);

    const tgt = nodeById.get(e.target);
    const line = document.createElement('div');
    line.className = 'line';
    line.innerHTML = `<span class="tag-${e.relation}">${e.relation}</span> → ${truncate(tgt?.label ?? '', 22)}`;
    feed.appendChild(line);
    setTimeout(() => line.remove(), 4200);
    while (feed.childNodes.length > 6) feed.removeChild(feed.firstChild);

    setTimeout(tick, 1600 + Math.random() * 2200);
  };
  setTimeout(tick, 800);
}

// ---------------------------------------------------------------- live events
// Real additions on the FastAPI backend (POST /api/argument, /api/edge) are
// pushed over SSE. A new argument materializes as a fresh node and fires a
// pulse along its edge — the graph grows as people actually contribute.
function connectEvents() {
  const es = new EventSource('/api/events');
  es.onmessage = (ev) => {
    let d;
    try { d = JSON.parse(ev.data); } catch { return; }
    if (d.type === 'node_added') onNodeAdded(d);
    else if (d.type === 'edge_added') onEdgeAdded(d);
  };
  es.onerror = () => {/* EventSource auto-reconnects */};
}

function addLiveNode(raw, edge) {
  if (nodeById.has(raw.id)) return nodeById.get(raw.id);
  const score01 = raw.poi_score == null ? 0 : raw.poi_score / 100;
  const bd = raw.poi_breakdown || null;
  const n = {
    id: raw.id,
    label: raw.text,
    type: 'argument',
    topic: (bd && bd.topic) || '',
    turn: (bd && bd.turn) || TURN_MAX + 1,   // newest -> right end of the axis
    source: (bd && bd.source) || 'live',
    poi_score: score01,
    confidence: 0.5,
    author: raw.author || null,
    authorColor: raw.author_color || null,
    weight: raw.weight ?? null,
    breakdown: bd,
  };
  const anchor = edge ? nodeById.get(edge.target_id) : null;
  const base = anchor || { x: 0, y: 0, z: 0 };
  n.x = base.x + (Math.random() - 0.5) * 22;
  n.y = base.y + (Math.random() - 0.5) * 22;
  n.z = base.z + (Math.random() - 0.5) * 22;
  n.vx = n.vy = n.vz = 0;
  NODES.push(n);
  nodeById.set(n.id, n);
  addNodeMesh(n);
  triggerNodePulse(n.id, 'SUPPORTS');
  reheat();
  return n;
}

function addLiveEdge(edge) {
  const relation = REL_FROM_TYPE[edge.type] || 'SUPPORTS';
  EDGES.push({ id: EDGES.length + 1, source: edge.source_id, target: edge.target_id, relation });
  addEdgeObj({ source: edge.source_id, target: edge.target_id, relation });
  spawnPulse(edge.source_id, edge.target_id, relation);
  return relation;
}

function logFeed(html) {
  const feed = document.getElementById('feed');
  const line = document.createElement('div');
  line.className = 'line';
  line.innerHTML = html;
  feed.appendChild(line);
  setTimeout(() => line.remove(), 4200);
  while (feed.childNodes.length > 6) feed.removeChild(feed.firstChild);
}

let liveRefreshT = null;
function scheduleLiveRefresh() {
  clearTimeout(liveRefreshT);
  liveRefreshT = setTimeout(() => loadLive(), 400);
}

function onNodeAdded(d) {
  const poi = (d.node.poi_score ?? 0).toFixed(0);
  logFeed(`<span class="tag-SUPPORTS">NEW</span> ${truncate(d.node.text || '', 18)} <b>PoI ${poi}</b>`);
  if (currentSource === 'live') {
    // A new argument may join this discussion or start a new topic — recompute
    // discussions and re-render (stays on the current topic).
    scheduleLiveRefresh();
  } else {
    addLiveNode(d.node, d.edge);
    if (d.edge) addLiveEdge(d.edge);
  }
  if (typeof refreshWeights === 'function') refreshWeights();
}

function onEdgeAdded(d) {
  if (currentSource === 'live') { scheduleLiveRefresh(); return; }
  const relation = addLiveEdge(d.edge);
  const tgt = nodeById.get(d.edge.target_id);
  logFeed(`<span class="tag-${relation}">${relation}</span> → ${truncate(tgt?.label ?? '', 18)}`);
}

// ---------------------------------------------------------------- HUD
const hud = document.getElementById('hud');

// Строка над заголовком панели. Раньше туда шло n.source, а у узлов из графа
// это служебное 'context' — панель подписывала любой узел «CONTEXT».
const REL_RU = { SUPPORTS: 'за', REBUTS: 'возражение', UNDERCUTS: 'не доказывает',
                 QUALIFIES: 'уточнение', QUESTION: 'вопрос', CONCLUDES: 'вывод' };
function nodeKindText(n) {
  if (n.source && n.source !== 'context') return n.source;   // варианты диалога
  if (n.type === 'proposal') return 'проблема';
  const out = edgeObjs.find((e) => e.a && e.a.id === n.id && e.relation !== 'CAUSES');
  const rel = out && REL_RU[out.relation];
  return rel ? `довод · ${rel}` : 'довод';
}
function openHud(n) {
  selectedId = n.id;
  hud.classList.add('open');

  // Pool / planet nodes hide the per-argument meters, reactions and reply.
  const isPool = !!n.isPool;
  const isPlanet = !!n.isPlanet;
  const special = isPool || isPlanet;
  hud.querySelectorAll('.meter').forEach((m) => { m.style.display = special ? 'none' : ''; });
  document.getElementById('hud-reactions').style.display = special ? 'none' : '';
  document.getElementById('hud-reply').style.display = special ? 'none' : '';
  if (isPool) { renderPoolHud(n); return; }
  if (isPlanet) { renderPlanetHud(n); return; }

  replyTargetId = n.id;   // an argument node is the reply target
  document.getElementById('hud-type').textContent = nodeKindText(n);
  // heading = short topic; the full verbatim dialogue text goes in the body
  document.getElementById('hud-label').textContent =
    (n.topic && n.topic.trim()) ? n.topic : truncate(n.label || '', 48);

  const c = document.getElementById('hud-content');
  const b = n.breakdown || {};
  const scored = CRITERIA.filter((k) => k in b);
  if (scored.length) {
    // Real LLM scoring: full argument text FIRST, then the evaluator's note,
    // then the per-criterion breakdown.
    const text = `<div style="margin-bottom:10px;color:#ece6d8;white-space:pre-wrap">${escapeHtml(n.label || '')}</div>`;
    const comment = b.comment ? `<div style="margin-bottom:8px;color:#8a8576;font-style:italic">${escapeHtml(b.comment)}</div>` : '';
    const rows = scored.map((k) =>
      `<div style="display:flex;justify-content:space-between;margin-bottom:2px">
         <span style="color:#8a8576">${CRIT_RU[k] || k}</span>
         <span style="color:#ece6d8;font-variant-numeric:tabular-nums">${b[k]}</span>
       </div>`).join('');
    c.innerHTML = text + comment + rows;
  } else {
    // dialogue variants: every word, readable and scrollable
    c.textContent = n.label || '—';
  }

  // имя автора → его публичный профиль (история голосований), новая вкладка.
  // «PoI автора в теме», «Вес аргумента», Centrality убраны — старая модель/мертво.
  const authEl = document.getElementById('hud-author');
  authEl.innerHTML = '';
  if (n.author_id) {
    const a = document.createElement('a');
    a.textContent = n.author || '—';
    a.href = '/profile.html?id=' + n.author_id;
    a.target = '_blank'; a.style.color = 'var(--bronze)';
    authEl.appendChild(a);
  } else { authEl.textContent = n.author || '—'; }

  updateHudScore(n.poi_score, 0);
  updateArgTarget();   // a node is now selected — reflect it in the add form
  loadReactions(n.id); // PoI-weighted votes on this node
}

// Vote weight mirrored from app/voteweight.py: weight = sqrt(100) * poi/100,
// i.e. poi/10. Author-independent — the author's PoI does NOT enter here.
function computeWeight(poiScore01) {
  return (poiScore01 || 0) * 10;
}
function updateHudScore(score, delta) {
  // null-safe: проблема и ещё не оценённый узел приходят с poi_score = null
  const s = (score == null || isNaN(score)) ? null : score;
  document.getElementById('hud-poi').textContent = s == null ? '…' : s.toFixed(2);
  const fill = document.getElementById('hud-poi-fill');
  fill.style.width = `${(s || 0)*100}%`;
  fill.classList.remove('up','down');
  if (delta > 0) fill.classList.add('up'); else if (delta < 0) fill.classList.add('down');
}
document.getElementById('hud-close').onclick = () => { hud.classList.remove('open'); selectedId = null; };

renderer.domElement.addEventListener('pointerdown', (e) => {
  // Координаты клика — ОТНОСИТЕЛЬНО канваса, а не окна: канвас смещён вниз под
  // шапку (~49px). Без вычета offset рейкастер целился на ~49px выше узла, и
  // обычный клик по узлу всегда промахивался. getBoundingClientRect надёжен при
  // любом смещении и размере.
  const rect = renderer.domElement.getBoundingClientRect();
  pointer.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
  pointer.y = -((e.clientY - rect.top) / rect.height) * 2 + 1;
  raycaster.setFromCamera(pointer, camera);
  // non-recursive: hit only the node spheres, not their glow sprites (which
  // carry no id and otherwise swallow clicks)
  const hits = raycaster.intersectObjects([...nodeMeshes.values()].map((m) => m.mesh), false);
  const hit = hits.find((h) => h.object.userData.id != null);
  if (hit) {
    const id = hit.object.userData.id;
    openHud(nodeById.get(id));
    triggerNodePulse(id, 'SUPPORTS');
  }
});

// ---------------------------------------------------------------- render loop
const tmp = new THREE.Vector3();
const edgeDir = new THREE.Vector3();
const PIPE_UP = new THREE.Vector3(0, 1, 0);
let last = performance.now();
// Подписи не налезают друг на друга и не уезжают от своих узлов. Раньше при
// пересечении подпись поднималась над соседней — до 12 раз, и в плотной части
// графа подписи вставали столбиком далеко над узлами, где уже не понять, чья
// какая. Теперь у подписи несколько мест рядом с её узлом: над ним, под ним,
// на ступень выше, на ступень ниже. Все заняты — подпись в этом кадре не
// показывается и вернётся, когда камера подъедет и места станет больше.
// Порядок: проблемы, выбранный узел, затем ближние к камере — их прячем последними.
function layoutLabels(placed) {
  placed.sort((a, b) => (b.prop - a.prop) || (b.selected - a.selected) || (a.depth - b.depth));
  const rects = [];
  const GAP = 3;
  const hits = (left, right, top, bottom) =>
    rects.some((r) => left < r.right && right > r.left && top < r.bottom && bottom > r.top);
  for (const p of placed) {
    const w = p.el.offsetWidth || 80, h = p.el.offsetHeight || 16;
    const left = p.x - w / 2, right = p.x + w / 2;
    // якорь — нижний край блока: над узлом он у p.y, под узлом — у p.yBelow + h
    const slots = p.prop
      ? [p.y]                                   // подпись проблемы не двигается
      : [p.y, p.yBelow + h, p.y - (h + GAP), p.yBelow + 2 * h + GAP];
    const bottom = slots.find((b) => !hits(left, right, b - h, b));
    if (bottom === undefined) { p.el.classList.remove('show'); continue; }
    rects.push({ left, right, top: bottom - h, bottom });
    p.el.style.left = `${p.x}px`;
    p.el.style.top = `${bottom}px`;
    p.el.classList.add('show');
  }
}

function animate() {
  requestAnimationFrame(animate);
  const now = performance.now();
  const dt = Math.min((now - last) / 1000, 0.05); last = now;

  if (!simFrozen) {
    tickForces(dt, simAlpha);
    simAlpha *= 0.985;
    if (simAlpha < 0.0025) { simFrozen = true; for (const n of NODES) n.vx = n.vy = n.vz = 0; }
  }
  updatePulses(dt);

  // sync node meshes + pulse decay
  const placed = [];                 // подписи этого кадра — см. layoutLabels
  for (const n of NODES) {
    const nm = nodeMeshes.get(n.id);
    nm.mesh.position.set(n.x, n.y, n.z);
    if (nm.pulse > 0) {
      nm.pulse = Math.max(0, nm.pulse - dt * 2.2);
      const s = nm.baseScale * (1 + nm.pulse * 0.5);
      nm.mesh.scale.setScalar(s);
      nm.mesh.material.emissiveIntensity = 1 + nm.pulse * 2.5;
      if (nm.pulseColor) nm.glow.material.color.lerpColors(nodeColor(n), nm.pulseColor, nm.pulse);
    } else {
      nm.mesh.scale.setScalar(nm.baseScale);
      nm.mesh.material.emissiveIntensity = 1;
    }
    // node label (full title), billboarded just above the sphere — its screen
    // position is collected here and applied after collision resolution below
    if (nm.labelEl) {
      tmp.set(n.x, n.y + nm.baseScale + 1.6, n.z).project(camera);
      if (tmp.z < 1) {
        const x = (tmp.x*0.5+0.5)*innerWidth, y = (-tmp.y*0.5+0.5)*innerHeight, depth = tmp.z;
        tmp.set(n.x, n.y - nm.baseScale - 1.6, n.z).project(camera);
        placed.push({ el: nm.labelEl, x, y, yBelow: (-tmp.y*0.5+0.5)*innerHeight, depth,
                      prop: n.type === 'proposal' ? 1 : 0, selected: n.id === selectedId ? 1 : 0 });
      } else nm.labelEl.classList.remove('show');
    }
  }
  layoutLabels(placed);

  // edges as pipes + billboard labels
  for (const e of edgeObjs) {
    edgeDir.set(e.b.x - e.a.x, e.b.y - e.a.y, e.b.z - e.a.z);
    const len = edgeDir.length() || 0.001;
    e.pipe.position.set((e.a.x+e.b.x)/2, (e.a.y+e.b.y)/2, (e.a.z+e.b.z)/2);
    e.pipe.quaternion.setFromUnitVectors(PIPE_UP, edgeDir.divideScalar(len));
    e.pipe.scale.set(1, len, 1);

    if (e.labelEl) {
      tmp.set((e.a.x+e.b.x)/2, (e.a.y+e.b.y)/2, (e.a.z+e.b.z)/2).project(camera);
      if (tmp.z < 1) {
        e.labelEl.style.left = `${(tmp.x*0.5+0.5)*innerWidth}px`;
        e.labelEl.style.top = `${(-tmp.y*0.5+0.5)*innerHeight}px`;
        e.labelEl.classList.add('show');
      } else e.labelEl.classList.remove('show');
    }
  }

  // time-axis phase markers
  for (const a of axisLabels) {
    tmp.copy(a.world).project(camera);
    if (tmp.z < 1) {
      a.el.style.left = `${(tmp.x*0.5+0.5)*innerWidth}px`;
      a.el.style.top = `${(-tmp.y*0.5+0.5)*innerHeight}px`;
      a.el.classList.add('show');
    } else a.el.classList.remove('show');
  }

  controls.update();
  renderer.render(scene, camera);
}

// ---------------------------------------------------------------- utils
function makeGlowTexture() {
  const c = document.createElement('canvas'); c.width = c.height = 128;
  const ctx = c.getContext('2d');
  const g = ctx.createRadialGradient(64,64,0,64,64,64);
  g.addColorStop(0, 'rgba(255,255,255,1)');
  g.addColorStop(0.25, 'rgba(255,255,255,0.55)');
  g.addColorStop(1, 'rgba(255,255,255,0)');
  ctx.fillStyle = g; ctx.fillRect(0,0,128,128);
  const t = new THREE.CanvasTexture(c); t.needsUpdate = true; return t;
}
function truncate(s, n) { return s.length > n ? s.slice(0, n-1) + '…' : s; }
function escapeHtml(s) {
  return (s || '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

addEventListener('resize', () => {
  camera.aspect = innerWidth / innerHeight; camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight);
});

// Travel along the time axis (and vertically) with the arrow keys: slide both
// the camera and its orbit target so the whole view glides down the timeline.
addEventListener('keydown', (e) => {
  const step = (e.shiftKey ? 24 : 9);
  let dx = 0, dy = 0;
  if (e.key === 'ArrowLeft')  dx = -step;
  else if (e.key === 'ArrowRight') dx = step;
  else if (e.key === 'ArrowUp')    dy = step;
  else if (e.key === 'ArrowDown')  dy = -step;
  else return;
  e.preventDefault();
  camera.position.x += dx; controls.target.x += dx;
  camera.position.y += dy; controls.target.y += dy;
});

// ---------------------------------------------------------------- panels (add / test)
// Add-argument form, persona manager and a live vote-weight table. This is the
// hands-on test surface: post arguments as different personas and watch how
// ARGUMENT PoI (not the author) sets each node's vote weight.
let argMode = 'branch';        // 'branch' (root) | 'reply' (attach to selected node)
let argRelation = 'support';
let newAuthorOpen = false;
let authorsCache = [];
let replyTargetId = null;      // node a new argument attaches to (an argument or a pool's member)

const PERSONA_COLORS = ['#b98cff', '#d8af6e', '#5aa9e6', '#57d98a', '#e2933f', '#e25b56', '#6f8bff'];
function randomColor() { return PERSONA_COLORS[Math.floor(Math.random() * PERSONA_COLORS.length)]; }

function setArgStatus(msg, cls) {
  const el = document.getElementById('arg-status');
  el.textContent = msg || '';
  el.className = 'status' + (cls ? ' ' + cls : '');
}

// Reflect the current reply target (an argument node, or a pool's representative
// member which may live outside the current view — look it up in liveNorm).
function updateArgTarget() {
  const el = document.getElementById('arg-target');
  if (!el) return;
  let label = null;
  if (replyTargetId != null) {
    if (nodeById.has(replyTargetId)) label = nodeLabelText(nodeById.get(replyTargetId));
    else if (liveNorm) {
      const r = liveNorm.nodes.find((n) => n.id === replyTargetId);
      if (r) label = (r.topic && r.topic.trim()) ? r.topic : truncate(r.label || '', 30);
    }
  }
  if (label) {
    el.textContent = 'Цель: ' + truncate(label, 30);
    el.style.color = '#8a8576';
  } else {
    el.textContent = 'Цель: выбери узел кликом по графу.';
    el.style.color = '#6b6658';
  }
}

async function loadAuthorsForForm() {
  // персон-дропдаунов больше нет — реакции и действия с пулами идут от твоего
  // аккаунта (сессия), без подмены. Оставлено пустым (вызовы не трогаем).
}

// PoI-weighted reactions on the open node.
async function loadReactions(nodeId) {
  const hist = document.getElementById('rx-hist');
  const summary = document.getElementById('rx-summary');
  if (!hist) return;
  const topic = rootOf(nodeId);
  if (topic == null) { hist.innerHTML = ''; summary.textContent = ''; return; }
  let d;
  try { d = await (await fetch(`/api/reactions/${nodeId}?topic=${topic}`)).json(); }
  catch (e) { return; }
  // Показываем СКОЛЬКО отреагировало и их СРЕДНИЙ PoI, а не сумму: сумма
  // поощряет накрутку числом. Вес голоса восстановлен (poi-weight-restored,
  // 2026-07-21), но формула ещё не зафиксирована — витрину не трогаем.
  // (a sum would read as a PoI-weighted vote). The histogram below shows spread.
  // счётчики — одна голова один сигнал; PoI-веса у реакции больше нет
  summary.innerHTML = `· 👍 ${d.agree.count} · 👎 ${d.disagree.count}`;
  hist.innerHTML = (d.agree.count || d.disagree.count)
    ? '' : '<div class="rx-empty">пока нет реакций</div>';
}

async function react(stance) {
  if (selectedId == null) return;
  // от твоего аккаунта (сессия) — без подмены персон
  const r = await fetch('/api/reactions', {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ node_id: selectedId, stance }),
  });
  if (r.status === 401) { setArgStatus('Войди, чтобы реагировать', 'err'); return; }
  await loadReactions(selectedId);
}

// ---------------------------------------------------------------- pools (Layer 2)
const STANCE_LABEL = { support: 'за', oppose: 'против', mixed: 'смешанная' };
const STANCE_COLOR = { support: '#57d98a', oppose: '#e25b56', mixed: '#5aa9e6' };

function bucketBarsHTML(support, color) {
  const max = Math.max(1, ...support.buckets.map((b) => b.count));
  let rows = '';
  for (const b of support.buckets) {
    if (!b.count) continue;
    rows += `<div class="rx-row"><span class="lab">${b.label}</span>
      <span class="bars"><span class="bar" style="width:${(b.count / max) * 70}px;background:${color}"></span></span>
      <span class="cnt">${b.count}</span></div>`;
  }
  if (support.no_data) rows += `<div class="rx-empty">без PoI здесь: ${support.no_data}</div>`;
  return rows || '<div class="rx-empty">пока никто не поддержал</div>';
}

const SRC = 'font-size:10px;color:#c4bfb2;padding-left:8px;border-left:1px solid rgba(255,255,255,.12);margin-top:4px';

// Позиция (пул) — СБОРКА ИИ, а не чей-то довод. Спорить/отвечать НА неё нельзя:
// это был бы спор с синтезом ИИ (против принципа брифа «ИИ указывает на реальные
// узлы, не на свой синтез»). HUD показывает сборку для чтения и список РЕАЛЬНЫХ
// аргументов — клик по любому уводит к настоящему узлу, где ответ прикрепится
// к нему, а не к сборке.
function renderPoolHud(n) {
  document.getElementById('hud-type').textContent = 'сборка ИИ · ' + (STANCE_LABEL[n.stance] || n.stance);
  document.getElementById('hud-label').textContent = truncate(n.label, 60);
  const ids = n.member_ids || [], texts = n.member_texts || [];
  const args = ids.map((mid, i) =>
    `<div class="pool-arg" data-mid="${mid}" style="${SRC};cursor:pointer">${escapeHtml(texts[i] || '')}</div>`
  ).join('');
  document.getElementById('hud-content').innerHTML =
    `<div style="margin-bottom:10px;color:#ece6d8;white-space:pre-wrap;line-height:1.55">${escapeHtml(n.composed || n.label)}</div>`
    + `<div style="color:var(--bronze);font-size:11px;margin-bottom:12px">👥 поддержали: ${n.support.count}</div>`
    + `<div style="border-top:1px solid rgba(216,175,110,0.18);padding-top:10px;font-size:11.5px;color:var(--dim);line-height:1.5;margin-bottom:10px">Это сборка ИИ, не отдельный довод. Чтобы возразить или ответить — открой конкретный довод ниже: ответ прикрепится к нему.</div>`
    + `<div style="color:#8a8576;font-size:9px;letter-spacing:.1em;text-transform:uppercase;margin-bottom:6px">Реальные доводы (${ids.length}) — открыть</div>`
    + args;

  // клик по реальному аргументу → выйти из пулов и открыть этот узел в графе,
  // где есть «Ответить на этот узел» (ответ цепляется к реальному доводу)
  document.querySelectorAll('.pool-arg').forEach((el) => {
    el.onclick = async () => {
      const mid = parseInt(el.dataset.mid);
      await togglePools(false);
      const node = nodeById.get(mid);
      if (node) openHud(node);
    };
  });
}

// "Сделать вывод": synthesize the star + its orbit into the next node forward.
async function concludePosition(n) {
  setArgStatus('');
  hud.classList.remove('open');
  const bar = document.getElementById('pools-topic');
  if (bar) bar.textContent = 'ИИ делает вывод…';
  try {
    await fetch(`/api/positions/${n.posId}/conclude`, { method: 'POST' });
  } catch (e) { /* ignore */ }
  await buildPoolGraph(false);
}

// A planet (question/detail) is a dialogue move branching off a node. It can
// branch further — sub-question or sub-detail.
function renderPlanetHud(n) {
  document.getElementById('hud-type').textContent =
    (n.planetKind === 'question' ? 'вопрос' : 'уточнение') + ' · ветка диалога';
  document.getElementById('hud-label').textContent = truncate(n.label, 60);
  document.getElementById('hud-content').innerHTML =
    `<div style="color:#ece6d8;white-space:pre-wrap;line-height:1.55;margin-bottom:8px">${escapeHtml(n.label)}</div>`
    + `<div style="color:var(--bronze);font-size:11px;margin-bottom:12px">PoI: ${n.poiRaw == null ? '—' : (+n.poiRaw).toFixed(0)}</div>`
    + `<div style="font-size:9px;letter-spacing:.1em;text-transform:uppercase;color:var(--bronze);margin-bottom:6px">Продолжить ветку</div>`
    + `<div style="display:flex;gap:5px">`
    + `<button id="pl-detail" class="rx-btn" style="flex:1;color:#5aa9e6;border:1px solid rgba(90,169,230,.4)">➕ Уточнить</button>`
    + `<button id="pl-question" class="rx-btn" style="flex:1;color:#b98cff;border:1px solid rgba(185,140,255,.4)">❓ Спросить</button>`
    + `</div>`;
  document.getElementById('pl-detail').onclick = () => branchAction(n, 'detail');
  document.getElementById('pl-question').onclick = () => branchAction(n, 'question');
}

// Support a position: a PoI-weighted vote ON the position (stable id).
async function poolVote(n) {
  // от твоего аккаунта (сессия), без подмены персон
  const r = await fetch(`/api/positions/${n.posId}/vote`, {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ stance: 'agree' }),
  });
  if (r.status === 401) { setArgStatus('Войди, чтобы поддержать', 'err'); return; }
  await buildPoolGraph(false);
  hud.classList.remove('open');
}

// Continue / oppose / question a position: open the add form anchored to THIS
// position (stable id). On submit it posts to the position endpoint.
// Pending anchored action: either on a position (continue/oppose/question) or a
// branch off a planet node. On submit the text is posted to the right endpoint.
let pendingAction = null;
const ACTION_LABEL = { continue: 'Развить', oppose: 'Оспорить', question: 'Вопрос к' };

// The form adapts to the action — a question doesn't read as "добавить аргумент".
// Every contribution is still PoI-scored, so the PoI hint stays.
const FORM_CHROME = {
  question: { h2: 'Вопрос к позиции', ph: 'Задай вопрос…', btn: '❓ Оценить и спросить' },
  continue: { h2: 'Развить позицию', ph: 'Добавь довод или деталь…', btn: '➕ Оценить и добавить' },
  oppose: { h2: 'Оспорить позицию', ph: 'Сформулируй возражение…', btn: '👎 Оценить и оспорить' },
  'branch-question': { h2: 'Под-вопрос', ph: 'Задай уточняющий вопрос…', btn: '❓ Оценить и спросить' },
  'branch-detail': { h2: 'Уточнение к ветке', ph: 'Добавь деталь…', btn: '➕ Оценить и добавить' },
};
const DEFAULT_CHROME = { h2: 'Добавить довод', ph: 'Сформулируй тезис…', btn: 'Оценить и добавить' };

function setFormChrome(c) {
  document.getElementById('arg-h2').textContent = c.h2;
  document.getElementById('arg-text').placeholder = c.ph;
  document.getElementById('arg-submit').textContent = c.btn;
}

// Писать отсюда больше нельзя — и это не упрощение интерфейса, а правило.
// Опубликованный текст не правится (vault: decisions/edit-delete-window),
// поэтому черновик проходит через разбор и разговор с ИИ-компаньоном, а тот
// живёт только в дереве. Пока форма письма стояла и здесь, любой мог
// опубликовать мимо компаньона — не нарушив ни одной серверной проверки.
// Граф остаётся тем, для чего он хорош: посмотреть форму спора.
function goWriteInTree(note) {
  hud.classList.remove('open');
  const root = selectedId != null ? rootOf(selectedId) : currentTopicId;
  setArgStatus(note || '', 'busy');
  location.href = root ? ('/?topic=' + root) : '/';
}

function _openActionForm(note, chrome) {
  goWriteInTree(note);
}

function poolAction(n, action) {
  pendingAction = { kind: 'position', posId: n.posId, action };
  _openActionForm(`${ACTION_LABEL[action]} позицию: «${truncate(n.label, 30)}»`, FORM_CHROME[action]);
}

// Branch a planet further (sub-detail or sub-question).
function branchAction(n, planetKind) {
  pendingAction = { kind: 'branch', nodeId: n.rawId, planetKind };
  const w = planetKind === 'question' ? 'Под-вопрос к' : 'Уточнение к';
  _openActionForm(`${w}: «${truncate(n.label, 30)}»`, FORM_CHROME['branch-' + planetKind]);
}

function clearPendingAction() {
  pendingAction = null;
  setFormChrome(DEFAULT_CHROME);
  document.getElementById('arg-reply-opts').style.display = 'none';
  setArgStatus('');
}

// Build the user-facing graph FROM persistent positions: root = trunk, each
// position = a node (stable id), linked to root by stance + position↔position
// links (oppose). Positions ARE the nodes the user works with.
async function buildPoolGraph(refresh) {
  if (currentTopicId == null) return;
  const bar = document.getElementById('pools-topic');
  const topic = (discussions.find((d) => d.rootId === currentTopicId) || {}).title || '';
  if (bar) bar.textContent = topic + (refresh ? ' · пересборка…' : ' · загрузка…');
  let data;
  try {
    // ?recompute убран с GET: сборка позиций — заказ вошедшего, со своего
    // гранта (POST …/recompute). Чтение модель больше не зовёт.
    if (refresh) {
      await fetch(`/api/positions/${currentTopicId}/recompute`, { method: 'POST' });
    }
    data = await (await fetch(`/api/positions/${currentTopicId}`)).json();
  } catch (e) { if (bar) bar.textContent = topic + ' · ошибка'; return; }
  if (data.detail) { if (bar) bar.textContent = topic + ' · ' + data.detail; return; }
  const positions = data.positions || [];
  if (bar) bar.textContent = topic + ' · позиций: ' + positions.length;

  const rootRaw = ((liveNorm && liveNorm.nodes) || []).find((n) => n.id === currentTopicId);
  const rootLabel = rootRaw ? (rootRaw.topic || rootRaw.label) : 'проблема';
  const nodes = [{
    id: currentTopicId, label: rootLabel, type: 'proposal', topic: truncate(rootLabel, 30),
    turn: 0, source: 'context', poi_score: 0, confidence: 1, authorColor: '#7fe3d4', breakdown: null,
  }];
  const REL = { support: 'support', oppose: 'refute', mixed: 'qualify' };
  const maxSup = Math.max(1, ...positions.map((p) => p.support.count));
  const edges = [];
  const pidToNode = {};
  positions.forEach((p, i) => {
    const id = 'pos-' + p.id; pidToNode[p.id] = id;
    nodes.push({
      id, posId: p.id, label: p.headline, type: 'pool', topic: truncate(p.headline, 42),
      turn: 1, source: 'pool', poi_score: 0, confidence: p.support.count / maxSup,
      authorColor: STANCE_COLOR[p.stance] || '#888', breakdown: null,
      isPool: true, stance: p.stance, composed: p.composed,
      support: p.support, member_texts: p.member_texts, member_ids: p.member_ids,
    });
    edges.push({ id: 'e' + i, source: id, target: currentTopicId, relation: REL_FROM_TYPE[REL[p.stance]] || 'SUPPORTS' });
    // planets orbit the star; sub-planets orbit their parent planet (recursive).
    (p.planets || []).forEach((pl) => {
      const plid = 'pl-' + pl.id;
      const parentId = pl.parent != null ? 'pl-' + pl.parent : id;
      nodes.push({
        id: plid, rawId: pl.id, label: pl.text, type: pl.kind, topic: truncate(pl.text, 30),
        turn: 2, source: 'planet', poi_score: 0, confidence: 0.2,
        authorColor: pl.kind === 'question' ? '#b98cff' : '#5aa9e6', breakdown: null,
        isPlanet: true, planetKind: pl.kind, posId: p.id, parentId, poiRaw: pl.poi,
      });
      edges.push({ id: 'p' + pl.id, source: plid, target: parentId, relation: pl.kind === 'question' ? 'QUESTION' : 'QUALIFIES' });
    });
  });
  (data.links || []).forEach((l, i) => {
    if (pidToNode[l.from_position] && pidToNode[l.to_position]) {
      edges.push({
        id: 'l' + i, source: pidToNode[l.from_position], target: pidToNode[l.to_position],
        relation: l.type === 'conclusion' ? 'CONCLUDES' : 'REBUTS',
      });
    }
  });
  // Та же раскладка, что и у обсуждения (layoutTree): слева-направо, кадрируется
  // при камере по умолчанию. radialLayout (орбиты) выносил корень и позиции за
  // край экрана — визуально ломался при входе в пулы.
  layoutTree(nodes, edges, currentTopicId);
  installGraph(nodes, edges, true);
}

async function togglePools(on) {
  poolsOn = on;
  document.getElementById('tab-pools').classList.toggle('active', poolsOn);
  document.getElementById('poolspanel').classList.toggle('open', poolsOn);
  hud.classList.remove('open');
  if (poolsOn) {
    // пулы считаются по одному обсуждению — из «всех ветвей» уходим в текущее
    if (viewAll) { viewAll = false; populateTopicMenu(); }
    await buildPoolGraph(false);
  } else {
    await renderCurrent();
  }
}

// Each persona's PoI IN THE CURRENT TOPIC — drives both the lens and the test
// sliders. PoI is domain-specific, so this is fetched per discussion.
// Линза PoI удалена полностью (решение 2026-07-22): система не считает и не
// показывает постоянное «стояние» участника. Узлы окрашиваются по автору (nodeColor).

async function refreshWeights() {
  const el = document.getElementById('weights-list');
  if (!el) return;   // таблицы весов нет в разметке (дев-стенд убран) — тихо выходим
  let rows;
  try { rows = await (await fetch('/api/weights')).json(); }
  catch (e) { return; }
  rows.sort((a, b) => (b.weight || 0) - (a.weight || 0));
  el.innerHTML = rows.slice(0, 30).map((r) => `
    <div class="wrow">
      <span class="t">${truncate(r.text || '', 26)}</span>
      <span class="a">${r.author || '—'}</span>
      <span class="w">${(r.weight || 0).toFixed(2)}</span>
    </div>`).join('');
}

async function submitArgument() {
  const text = document.getElementById('arg-text').value.trim();
  if (!text) { setArgStatus('Введите текст довода', 'err'); return; }

  // автора ставит сервер по сессии
  const authorId = null;

  const btn = document.getElementById('arg-submit');

  // Anchored action: a position move (continue/oppose/question) or a planet
  // branch — posts to the right endpoint, not /api/argument.
  if (pendingAction && pendingAction.kind === 'reply') {
    btn.disabled = true;
    setArgStatus('ИИ оценивает довод…', 'busy');
    try {
      const res = await fetch('/api/argument', {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ text, connect_to: pendingAction.nodeId,
                               edge_type: argRelation }),
      });
      if (!res.ok) {
        const e = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(typeof e.detail === 'string' ? e.detail : ('HTTP ' + res.status));
      }
      document.getElementById('arg-text').value = '';
      clearPendingAction();
      setArgStatus('Добавлено', 'ok');
      scheduleLiveRefresh();
    } catch (e) {
      setArgStatus('Ошибка: ' + e.message, 'err');
    } finally {
      btn.disabled = false;
    }
    return;
  }

  if (pendingAction) {
    let url, payload = { text, author_id: authorId };
    if (pendingAction.kind === 'branch') {
      url = `/api/nodes/${pendingAction.nodeId}/branch`;
      payload.kind = pendingAction.planetKind;
    } else {
      url = `/api/positions/${pendingAction.posId}/${pendingAction.action}`;
    }
    btn.disabled = true;
    setArgStatus('Добавляю…', 'busy');
    try {
      const res = await fetch(url, {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        const e = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(typeof e.detail === 'string' ? e.detail : ('HTTP ' + res.status));
      }
      document.getElementById('arg-text').value = '';
      clearPendingAction();
      setArgStatus('Готово', 'ok');
      await loadAuthorsForForm();
      await buildPoolGraph(false);   // positions/orbit changed
    } catch (e) {
      setArgStatus('Ошибка: ' + e.message, 'err');
    } finally {
      btn.disabled = false;
    }
    return;
  }

  // Только корень темы: ответ на конкретный узел делается из HUD, где видно,
  // к чему он прикрепляется. Автор берётся из сессии на сервере.
  const body = { text, title: text.slice(0, 80) };

  btn.disabled = true;
  setArgStatus('LLM оценивает довод…', 'busy');
  try {
    const res = await fetch('/api/argument', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const e = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(typeof e.detail === 'string' ? e.detail : ('HTTP ' + res.status));
    }
    const node = await res.json();
    setArgStatus(`Добавлено · PoI ${(node.poi_score ?? 0).toFixed(0)} · вес ${(node.weight ?? 0).toFixed(2)}`, 'ok');
    document.getElementById('arg-text').value = '';
    await loadAuthorsForForm();   // a new persona may have appeared
    scheduleLiveRefresh();        // re-derive discussions; node arrives via SSE too
    refreshWeights();
  } catch (e) {
    setArgStatus('Ошибка: ' + e.message, 'err');
  } finally {
    btn.disabled = false;
  }
}

function initPanels() {
  const addPanel = document.getElementById('addpanel');

  // «Новая тема» убрана из графа: граф показывает одну выбранную тему, темы
  // создаются в дереве. Панель открывается только для ответа/действия из HUD;
  // закрытие — крестиком.
  document.getElementById('add-close').onclick = () => {
    clearPendingAction();
    addPanel.classList.remove('open');
  };

  document.getElementById('arg-submit').onclick = submitArgument;
  document.getElementById('rx-agree').onclick = () => react('agree');
  document.getElementById('rx-disagree').onclick = () => react('disagree');


  // topic (discussion) switcher — stub for the future topics menu
  document.getElementById('topicsel').onchange = async (e) => {
    viewAll = e.target.value === 'all';
    if (!viewAll) currentTopicId = parseInt(e.target.value);
    if (viewAll && poolsOn) await togglePools(false);
    else await renderCurrent();
  };

  // Pools (Layer 2): AI-composed reading view, rendered as graph nodes
  document.getElementById('tab-pools').onclick = () => togglePools(!poolsOn);
  document.getElementById('pools-close').onclick = () => togglePools(false);
  document.getElementById('pools-refresh').onclick = () => buildPoolGraph(true);

  // "Reply to this node" in the HUD: open the add form, pre-targeted at it.
  document.querySelectorAll('#arg-rel button').forEach((b) => b.onclick = () => {
    document.querySelectorAll('#arg-rel button').forEach((x) => x.classList.remove('active'));
    b.classList.add('active');
    argRelation = b.dataset.rel;
  });

  document.getElementById('hud-reply').onclick = () => {
    if (selectedId == null) return;
    // Ответ пишется в дереве: там виден родитель, там разбор черновика и
    // разговор с компаньоном до отправки.
    goWriteInTree('');
  };

  loadAuthorsForForm();
}

boot();
initPanels();
