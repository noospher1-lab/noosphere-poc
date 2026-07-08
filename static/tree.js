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
  const badge = $("#poiBadge");
  badge.innerHTML = "";
  if (ME) {
    u.textContent = ME.name + " (@" + ME.username + ")";
    if (ME.color) u.style.color = ME.color;
    $("#loginBtn").style.display = "none";
    $("#logoutBtn").style.display = "";
    // vault: the UI must show "PoI 10 · пройди диалог или аргументируй"
    if (ME.dialogue_poi == null) {
      const a = el("a", null, "PoI 10 · пройди диалог PoI ▸");
      a.href = "/dialogue.html";
      a.title = "стартовый PoI = 10; пройди диалог или аргументируй, чтобы его поднять";
      badge.appendChild(a);
    } else {
      badge.textContent = "PoI диалога: " + Math.round(ME.dialogue_poi);
    }
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
  return { support: "за", refute: "против", qualify: "уточн.", question: "вопрос",
           proposal: "предл.", exploration: "разбор", atom: "атом", root: "тема" }[type] || type;
}
const KIND_CHIP = { question: "вопрос", proposal: "предложение", exploration: "разбор" };
function nodeRow(node, type) {
  const row = el("div", "row" + (node.id === selectedId ? " sel" : ""));
  row.dataset.id = node.id;
  const hasKids = (node.reply_count ?? 0) > 0;
  const tw = el("span", "tw", hasKids ? (expanded.has(node.id) ? "▾" : "▸") : "·");
  tw.onclick = (e) => { e.stopPropagation(); toggleExpand(node.id); };
  row.appendChild(tw);
  row.appendChild(el("span", "rel " + type, relLabel(type)));
  if (type === "root" && KIND_CHIP[node.kind])
    row.appendChild(el("span", "rel question", KIND_CHIP[node.kind]));
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
  // an atom of an exploration is a point under investigation: it carries no
  // LLM base score (the разбор was scored as a whole) and no taken position
  meta.append(
    "автор: ", node.author || "—",
    "  ·  PoI: " + (node.poi_score != null ? node.poi_score
                    : node.atom_group ? "— (атом разбора, живёт реакциями)" : "оценивается…"),
    "  ·  тип: " + (node.kind || "argument"),
    ...(node.atom_group ? ["  ·  из разбора · " + node.atom_group] : []),
    "  ·  ответов: " + (node.reply_count ?? 0),
    "  ·  #" + node.id
  );
  card.appendChild(meta);
  if (node.poi_breakdown && !node.poi_breakdown.seed) card.appendChild(critBreakdown(node.poi_breakdown));
  d.appendChild(card);

  // atomization: the author of an exploration can cut it into atoms
  if (node.kind === "exploration" && ME && node.author_id === ME.id)
    d.appendChild(atomizeCard(node));

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

    // the PoI lens: who argues here and with what understanding of the topic
    const ac = el("div", "card");
    ac.appendChild(el("div", "section-title", "участники · PoI в теме"));
    const abody = el("div"); abody.textContent = "загрузка…";
    ac.appendChild(abody);
    d.appendChild(ac);
    loadTopicPoi(root, abody);
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

// ---- atomization (vault: exploration-atomization): the AI proposes groups
// and atoms cut from the разбор in the author's own words; the author edits,
// unchecks, and confirms — nothing reaches the graph without their consent.
const ATOM_LABEL = { argument: "тезис", question: "вопрос",
                     detail: "дополнение", proposal: "предложение" };

function atomizeCard(node) {
  const card = el("div", "card");
  card.appendChild(el("div", "section-title", "атомизация разбора"));
  card.appendChild(el("div", "muted",
    "ИИ предложит разбить разбор на группы и атомы (тезисы, вопросы, " +
    "дополнения, предложения) твоими же словами; ты правишь и подтверждаешь — " +
    "без подтверждения в граф ничего не попадает."));
  const act = el("div", "actions");
  const go = el("button", "mini", "⚛ предложить атомизацию");
  const box = el("div");
  box.style.display = "none";
  go.onclick = async () => {
    go.disabled = true; go.textContent = "ИИ читает разбор…";
    let pre;
    try { pre = await api(`/api/nodes/${node.id}/atomize`, { method: "POST" }); }
    catch (e) {
      toast("ошибка: " + e.message);
      go.disabled = false; go.textContent = "⚛ предложить атомизацию";
      return;
    }
    go.disabled = false; go.textContent = "⚛ предложить заново";
    renderAtomPreview(box, node, pre.groups || []);
  };
  act.appendChild(go);
  card.appendChild(act);
  card.appendChild(box);
  return card;
}

function renderAtomPreview(box, node, groups) {
  box.innerHTML = "";
  box.style.display = "";
  if (!groups.length) {
    box.appendChild(el("div", "muted", "ИИ не нашёл, что атомизировать"));
    return;
  }
  const rows = [];
  for (const g of groups) {
    box.appendChild(el("div", "section-title", "группа: " + g.title));
    for (const a of g.atoms) {
      const line = el("div");
      const chk = el("input");
      chk.type = "checkbox"; chk.checked = true; chk.title = "включить этот атом";
      line.appendChild(chk);
      line.appendChild(el("span", "rel " + (a.type === "question" ? "question" : "qualify"),
                          ATOM_LABEL[a.type] || a.type));
      const ta = el("textarea");
      ta.value = a.text; ta.rows = 2;
      line.appendChild(ta);
      box.appendChild(line);
      rows.push({ chk, ta, type: a.type, group: g.title });
    }
  }
  const act = el("div", "actions");
  const make = el("button", "primary", "создать атомы");
  make.onclick = async () => {
    const atoms = rows.filter((r) => r.chk.checked && r.ta.value.trim())
      .map((r) => ({ type: r.type, text: r.ta.value.trim(), group: r.group }));
    if (!atoms.length) { toast("не выбран ни один атом"); return; }
    make.disabled = true; make.textContent = "создаю…";
    try {
      const res = await api(`/api/nodes/${node.id}/atomize/confirm`, {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ atoms }),
      });
      toast("создано атомов: " + res.created.length);
      box.style.display = "none";
      expanded.add(node.id);
      await fetchChildren(node.id);
      await loadTopics();
      selectNode(node.id);
    } catch (e) {
      toast("ошибка: " + e.message);
      make.disabled = false; make.textContent = "создать атомы";
    }
  };
  const cancel = el("button", "mini", "отмена");
  cancel.onclick = () => { box.style.display = "none"; };
  act.append(make, cancel);
  box.appendChild(act);
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

async function loadTopicPoi(root, target) {
  try {
    const rows = await api(`/api/topic_poi/${root}`);
    // the endpoint returns EVERY author, defaulted to 10 — keep only those who
    // left a trace in this topic (a prior or a recomputed PoI). Known trade-off:
    // a participant whose computed PoI is exactly 10 with no prior is hidden.
    const active = rows.filter((r) => r.prior != null || r.poi !== 10);
    active.sort((a, b) => (b.poi ?? 0) - (a.poi ?? 0));
    target.innerHTML = "";
    if (!active.length) {
      target.appendChild(el("div", "muted", "пока все на стартовом PoI 10"));
      return;
    }
    for (const r of active) {
      const line = el("div", "row");
      const nm = el("span", "txt", r.name);
      if (r.color) nm.style.color = r.color;
      line.appendChild(nm);
      const poi = el("span", "poi");
      poi.innerHTML = "PoI <b>" + Math.round(r.poi) + "</b>";
      line.appendChild(poi);
      target.appendChild(line);
    }
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

// ---- AI navigator, full pass (vault: ai-navigator-draft-review): BEFORE a
// draft is published, one call checks its type, suggests one improvement and
// looks for existing answers/counters in the graph. A suggestion, never a
// block — fail-open: any error means "post as is".
async function reviewDraft(payload) {
  try {
    return await api("/api/draft/review", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch (_) { return null; }
}

function reviewHasNotes(rev) {
  return !!rev && (!rev.type_ok || !!rev.quality_note || rev.verdict !== "new");
}

const TYPE_LABEL = { support: "за", refute: "против", qualify: "уточнение",
                     question: "вопрос", proposal: "предложение", exploration: "разбор" };

// Renders the navigator's suggestions into `hint`. The author stays in charge:
// callbacks wire "switch type", "go to the node", "support the position",
// "post as is" and "cancel"; editing the draft and resending re-reviews it.
function renderReview(hint, rev, { root, onSend, onSwitch, onSupport, onSplit, switchLabel }) {
  hint.innerHTML = "";
  hint.style.display = "";
  hint.className = "card";
  hint.style.borderColor = "var(--bronze)";
  hint.appendChild(el("div", "section-title", "ИИ-навигатор — подсказка перед публикацией"));

  const actions = el("div", "actions");

  if (!rev.type_ok && rev.suggested_type) {
    const t = el("div");
    t.appendChild(el("b", null, "похоже, тип не совпадает. "));
    t.appendChild(document.createTextNode(rev.type_note || ""));
    hint.appendChild(t);
    // the quality note and verdict were already computed for the suggested
    // type (one LLM call covers both versions) — switching publishes right away
    const sw = el("button", "mini",
      "отправить как «" + (switchLabel || TYPE_LABEL[rev.suggested_type] || rev.suggested_type) + "»");
    sw.onclick = () => { hint.style.display = "none"; onSwitch(rev.suggested_type); };
    actions.appendChild(sw);
  }

  if (rev.verdict === "answered" || rev.verdict === "countered") {
    hint.appendChild(el("div", "section-title",
      rev.verdict === "answered" ? "на этот вопрос уже есть ответ"
                                 : "на этот тезис уже есть возражение"));
    hint.appendChild(el("b", null, rev.target_text || ""));
    if (rev.note) hint.appendChild(el("div", "muted", rev.note));
    const go = el("button", "mini", "перейти к узлу");
    go.onclick = () => {
      hint.style.display = "none";
      if (root != null) ROOT.set(rev.node_id, root);
      selectNode(rev.node_id);
    };
    actions.appendChild(go);
  } else if (rev.verdict === "similar" || rev.verdict === "covered") {
    hint.appendChild(el("div", "section-title",
      rev.verdict === "covered" ? "это уже есть в обсуждении" : "похожая позиция уже есть"));
    hint.appendChild(el("b", null, rev.headline || ""));
    if (rev.note) hint.appendChild(el("div", "muted", rev.note));
    if (onSupport) {
      const supp = el("button", "mini", "▲ поддержать её");
      supp.onclick = () => onSupport(rev.position_id);
      actions.appendChild(supp);
    }
  }

  // two contributions glued together: editable split preview — the parts are
  // the author's own words, each will be its own node under its own rubric
  if (rev.split && onSplit) {
    hint.appendChild(el("div", "section-title", "похоже, здесь два вклада"));
    const parts = [];
    for (const p of rev.split) {
      const line = el("div");
      line.appendChild(el("span", "rel " + (p.type === "question" ? "question" : p.type),
                          TYPE_LABEL[p.type] || p.type));
      const pta = el("textarea");
      pta.value = p.text; pta.rows = 2;
      line.appendChild(pta);
      hint.appendChild(line);
      parts.push({ type: p.type, ta: pta });
    }
    hint.appendChild(el("div", "muted",
      "каждая часть станет отдельным узлом и получит оценку по своей рубрике"));
    const both = el("button", "mini", "опубликовать оба");
    both.onclick = () => {
      const out = parts.map((p) => ({ type: p.type, text: p.ta.value.trim() }))
                       .filter((p) => p.text);
      if (out.length < 2) { toast("обе части должны быть непустыми"); return; }
      hint.style.display = "none";
      onSplit(out);
    };
    actions.appendChild(both);
  }

  if (rev.quality_note) {
    const q = el("div");
    q.appendChild(el("b", null, "как усилить: "));
    q.appendChild(document.createTextNode(rev.quality_note));
    hint.appendChild(q);
    hint.appendChild(el("div", "muted", "поправь текст и нажми «отправить» ещё раз — навигатор перечитает"));
  }

  const anyway = el("button", "mini", "отправить как есть");
  anyway.onclick = () => { hint.style.display = "none"; onSend(); };
  const cancel = el("button", "mini", "отмена");
  cancel.onclick = () => { hint.style.display = "none"; };
  actions.append(anyway, cancel);
  hint.appendChild(actions);
}

function replyForm(parentId) {
  const card = el("div", "card");
  card.appendChild(el("div", "section-title", "ответить (создаёт дочерний узел)"));
  const ta = el("textarea");
  ta.placeholder = "аргумент…";
  card.appendChild(ta);
  const act = el("div", "actions");
  const typeSel = el("select");
  for (const [v, l] of [["support", "за"], ["refute", "против"], ["qualify", "уточнение"],
                        ["question", "вопрос"], ["proposal", "предложение"], ["exploration", "разбор"]])
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

  const runReview = async () => {
    const text = ta.value.trim();
    if (!text) return;
    if (!requireAuth()) return;
    send.disabled = true; send.textContent = "ИИ читает черновик…";
    const root = ROOT.get(parentId) ?? parentId;
    const rev = await reviewDraft({ text, connect_to: parentId, edge_type: typeSel.value });
    send.disabled = false; send.textContent = "отправить";
    // nothing to suggest (or the navigator is down) → publish silently
    if (!reviewHasNotes(rev)) { hint.style.display = "none"; await doSend(text); return; }

    renderReview(hint, rev, {
      root,
      onSend: async () => { await doSend(ta.value.trim()); },
      // advice on the card already applies to the suggested type — publish
      onSwitch: async (type) => {
        typeSel.value = type;
        await doSend(ta.value.trim());
      },
      onSupport: async (pid) => {
        hint.style.display = "none";
        ta.value = "";
        await positionVote(pid, root, "agree");
      },
      // two glued contributions, approved by the author → two sibling nodes
      onSplit: async (parts) => {
        try {
          for (const p of parts) {
            await api("/api/argument", {
              method: "POST", headers: { "content-type": "application/json" },
              body: JSON.stringify({ text: p.text, connect_to: parentId, edge_type: p.type }),
            });
          }
          toast("добавлено " + parts.length + " узла — PoI оценивается в фоне…");
          ta.value = "";
          expanded.add(parentId);
          await fetchChildren(parentId);
          await loadTopics();
          selectNode(parentId);
        } catch (e) { toast("ошибка: " + e.message); }
      },
    });
  };
  send.onclick = runReview;
  act.append(typeSel, send);
  card.appendChild(act);
  card.appendChild(hint);
  return card;
}

// ---- new topic: a root node, opened straight from the header
function newTopicForm() {
  selectedId = null;
  renderTree();
  const d = $("#detail");
  d.innerHTML = "";
  const card = el("div", "card");
  card.appendChild(el("div", "section-title", "новая тема (корень обсуждения)"));
  const ta = el("textarea");
  ta.placeholder = "тезис, вопрос, предложение или разбор, открывающий обсуждение…";
  card.appendChild(ta);
  const act = el("div", "actions");
  const kindSel = el("select");
  kindSel.appendChild(new Option("тезис", "argument"));
  kindSel.appendChild(new Option("вопрос", "question"));
  kindSel.appendChild(new Option("предложение", "proposal"));
  kindSel.appendChild(new Option("разбор", "exploration"));
  const send = el("button", "primary", "создать тему");
  const cancel = el("button", "mini", "отмена");
  cancel.onclick = () => { d.innerHTML = '<div class="muted">выбери узел слева</div>'; };
  const hint = el("div");                       // the navigator's suggestion box
  hint.style.display = "none";

  const doCreate = async (text) => {
    send.disabled = true; send.textContent = "создаю…";
    try {
      // no connect_to => a new root; the review has no topic to compare
      // against yet, so only the type/quality checks apply here
      const node = await api("/api/argument", {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ text, kind: kindSel.value }),
      });
      toast("тема создана — PoI оценивается в фоне…");
      ROOT.set(node.id, node.id);
      await loadTopics();
      selectNode(node.id);
    } catch (e) {
      toast("ошибка: " + e.message);
      send.disabled = false; send.textContent = "создать тему";
    }
  };

  const runReview = async () => {
    const text = ta.value.trim();
    if (!text) return;
    if (!requireAuth()) return;
    send.disabled = true; send.textContent = "ИИ читает черновик…";
    const rev = await reviewDraft({ text, kind: kindSel.value });
    send.disabled = false; send.textContent = "создать тему";
    if (!reviewHasNotes(rev)) { hint.style.display = "none"; await doCreate(text); return; }

    // a root is a thesis, question, proposal or exploration — map onto kinds
    const rootKind = KIND_CHIP[rev.suggested_type] ? rev.suggested_type : "argument";
    renderReview(hint, rev, {
      root: null,
      switchLabel: KIND_CHIP[rootKind] || "тезис",
      onSend: async () => { await doCreate(ta.value.trim()); },
      // advice on the card already applies to the suggested kind — publish
      onSwitch: async () => {
        kindSel.value = rootKind;
        await doCreate(ta.value.trim());
      },
    });
  };
  send.onclick = runReview;
  act.append(kindSel, send, cancel);
  card.appendChild(act);
  card.appendChild(hint);
  d.appendChild(card);
  ta.focus();
}

// ---- positions
async function loadPositions(root, target) {
  try {
    const data = await api(`/api/positions/${root}`);
    target.innerHTML = "";
    if (!data.positions.length) { target.appendChild(el("div", "muted", "нет позиций")); return; }
    const byId = new Map(data.positions.map((p) => [p.id, p]));
    for (const p of data.positions) target.appendChild(positionCard(p, root, byId, data.links || []));
  } catch (e) { target.textContent = "ошибка: " + e.message; }
}

function positionCard(p, root, byId, links) {
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

  // inter-position links, both directions (oppose / conclusion)
  const LINK_OUT = { oppose: "⚔ оспаривает: ", conclusion: "✦ вывод из: " };
  const LINK_IN = { oppose: "⚔ оспорена: ", conclusion: "→ есть вывод: " };
  for (const l of links || []) {
    let label = null, other = null;
    if (l.from_position === p.id) { label = LINK_OUT[l.type]; other = byId.get(l.to_position); }
    else if (l.to_position === p.id) { label = LINK_IN[l.type]; other = byId.get(l.from_position); }
    if (!label || !other) continue;  // link to a position dropped by a re-cluster
    box.appendChild(el("div", "muted", label + "«" + (other.headline || "позиция #" + other.id) + "»"));
  }

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
  if (p.stance !== "conclusion") {
    const concl = el("button", "mini", "сделать вывод");
    concl.onclick = async () => {
      if (!requireAuth()) return;
      if (!confirm("ИИ синтезирует вывод из позиции и её веток. Продолжить?")) return;
      concl.disabled = true; concl.textContent = "ИИ делает вывод…";
      try {
        await api(`/api/positions/${p.id}/conclude`, { method: "POST" });
        toast("вывод создан");
        selectNode(root);   // re-renders positions; no new tree node appears
      } catch (e) {
        toast("ошибка: " + e.message);
        concl.disabled = false; concl.textContent = "сделать вывод";
      }
    };
    act.append(concl);
  }
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
      if (data.type === "topic_poi_updated" && selectedId != null
          && ROOT.get(selectedId) === data.topic_root_id) {
        selectNode(selectedId); // participants card shows fresh per-topic PoI
      }
      clearTimeout(listenEvents._t);
      listenEvents._t = setTimeout(() => refreshVisible(), 500);
    };
  } catch (e) { /* SSE optional */ }
}

// ---- boot
$("#refresh").onclick = () => refreshVisible();
$("#newTopic").onclick = () => newTopicForm();
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
