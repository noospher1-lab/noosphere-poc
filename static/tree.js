// Noosphere — tree UI, lazy edition.
//
// Read contract (scaling draft, principle 3): the client NEVER loads the whole
// graph. It reads /api/topics (roots) and, per expanded node, a ranked page of
// children from /api/nodes/{id}/children. Expanding a folder = fetching a page.
// /api/graph is only used by the force-directed mode (/graph.html).

const PAGE = 20; // children per page

const $ = (s, r = document) => r.querySelector(s);
const el = (tag, cls, txt) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (txt != null) n.textContent = txt;
  return n;
};
async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error((await r.text()) || r.status);
  return r.status === 204 ? null : r.json();
}
function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.remove("show"), 1800);
}

// ---- state
let TOPICS = [];
let ME = null;              // the logged-in author (from the session cookie)
const KIDS = new Map();     // nodeId -> {total, children:[...]}  (fetched pages)
const ROOT = new Map();     // nodeId -> topic root id (learned while walking down)
const expanded = new Set();
let selectedId = null;

// ---- auth: the author of every write is the session, not a dropdown
function renderAuthUI() {
  const u = $("#user");
  if (ME) {
    u.textContent = ME.name + " (@" + ME.username + ")";
    if (ME.color) u.style.color = ME.color;
    $("#loginBtn").style.display = "none";
    $("#logoutBtn").style.display = "";
  } else {
    u.textContent = "наблюдатель";
    u.style.color = "";
    $("#loginBtn").style.display = "";
    $("#logoutBtn").style.display = "none";
  }
}

async function loadMe() {
  try { ME = await api("/api/auth/me"); } catch (_) { ME = null; }
  renderAuthUI();
}

function openAuth(msg) {
  $("#authErr").textContent = msg || "";
  $("#authModal").style.display = "flex";
  $("#authUser").focus();
}
function closeAuth() { $("#authModal").style.display = "none"; }

// every write goes through this: logged in → proceed, otherwise → modal
function requireAuth() {
  if (ME) return true;
  openAuth("для этого действия нужен аккаунт");
  return false;
}

async function doAuth(path) {
  const body = {
    username: $("#authUser").value.trim(),
    password: $("#authPass").value,
  };
  if (path.endsWith("register")) body.name = $("#authName").value.trim() || undefined;
  try {
    ME = await api(path, {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    renderAuthUI();
    closeAuth();
    toast("привет, " + ME.name);
  } catch (e) {
    let msg = e.message;
    try { msg = JSON.parse(msg).detail || msg; } catch (_) { /* raw */ }
    $("#authErr").textContent = msg;
  }
}

async function doLogout() {
  await api("/api/auth/logout", { method: "POST" });
  ME = null;
  renderAuthUI();
  toast("вышел");
}

async function loadTopics() {
  TOPICS = await api("/api/topics");
  for (const t of TOPICS) ROOT.set(t.id, t.id); // a root is its own topic
  renderTree();
}

async function fetchChildren(id, { more = false } = {}) {
  const cur = KIDS.get(id);
  const offset = more && cur ? cur.children.length : 0;
  const page = await api(`/api/nodes/${id}/children?limit=${PAGE}&offset=${offset}`);
  const merged = more && cur
    ? { total: page.total, children: cur.children.concat(page.children) }
    : page;
  KIDS.set(id, merged);
  const root = ROOT.get(id) ?? id;
  for (const c of merged.children) ROOT.set(c.id, root);
  return merged;
}

// refresh pages we already show (topics + every expanded node), nothing else
async function refreshVisible() {
  await loadTopics();
  await Promise.all([...expanded].map((id) => fetchChildren(id).catch(() => {})));
  renderTree();
}

// ---- tree render
function relLabel(type) {
  return { support: "за", refute: "против", qualify: "уточн.", question: "вопрос", root: "тема" }[type] || type;
}
function nodeRow(node, type) {
  const row = el("div", "row" + (node.id === selectedId ? " sel" : ""));
  row.dataset.id = node.id;
  const hasKids = (node.reply_count ?? 0) > 0;
  const tw = el("span", "tw", hasKids ? (expanded.has(node.id) ? "▾" : "▸") : "·");
  tw.onclick = (e) => { e.stopPropagation(); toggleExpand(node.id); };
  row.appendChild(tw);
  row.appendChild(el("span", "rel " + type, relLabel(type)));
  const txt = el("span", "txt", node.text);
  row.appendChild(txt);
  if (hasKids) row.appendChild(el("span", "poi", "(" + node.reply_count + ")"));
  const poi = el("span", "poi");
  poi.innerHTML = node.poi_score != null ? "PoI <b>" + node.poi_score + "</b>" : "…";
  row.appendChild(poi);
  const who = el("span", "who", node.author || "—");
  if (node.author_color) who.style.color = node.author_color;
  else who.classList.add("muted");
  row.appendChild(who);
  row.onclick = () => selectNode(node.id);
  return row;
}
function renderSubtree(container, node, type) {
  container.appendChild(nodeRow(node, type));
  if (!expanded.has(node.id)) return;
  const page = KIDS.get(node.id);
  const box = el("div", "children");
  if (!page) {
    box.appendChild(el("div", "row muted", "загрузка…"));
  } else {
    for (const c of page.children) renderSubtree(box, c, c.rel);
    const left = page.total - page.children.length;
    if (left > 0) {
      const moreRow = el("div", "row muted", `▸ ещё ${left}…`);
      moreRow.onclick = async () => {
        await fetchChildren(node.id, { more: true });
        renderTree();
      };
      box.appendChild(moreRow);
    }
  }
  container.appendChild(box);
}
function renderTree() {
  const tree = $("#tree");
  tree.innerHTML = "";
  if (!TOPICS.length) { tree.appendChild(el("div", "muted", "нет тем")); return; }
  for (const t of TOPICS) renderSubtree(tree, t, "root");
}
async function toggleExpand(id) {
  if (expanded.has(id)) {
    expanded.delete(id);
    renderTree();
    return;
  }
  expanded.add(id);
  renderTree();                      // shows "загрузка…" immediately
  if (!KIDS.has(id)) await fetchChildren(id);
  renderTree();
}

// ---- detail panel
async function selectNode(id) {
  selectedId = id;
  renderTree();
  if (!expanded.has(id)) toggleExpand(id);

  let node;
  try { node = await api(`/api/nodes/${id}`); }
  catch (e) { toast("ошибка: " + e.message); return; }
  const root = ROOT.get(id) ?? id;
  const isRoot = root === id;
  const d = $("#detail");
  d.innerHTML = "";

  // node card
  const card = el("div", "card");
  card.appendChild(el("h2", null, node.text));
  const meta = el("div", "muted");
  meta.append(
    "автор: ", node.author || "—",
    "  ·  PoI: " + (node.poi_score != null ? node.poi_score : "оценивается…"),
    "  ·  тип: " + (node.kind || "argument"),
    "  ·  ответов: " + (node.reply_count ?? 0),
    "  ·  #" + node.id
  );
  card.appendChild(meta);
  if (node.poi_breakdown && !node.poi_breakdown.seed) card.appendChild(critBreakdown(node.poi_breakdown));
  d.appendChild(card);

  // reactions
  const rc = el("div", "card");
  rc.appendChild(el("div", "section-title", "реакции (взвешены по PoI темы)"));
  const rbody = el("div"); rbody.textContent = "загрузка…";
  rc.appendChild(rbody);
  const ract = el("div", "actions");
  const agree = el("button", "mini", "▲ согласен");
  const dis = el("button", "mini", "▼ не согласен");
  agree.onclick = () => react(id, root, "agree");
  dis.onclick = () => react(id, root, "disagree");
  ract.append(agree, dis);
  rc.appendChild(ract);
  d.appendChild(rc);
  loadReactions(id, root, rbody);

  // reply form
  d.appendChild(replyForm(id));

  // positions (only for the discussion root)
  if (isRoot) {
    const pc = el("div", "card");
    pc.appendChild(el("div", "section-title", "позиции (пулы) по теме"));
    const pbody = el("div"); pbody.textContent = "сборка позиций…";
    pc.appendChild(pbody);
    d.appendChild(pc);
    loadPositions(root, pbody);
  }
}

function critBreakdown(bd) {
  const box = el("div", "crit");
  for (const [k, v] of Object.entries(bd)) {
    if (["topic", "comment", "kind", "seed"].includes(k)) continue;
    box.appendChild(el("span", "muted", k));
    const bar = el("div", "bar");
    const span = el("span"); span.style.width = Math.max(0, Math.min(100, v)) + "%";
    bar.appendChild(span);
    box.appendChild(bar);
    box.appendChild(el("span", null, String(v)));
  }
  if (bd.comment) {
    const c = el("div", "muted"); c.style.gridColumn = "1/4"; c.style.marginTop = "4px";
    c.textContent = "“" + bd.comment + "”";
    box.appendChild(c);
  }
  return box;
}

function bucketRow(data, cls) {
  const wrap = el("div");
  const bars = el("div", "buckets");
  const max = Math.max(1, ...data.buckets.map((b) => b.count));
  for (const b of data.buckets) {
    const col = el("div", "bk " + cls);
    col.style.height = (10 + (b.count / max) * 90) + "%";
    col.title = b.label + ": " + b.count;
    if (!b.count) col.style.opacity = ".25";
    bars.appendChild(col);
  }
  wrap.appendChild(bars);
  return wrap;
}

async function loadReactions(id, root, target) {
  try {
    const r = await api(`/api/reactions/${id}?topic=${root}`);
    target.innerHTML = "";
    const line = el("div", "muted");
    line.textContent = `за: ${r.agree.count} (Σ PoI ${r.agree.weight}) · против: ${r.disagree.count} (Σ PoI ${r.disagree.weight}) · нетто ${r.net_weight}`;
    target.appendChild(line);
    if (r.agree.count) { target.appendChild(el("div", "muted", "поддержка →")); target.appendChild(bucketRow(r.agree, "agree")); }
    if (r.disagree.count) { target.appendChild(el("div", "muted", "возражение →")); target.appendChild(bucketRow(r.disagree, "disagree")); }
    if (!r.agree.count && !r.disagree.count) target.appendChild(el("div", "muted", "пока нет реакций"));
  } catch (e) { target.textContent = "ошибка: " + e.message; }
}

async function react(id, root, stance) {
  if (!requireAuth()) return;
  try {
    await api("/api/reactions", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ node_id: id, stance }),
    });
    toast("реакция учтена");
    if (selectedId === id) selectNode(id);
  } catch (e) { toast("ошибка: " + e.message); }
}

// ---- AI navigator: pre-publication check against the topic's positions.
// A suggestion, never a block — "отправить всё равно" is always available.
async function precheck(root, text) {
  try {
    return await api(`/api/topics/${root}/precheck`, {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ text }),
    });
  } catch (_) { return { verdict: "new" }; }   // fail-open
}

function replyForm(parentId) {
  const card = el("div", "card");
  card.appendChild(el("div", "section-title", "ответить (создаёт дочерний узел)"));
  const ta = el("textarea");
  ta.placeholder = "аргумент…";
  card.appendChild(ta);
  const act = el("div", "actions");
  const typeSel = el("select");
  for (const [v, l] of [["support", "за"], ["refute", "против"], ["qualify", "уточнение"]])
    typeSel.appendChild(new Option(l, v));
  const send = el("button", "primary", "отправить");
  const hint = el("div");                       // the navigator's suggestion box
  hint.style.display = "none";

  const doSend = async (text) => {
    try {
      await api("/api/argument", {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({
          text, connect_to: parentId, edge_type: typeSel.value,
        }),
      });
      toast("добавлено — PoI оценивается в фоне…");
      expanded.add(parentId);
      await fetchChildren(parentId);   // refresh just this branch
      await loadTopics();              // reply counts on roots may change
      selectNode(parentId);
    } catch (e) { toast("ошибка: " + e.message); }
  };

  send.onclick = async () => {
    const text = ta.value.trim();
    if (!text) return;
    if (!requireAuth()) return;
    send.disabled = true; send.textContent = "смотрю, нет ли похожего…";
    const root = ROOT.get(parentId) ?? parentId;
    const pre = await precheck(root, text);
    send.disabled = false; send.textContent = "отправить";
    if (pre.verdict === "new") { await doSend(text); return; }

    // similar/covered: show what exists, offer to support it or post anyway
    hint.innerHTML = "";
    hint.style.display = "";
    hint.className = "card";
    hint.style.borderColor = "var(--bronze)";
    hint.appendChild(el("div", "section-title",
      pre.verdict === "covered" ? "это уже есть в обсуждении" : "похожая позиция уже есть"));
    hint.appendChild(el("b", null, pre.headline || ""));
    if (pre.note) hint.appendChild(el("div", "muted", pre.note));
    const hactions = el("div", "actions");
    const supp = el("button", "mini", "▲ поддержать её");
    supp.onclick = async () => {
      hint.style.display = "none";
      ta.value = "";
      await positionVote(pre.position_id, root, "agree");
    };
    const anyway = el("button", "mini", "отправить всё равно");
    anyway.onclick = async () => { hint.style.display = "none"; await doSend(text); };
    const cancel = el("button", "mini", "отмена");
    cancel.onclick = () => { hint.style.display = "none"; };
    hactions.append(supp, anyway, cancel);
    hint.appendChild(hactions);
  };
  act.append(typeSel, send);
  card.appendChild(act);
  card.appendChild(hint);
  return card;
}

// ---- positions
async function loadPositions(root, target) {
  try {
    const data = await api(`/api/positions/${root}`);
    target.innerHTML = "";
    if (!data.positions.length) { target.appendChild(el("div", "muted", "нет позиций")); return; }
    for (const p of data.positions) target.appendChild(positionCard(p, root));
  } catch (e) { target.textContent = "ошибка: " + e.message; }
}

function positionCard(p, root) {
  const box = el("div"); box.style.borderTop = "1px solid var(--line)";
  box.style.padding = "10px 0";
  const head = el("div");
  head.append(el("span", "pill stance-" + (p.stance || "mixed"), p.stance || "mixed"), " ");
  head.appendChild(el("b", null, p.headline || "(без заголовка)"));
  box.appendChild(head);
  if (p.composed) box.appendChild(el("div", "muted", p.composed));
  const sup = p.support;
  box.appendChild(el("div", "muted", `сторонников: ${sup.count} · Σ PoI ${sup.weight}`));
  if (sup.count) box.appendChild(bucketRow(sup, "agree"));

  const act = el("div", "actions");
  const vote = el("button", "mini", "▲ поддержать");
  vote.onclick = () => positionVote(p.id, root, "agree");
  const cont = el("button", "mini", "развить");
  cont.onclick = () => positionText(p.id, root, "continue", "чем развить позицию?");
  const opp = el("button", "mini", "оспорить");
  opp.onclick = () => positionText(p.id, root, "oppose", "контр-аргумент:");
  const q = el("button", "mini", "вопрос");
  q.onclick = () => positionText(p.id, root, "question", "острый вопрос к позиции:");
  act.append(vote, cont, opp, q);
  box.appendChild(act);

  if (p.planets && p.planets.length) {
    const pl = el("div", "muted"); pl.style.marginTop = "6px";
    pl.textContent = "спутники: " + p.planets.map((x) => `${x.kind}·PoI${x.poi ?? "—"}`).join(", ");
    box.appendChild(pl);
  }
  return box;
}

async function positionVote(pid, root, stance) {
  if (!requireAuth()) return;
  try {
    await api(`/api/positions/${pid}/vote`, {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ stance }),
    });
    toast("голос учтён");
    selectNode(root);
  } catch (e) { toast("ошибка: " + e.message); }
}

async function positionText(pid, root, action, prompt_) {
  if (!requireAuth()) return;
  const text = prompt(prompt_);
  if (!text || !text.trim()) return;
  // navigator pre-check (suggestion, not a block) — skip for "continue",
  // which by definition builds on the position it belongs to
  if (action !== "continue") {
    const pre = await precheck(root, text.trim());
    if (pre.verdict !== "new" && pre.position_id !== pid) {
      const msg = (pre.verdict === "covered"
        ? "Это уже есть в обсуждении: «" : "Похожая позиция уже есть: «")
        + (pre.headline || "") + "»\n" + (pre.note || "") + "\n\nВсё равно отправить?";
      if (!confirm(msg)) return;
    }
  }
  try {
    await api(`/api/positions/${pid}/${action}`, {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ text: text.trim() }),
    });
    toast("отправлено — PoI оценивается в фоне…");
    if (KIDS.has(root)) await fetchChildren(root);
    selectNode(root);
  } catch (e) { toast("ошибка: " + e.message); }
}

// ---- live updates: refresh only what is on screen (topics + expanded pages)
function listenEvents() {
  try {
    const es = new EventSource("/api/events");
    es.onmessage = (ev) => {
      let data = {};
      try { data = JSON.parse(ev.data); } catch (_) { /* ignore */ }
      // scoring finished for the node open in the detail panel → redraw it
      if (data.type === "node_scored" && data.node_id === selectedId) {
        selectNode(selectedId);
      }
      if (data.type === "node_score_failed") {
        toast("оценка узла #" + data.node_id + " не удалась");
      }
      if (data.type === "position_updated" && selectedId != null) {
        selectNode(selectedId); // re-render positions with the composed headline
      }
      clearTimeout(listenEvents._t);
      listenEvents._t = setTimeout(() => refreshVisible(), 500);
    };
  } catch (e) { /* SSE optional */ }
}

// ---- boot
$("#refresh").onclick = () => refreshVisible();
$("#loginBtn").onclick = () => openAuth();
$("#logoutBtn").onclick = () => doLogout();
$("#doLogin").onclick = () => doAuth("/api/auth/login");
$("#doRegister").onclick = () => doAuth("/api/auth/register");
$("#authCancel").onclick = () => closeAuth();
$("#authPass").addEventListener("keydown", (e) => {
  if (e.key === "Enter") doAuth("/api/auth/login");
});
(async function boot() {
  try {
    await loadMe();
    await loadTopics();
    listenEvents();
  } catch (e) {
    $("#tree").innerHTML = '<div class="muted" style="padding:10px">' +
      "не удалось загрузить: " + e.message + "<br>Postgres запущен? seed выполнен?</div>";
  }
})();
