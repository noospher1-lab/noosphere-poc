import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const REL_COLOR = {
  SUPPORTS:  0x57d98a,
  REBUTS:    0xe25b56,
  UNDERCUTS: 0xe2933f,
  QUALIFIES: 0x5aa9e6,
  QUESTION:  0xb98cff,
  CONCLUDES: 0xd8af6e,
};
const TYPE_COLOR = {
  proposal: 0x7fe3d4, // teal — central
  argument: 0x6f8bff, // indigo
  evidence: 0xd8af6e, // bronze
};

// Map the Python backend's lowercase edge types onto the viz' relations.
const REL_FROM_TYPE = { support: 'SUPPORTS', refute: 'REBUTS', qualify: 'QUALIFIES', question: 'QUESTION' };
const CRITERIA = ['clarity', 'depth', 'counterargument', 'evidence', 'awareness_of_limits'];

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

const camera = new THREE.PerspectiveCamera(55, innerWidth / innerHeight, 0.1, 2000);
camera.position.set(0, 18, 122); // pulled back so the full time axis fits in frame

const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.setSize(innerWidth, innerHeight);
app.appendChild(renderer.domElement);

const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.dampingFactor = 0.08;
controls.rotateSpeed = 0.6;
controls.minDistance = 30;
controls.maxDistance = 220;
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
      topic: n.topic || (bd && bd.topic) || '',          // short theme, shown on the node
      turn: n.turn ?? (bd && bd.turn) ?? 0,              // dialogue order -> X position
      source: n.source || (bd && bd.source) || 'context', // alex / gemini / claude / live
      poi_score: score01,                       // 0..1 for the HUD bar
      confidence: maxIn > 0 ? inD / maxIn : 0,   // centrality proxy (in-degree)
      author: n.author || null,
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

  return { nodes, edges };
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
  if (poolsOn) await buildPoolGraph(false);
  else await renderDiscussion();
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
    const title = (rootNode && (rootNode.topic || truncate(rootNode.label, 26))) || ('Тема ' + rootId);
    comps.push({ rootId, title, ids });
  }
  return comps;
}

function populateTopicMenu() {
  const sel = document.getElementById('topicsel');
  if (!sel) return;
  sel.innerHTML = discussions.map((d) =>
    `<option value="${d.rootId}">${d.title} · ${d.ids.size}</option>`).join('');
  if (currentTopicId != null) sel.value = String(currentTopicId);
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
  await loadTopicPoi();
  applyLens();
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
  document.body.appendChild(labelEl);

  nodeMeshes.set(n.id, { mesh, glow, baseScale: base, pulse: 0, labelEl, node: n });
}

// The graphic label is the short thesis (LLM topic, else a trimmed claim).
function nodeLabelText(n) {
  if (n.topic && n.topic.trim()) return n.topic.trim();
  return truncate(n.label || '', 28);
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
  const y = -28, x0 = timeX(0), x1 = timeX(TURN_MAX);
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
  document.getElementById('hud-type').textContent = n.source || n.type;
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
         <span style="color:#8a8576">${k}</span>
         <span style="color:#ece6d8;font-variant-numeric:tabular-nums">${b[k]}</span>
       </div>`).join('');
    c.innerHTML = text + comment + rows;
  } else {
    // dialogue variants: every word, readable and scrollable
    c.textContent = n.label || '—';
  }

  // null-safe: у проблемы/неоценённого узла confidence может отсутствовать
  const conf = (n.confidence == null || isNaN(n.confidence)) ? 0 : n.confidence;
  document.getElementById('hud-conf').textContent = conf.toFixed(2);
  document.getElementById('hud-conf-fill').style.width = `${conf*100}%`;

  document.getElementById('hud-author').textContent = n.author || '—';
  const ap = n.author != null ? topicPoi[n.author] : null;
  document.getElementById('hud-rep').textContent = ap == null ? '—' : ap.toFixed(0);
  document.getElementById('hud-weight').textContent =
    (n.weight != null ? n.weight : computeWeight(n.poi_score)).toFixed(2);

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
    // node label (short thesis), billboarded just above the sphere
    if (nm.labelEl) {
      tmp.set(n.x, n.y + nm.baseScale + 1.6, n.z).project(camera);
      if (tmp.z < 1) {
        nm.labelEl.style.left = `${(tmp.x*0.5+0.5)*innerWidth}px`;
        nm.labelEl.style.top = `${(-tmp.y*0.5+0.5)*innerHeight}px`;
        nm.labelEl.classList.add('show');
      } else nm.labelEl.classList.remove('show');
    }
  }

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
  try { authorsCache = await (await fetch('/api/authors')).json(); }
  catch (e) { console.error('authors load failed', e); return; }
  // who is reacting (in the HUD reaction row)
  const rx = document.getElementById('rx-author');
  if (rx) {
    const rp = rx.value;
    rx.innerHTML = authorsCache.map((a) => `<option value="${a.id}">${a.name}</option>`).join('');
    if (rp) rx.value = rp;
  }
}

// PoI-weighted reactions on the open node.
async function loadReactions(nodeId) {
  const hist = document.getElementById('rx-hist');
  const summary = document.getElementById('rx-summary');
  if (!hist) return;
  if (currentTopicId == null) { hist.innerHTML = ''; summary.textContent = ''; return; }
  let d;
  try { d = await (await fetch(`/api/reactions/${nodeId}?topic=${currentTopicId}`)).json(); }
  catch (e) { return; }
  // Показываем СКОЛЬКО отреагировало и их СРЕДНИЙ PoI, а не сумму: сумма
  // поощряет накрутку числом. Вес голоса восстановлен (poi-weight-restored,
  // 2026-07-21), но формула ещё не зафиксирована — витрину не трогаем.
  // (a sum would read as a PoI-weighted vote). The histogram below shows spread.
  const avgTag = (s) => s.avg != null ? ` (ср. PoI ${Math.round(s.avg)})` : '';
  summary.innerHTML = `· 👍 ${d.agree.count}${avgTag(d.agree)} · 👎 ${d.disagree.count}${avgTag(d.disagree)}`;

  const maxCount = Math.max(1, ...d.agree.buckets.map((b) => b.count), ...d.disagree.buckets.map((b) => b.count));
  const UNIT = 70;
  let rows = '';
  for (let i = 0; i < 10; i++) {
    const a = d.agree.buckets[i].count, dd = d.disagree.buckets[i].count;
    if (!a && !dd) continue;
    rows += `<div class="rx-row">
      <span class="lab">${d.agree.buckets[i].label}</span>
      <span class="bars">
        ${a ? `<span class="bar a" style="width:${(a / maxCount) * UNIT}px"></span>` : ''}
        ${dd ? `<span class="bar d" style="width:${(dd / maxCount) * UNIT}px"></span>` : ''}
      </span>
      <span class="cnt">${a ? '+' + a : ''}${dd ? ' −' + dd : ''}</span>
    </div>`;
  }
  const nd = d.agree.no_data + d.disagree.no_data;
  if (!rows) rows = '<div class="rx-empty">пока нет реакций</div>';
  else if (nd) rows += `<div class="rx-empty">без PoI в теме: ${nd}</div>`;
  hist.innerHTML = rows;
}

async function react(stance) {
  if (selectedId == null) return;
  const aid = document.getElementById('rx-author').value;
  if (!aid) return;
  await fetch('/api/reactions', {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ node_id: selectedId, author_id: parseInt(aid), stance }),
  });
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
  if (support.no_data) rows += `<div class="rx-empty">без PoI в теме: ${support.no_data}</div>`;
  return rows || '<div class="rx-empty">пока никто не поддержал</div>';
}

const SRC = 'font-size:10px;color:#c4bfb2;padding-left:8px;border-left:1px solid rgba(255,255,255,.12);margin-top:4px';

function renderPoolHud(n) {
  document.getElementById('hud-type').textContent = 'позиция · ' + (STANCE_LABEL[n.stance] || n.stance);
  document.getElementById('hud-label').textContent = truncate(n.label, 60);
  const s = n.support;
  const args = n.member_texts.map((t) => `<div style="${SRC}">${escapeHtml(t)}</div>`).join('');
  const personas = authorsCache.map((a) => `<option value="${a.id}">${a.name}</option>`).join('');
  document.getElementById('hud-content').innerHTML =
    `<div style="margin-bottom:10px;color:#ece6d8;white-space:pre-wrap;line-height:1.55">${escapeHtml(n.composed || n.label)}</div>`
    + `<div style="color:var(--bronze);font-size:11px;margin-bottom:12px">👥 поддержали: ${s.count}</div>`
    + `<div style="border-top:1px solid rgba(216,175,110,0.18);padding-top:10px;margin-bottom:8px">`
    + `<div style="font-size:9px;letter-spacing:.1em;text-transform:uppercase;color:var(--bronze);margin-bottom:6px">Действие с позицией</div>`
    + `<select id="pool-author" style="width:100%;font-family:var(--mono);font-size:10px;color:var(--ivory);background:rgba(5,6,10,0.6);border:1px solid rgba(216,175,110,0.22);border-radius:3px;padding:5px;margin-bottom:6px">${personas}</select>`
    + `<div style="display:flex;flex-wrap:wrap;gap:5px">`
    + `<button id="pool-support" class="rx-btn agree" style="flex:1 1 45%">👍 Поддержать</button>`
    + `<button id="pool-contest" class="rx-btn disagree" style="flex:1 1 45%">👎 Оспорить</button>`
    + `<button id="pool-continue" class="rx-btn" style="flex:1 1 45%;color:var(--bronze);border:1px solid rgba(216,175,110,0.4)">➕ Развить</button>`
    + `<button id="pool-question" class="rx-btn" style="flex:1 1 45%;color:#b98cff;border:1px solid rgba(185,140,255,0.4)">❓ Спросить</button>`
    + `</div>`
    + `<button id="pool-conclude" class="rx-btn" style="width:100%;margin-top:6px;color:#05060a;background:var(--bronze);border:none">🔭 Сделать вывод из орбиты</button>`
    + `</div>`
    + `<div style="color:#8a8576;font-size:9px;letter-spacing:.1em;text-transform:uppercase">Исходные аргументы (${n.member_ids.length})</div>`
    + args;

  document.getElementById('pool-support').onclick = () => poolVote(n);
  document.getElementById('pool-contest').onclick = () => poolAction(n, 'oppose');
  document.getElementById('pool-continue').onclick = () => poolAction(n, 'continue');
  document.getElementById('pool-question').onclick = () => poolAction(n, 'question');
  document.getElementById('pool-conclude').onclick = () => concludePosition(n);
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
  const aid = document.getElementById('pool-author').value;
  if (!aid) return;
  await fetch(`/api/positions/${n.posId}/vote`, {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ author_id: parseInt(aid), stance: 'agree' }),
  });
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
const DEFAULT_CHROME = { h2: 'Добавить аргумент', ph: 'Сформулируй тезис…', btn: 'Оценить и добавить' };

function setFormChrome(c) {
  document.getElementById('arg-h2').textContent = c.h2;
  document.getElementById('arg-text').placeholder = c.ph;
  document.getElementById('arg-submit').textContent = c.btn;
}

function _openActionForm(note, chrome) {
  setFormChrome(chrome || DEFAULT_CHROME);
  document.getElementById('addpanel').classList.add('open');
  document.getElementById('tab-add').classList.add('active');
  setArgStatus(note, 'busy');
  hud.classList.remove('open');
  document.getElementById('arg-text').focus();
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
  document.getElementById('arg-h2').textContent = 'Новая тема';
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
    data = await (await fetch(`/api/positions/${currentTopicId}${refresh ? '?recompute=true' : ''}`)).json();
  } catch (e) { if (bar) bar.textContent = topic + ' · ошибка'; return; }
  if (data.detail) { if (bar) bar.textContent = topic + ' · ' + data.detail; return; }
  const positions = data.positions || [];
  if (bar) bar.textContent = topic + ' · позиций: ' + positions.length;

  const rootRaw = ((liveNorm && liveNorm.nodes) || []).find((n) => n.id === currentTopicId);
  const rootLabel = rootRaw ? (rootRaw.topic || rootRaw.label) : 'тема';
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
  radialLayout(nodes, edges, currentTopicId);
  installGraph(nodes, edges, true);
}

async function togglePools(on) {
  poolsOn = on;
  document.getElementById('tab-pools').classList.toggle('active', poolsOn);
  document.getElementById('poolspanel').classList.toggle('open', poolsOn);
  hud.classList.remove('open');
  if (poolsOn) {
    lensOn = false;
    document.getElementById('tab-lens').classList.remove('active');
    document.getElementById('lens-legend').classList.remove('show');
    await buildPoolGraph(false);
  } else {
    await renderDiscussion();
  }
}

// Each persona's PoI IN THE CURRENT TOPIC — drives both the lens and the test
// sliders. PoI is domain-specific, so this is fetched per discussion.
async function loadTopicPoi() {
  // Линза PoI отключена (решение 2026-07-22): система не считает и не показывает
  // постоянное «стояние» участника. Эндпоинт /api/topic_poi удалён — не дёргаем
  // его (иначе 404 → объект вместо массива → падение всей отрисовки графа).
  topicPoi = {}; topicPoiRows = [];
  renderTopicPanel();
}

function renderTopicPanel() {
  const list = document.getElementById('authors-list');
  if (!list) return;
  if (currentTopicId == null) { list.innerHTML = '<div class="hint">нет активной темы</div>'; return; }
  list.innerHTML = topicPoiRows.map((a) => `
    <div class="author-row" data-id="${a.author_id}">
      <div class="top">
        <span class="nm"><span class="dot" style="background:${a.color || '#8899aa'}"></span>${a.name}</span>
        <span class="rep">${a.poi == null ? '—' : a.poi.toFixed(0)}</span>
      </div>
      <input type="range" min="0" max="100" value="${a.poi == null ? 50 : a.poi}" />
    </div>`).join('');

  list.querySelectorAll('.author-row').forEach((row) => {
    const id = parseInt(row.dataset.id);
    const range = row.querySelector('input[type=range]');
    const rep = row.querySelector('.rep');
    range.addEventListener('input', () => { rep.textContent = range.value; });
    range.addEventListener('change', async () => {
      await fetch(`/api/topic_poi/${currentTopicId}/${id}`, {
        method: 'PATCH', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ poi: parseFloat(range.value) }),
      });
      await loadTopicPoi();   // refresh map + sliders
      applyLens();            // recolor nodes if lens is on
    });
  });
}

// PoI lens: colour each node by its AUTHOR'S PoI in the current topic — so you
// can see at a glance who argued from understanding and who didn't. This never
// touches argument weight; it's purely a viewing lens.
function poiTier(poi) {
  if (poi == null) return 0x555a66;   // no data — grey
  if (poi >= 70) return 0x57d98a;     // high — green
  if (poi >= 40) return 0xe2933f;     // mid — orange
  return 0xe25b56;                    // low — red
}
function applyLens() {
  for (const nm of nodeMeshes.values()) {
    const n = nm.node;
    const col = lensOn
      ? new THREE.Color(poiTier(n.author != null ? topicPoi[n.author] : null))
      : nodeColor(n);
    nm.mesh.material.color.copy(col);
    nm.mesh.material.emissive.copy(col).multiplyScalar(0.35);
    nm.glow.material.color.copy(col);
  }
}

async function refreshWeights() {
  let rows;
  try { rows = await (await fetch('/api/weights')).json(); }
  catch (e) { return; }
  rows.sort((a, b) => (b.weight || 0) - (a.weight || 0));
  document.getElementById('weights-list').innerHTML = rows.slice(0, 30).map((r) => `
    <div class="wrow">
      <span class="t">${truncate(r.text || '', 26)}</span>
      <span class="a">${r.author || '—'}</span>
      <span class="w">${(r.weight || 0).toFixed(2)}</span>
    </div>`).join('');
}

async function submitArgument() {
  const text = document.getElementById('arg-text').value.trim();
  if (!text) { setArgStatus('Введите текст аргумента', 'err'); return; }

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
  setArgStatus('LLM оценивает аргумент…', 'busy');
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
  const tabAdd = document.getElementById('tab-add');

  // Панель одна: «новая тема». Тест-панель (персоны, веса, reseed) и выбор
  // «куда добавить» убраны из разметки — это был стенд для соло-разработки,
  // а не то, что должен видеть участник.
  tabAdd.onclick = () => {
    if (pendingAction) clearPendingAction();
    const open = addPanel.classList.toggle('open');
    tabAdd.classList.toggle('active', open);
  };

  document.getElementById('arg-submit').onclick = submitArgument;
  document.getElementById('rx-agree').onclick = () => react('agree');
  document.getElementById('rx-disagree').onclick = () => react('disagree');

  // PoI lens toggle
  const tabLens = document.getElementById('tab-lens');
  tabLens.onclick = () => {
    lensOn = !lensOn;
    tabLens.classList.toggle('active', lensOn);
    document.getElementById('lens-legend').classList.toggle('show', lensOn);
    applyLens();
  };

  // topic (discussion) switcher — stub for the future topics menu
  document.getElementById('topicsel').onchange = async (e) => {
    currentTopicId = parseInt(e.target.value);
    if (poolsOn) await buildPoolGraph(false);
    else await renderDiscussion();
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
    const target = nodeById.get(selectedId);
    pendingAction = { kind: 'reply', nodeId: selectedId };
    document.getElementById('addpanel').classList.add('open');
    document.getElementById('tab-add').classList.add('active');
    document.getElementById('arg-h2').textContent = 'Ответ на узел';
    document.getElementById('arg-reply-opts').style.display = 'block';
    setArgStatus('Отвечаешь на: «' + truncate(nodeLabelText(target) || '', 34) + '»', 'busy');
    hud.classList.remove('open');
    document.getElementById('arg-text').focus();
  };

  loadAuthorsForForm();
}

boot();
initPanels();
