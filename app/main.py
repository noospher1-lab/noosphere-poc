"""
Noosphere PoC API — async, backed by Postgres.

Endpoints:
    GET  /api/graph          -> full graph (nodes + links)
    GET  /api/topics         -> root nodes = discussions (folders in the tree UI)
    POST /api/argument       -> add an argument node, score it via LLM, return it
    POST /api/edge           -> connect two nodes with a typed edge
    GET  /api/weights        -> vote-weights computed from current PoI scores
    ...plus authors, per-topic PoI, reactions, and positions (Layer 2).

Concurrency: every write goes through the asyncpg pool so many users can write
at once. The blocking LLM scoring call is pushed to a thread (asyncio.to_thread)
so it never stalls the event loop.
"""

import asyncio
import json
import re

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from pathlib import Path

from . import auth, db, dialogue as dialogue_mod, poi, voteweight, pools as pools_mod

app = FastAPI(title="Noosphere PoC", version="0.2.0")


class EventHub:
    """
    Minimal in-process pub/sub for Server-Sent Events. Each SSE client gets its
    own asyncio.Queue. No persistence, no backpressure — enough to make the live
    graph feel alive (constitution Part II: prove it's real, not the protocol).
    """

    def __init__(self):
        self._subscribers: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop):
        self._loop = loop

    async def subscribe(self):
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.add(q)
        try:
            while True:
                yield await q.get()
        finally:
            self._subscribers.discard(q)

    def publish(self, event: dict):
        if self._loop is None:
            return
        data = json.dumps(event)
        for q in list(self._subscribers):
            self._loop.call_soon_threadsafe(q.put_nowait, data)


hub = EventHub()


# ---- background scoring (scaling draft, principle 4: scoring is async)
#
# A new node is persisted IMMEDIATELY with poi_score = NULL and the response
# returns right away; the LLM call runs in a background task (off-loop via
# to_thread) and the result arrives as an SSE "node_scored" event. The user
# never waits on the LLM, and one slow scoring call never blocks another write.
_bg_tasks: set[asyncio.Task] = set()


def _spawn(coro):
    """Fire-and-forget task, kept referenced so it isn't garbage-collected."""
    t = asyncio.create_task(coro)
    _bg_tasks.add(t)
    t.add_done_callback(_bg_tasks.discard)


async def _score_later(node_id: int, text: str, kind: str = "argument"):
    fn = poi.score_question if kind == "question" else poi.score_argument
    try:
        score, breakdown = await asyncio.to_thread(fn, text)
    except Exception as e:
        # score stays NULL ("unscored"); the client just keeps showing "…"
        hub.publish({"type": "node_score_failed", "node_id": node_id, "error": str(e)})
        return
    await db.update_node_score(node_id, score, breakdown)
    hub.publish({
        "type": "node_scored",
        "node_id": node_id,
        "poi_score": score,
        "weight": voteweight.vote_weight(score),
    })
    # the author's topic PoI accrues from their scored contributions
    node = await db.get_node(node_id)
    if node and node.get("author_id") and node.get("topic_root_id"):
        value = await db.recompute_topic_poi(node["author_id"], node["topic_root_id"])
        hub.publish({"type": "topic_poi_updated", "author_id": node["author_id"],
                     "topic_root_id": node["topic_root_id"], "poi": value})


async def _assign_position_later(node_id: int, text: str):
    """
    Background, incremental clustering (scaling draft: the full topic re-cluster
    never runs on a write). The LLM sees only the topic's POSITIONS (a handful),
    decides "belongs to #N / is new", and — when it joins an existing pool —
    re-composes that one pool to integrate the new point. Cost per argument is
    flat no matter how large the topic grows. Full re-cluster stays available
    as an explicit, rare operation (GET /api/positions/{root}?recompute=true).
    """
    try:
        root = await db.topic_root_of(node_id)
        positions = await db.list_positions(root)
        if not positions:
            return          # first read of the topic runs the initial clustering
        result = await asyncio.to_thread(
            pools_mod.assign_argument, text,
            [{"id": p["id"], "headline": p["headline"], "composed": p["composed"]}
             for p in positions])
        pid = result.get("position_id")
        if pid is not None and any(p["id"] == pid for p in positions):
            await db.set_node_position(node_id, pid)
            # the product loop: the pool re-composes to absorb the new point
            members = await db.position_nodes(pid, "argument")
            comp = await asyncio.to_thread(
                pools_mod.compose_one, [m["text"] for m in members])
            await db.update_position(pid, comp.get("headline", ""),
                                     comp.get("composed", ""))
        else:
            pid = await db.add_position(root, result.get("headline", text[:60]),
                                        result.get("composed", text),
                                        result.get("stance", "mixed"))
            await db.set_node_position(node_id, pid)
        hub.publish({"type": "position_updated", "position_id": pid})
    except Exception as e:
        hub.publish({"type": "position_assign_failed", "node_id": node_id,
                     "error": str(e)})


@app.on_event("startup")
async def _startup():
    await db.init_pool()
    await db.init_db()
    hub.bind_loop(asyncio.get_running_loop())


@app.on_event("shutdown")
async def _shutdown():
    await db.close_pool()


# ---------------------------------------------------------------- accounts
# The author of EVERY write comes from the session (HTTP-only cookie), never
# from the request body — the client cannot claim to be someone else. Reads
# stay public (observer mode). Seeded personas (username=NULL) cannot log in.
SESSION_COOKIE = "session"
SESSION_DAYS = 30

# deterministic per-username tint for new accounts
_PALETTE = ["#d8af6e", "#5aa9e6", "#b98cff", "#57d98a",
            "#e2933f", "#e25b56", "#6ee7dc", "#e6a3c8"]


async def current_author(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        author = await db.session_author(token)
        if author is not None:
            return author
    raise HTTPException(401, "требуется вход")


def _set_session(response: Response, token: str):
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax",
                        max_age=SESSION_DAYS * 86400, path="/")


class RegisterIn(BaseModel):
    username: str
    password: str
    name: str | None = None


class LoginIn(BaseModel):
    username: str
    password: str


@app.post("/api/auth/register")
async def register(body: RegisterIn, response: Response):
    username = body.username.strip().lower()
    if not re.fullmatch(r"[a-z0-9_]{3,32}", username):
        raise HTTPException(400, "логин: 3–32 символа, латиница/цифры/подчёркивание")
    if len(body.password) < 6:
        raise HTTPException(400, "пароль: минимум 6 символов")
    color = _PALETTE[sum(username.encode()) % len(_PALETTE)]
    author_id = await db.add_user(
        username, auth.hash_password(body.password),
        (body.name or username).strip() or username, color)
    if author_id is None:
        raise HTTPException(409, "логин занят")
    token = auth.new_token()
    await db.create_session(token, author_id, SESSION_DAYS)
    _set_session(response, token)
    return await db.get_author(author_id)


@app.post("/api/auth/login")
async def login(body: LoginIn, response: Response):
    a = await db.get_author_by_username(body.username.strip().lower())
    if not a or not a.get("password_hash") or \
            not auth.verify_password(body.password, a["password_hash"]):
        raise HTTPException(401, "неверный логин или пароль")
    token = auth.new_token()
    await db.create_session(token, a["id"], SESSION_DAYS)
    _set_session(response, token)
    return {k: v for k, v in a.items() if k != "password_hash"}


@app.post("/api/auth/logout")
async def logout(request: Request, response: Response):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        await db.delete_session(token)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@app.get("/api/auth/me")
async def me(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        return await db.session_author(token)   # author or JSON null
    return None


class ArgumentIn(BaseModel):
    text: str
    connect_to: int | None = None         # optional: target node id (None = new branch / root)
    edge_type: str | None = "support"     # support / refute / qualify


class EdgeIn(BaseModel):
    source_id: int
    target_id: int
    type: str


class AuthorIn(BaseModel):
    name: str
    reputation: float = 50.0              # 0..100 track-record PoI
    color: str | None = None


class ReputationIn(BaseModel):
    reputation: float                    # 0..100


@app.get("/api/graph")
async def get_graph():
    return await db.get_graph()


@app.get("/api/topics")
async def get_topics():
    """Root discussions — the top-level folders of the tree UI."""
    return await db.list_topics()


# The tree UI's read contract (scaling draft, principle 3): the client asks for
# the NODE it looks at and a RANKED PAGE of children — never the whole graph.
# /api/graph stays only for the force-directed visualization mode.
@app.get("/api/nodes/{node_id}")
async def get_node(node_id: int):
    node = await db.get_node_full(node_id)
    if node is None:
        raise HTTPException(404, f"node {node_id} not found")
    return node


@app.get("/api/nodes/{node_id}/children")
async def get_children(node_id: int, limit: int = 20, offset: int = 0):
    if await db.get_node(node_id) is None:
        raise HTTPException(404, f"node {node_id} not found")
    limit = max(1, min(100, limit))
    offset = max(0, offset)
    return await db.get_children(node_id, limit, offset)


@app.post("/api/argument")
async def add_argument(arg: ArgumentIn, author=Depends(current_author)):
    if not arg.text.strip():
        raise HTTPException(400, "argument text is empty")

    # 0. resolve the discussion this argument joins (None = it opens a new topic)
    root_id = None
    if arg.connect_to is not None:
        parent = await db.get_node(arg.connect_to)
        if parent is None:
            raise HTTPException(404, f"connect_to node {arg.connect_to} not found")
        root_id = parent.get("topic_root_id") or await db.topic_root_of(arg.connect_to)

    # 1. persist the node RIGHT AWAY, unscored (poi_score = NULL)
    node_id = await db.add_node(arg.text, author_id=author["id"], topic_root_id=root_id)

    # 2. optional typed edge to an existing node (None = a new root branch)
    edge = None
    if arg.connect_to is not None:
        await db.add_edge(node_id, arg.connect_to, arg.edge_type or "support")
        edge = {
            "source_id": node_id,
            "target_id": arg.connect_to,
            "type": arg.edge_type or "support",
        }

    # 3. scoring AND pool assignment happen in the background
    _spawn(_score_later(node_id, arg.text))
    _spawn(_assign_position_later(node_id, arg.text))

    node = await db.get_node(node_id)
    node["weight"] = 0.0                       # unscored contributes zero weight
    node["author"] = author["name"]
    node["author_color"] = author["color"]
    node["scoring"] = "pending"

    # 4. broadcast so live front-ends can materialize the node + fire a pulse
    hub.publish({
        "type": "node_added",
        "node": {
            "id": node_id,
            "text": arg.text,
            "poi_score": None,
            "weight": 0.0,
            "author": node["author"],
            "author_color": node["author_color"],
        },
        "edge": edge,
    })
    return node


@app.post("/api/edge")
async def add_edge(edge: EdgeIn):
    for nid in (edge.source_id, edge.target_id):
        if await db.get_node(nid) is None:
            raise HTTPException(404, f"node {nid} not found")
    try:
        edge_id = await db.add_edge(edge.source_id, edge.target_id, edge.type)
    except ValueError as e:
        raise HTTPException(400, str(e))
    hub.publish({
        "type": "edge_added",
        "edge": {
            "source_id": edge.source_id,
            "target_id": edge.target_id,
            "type": edge.type,
        },
    })
    return {"id": edge_id, **edge.model_dump()}


@app.get("/api/weights")
async def get_weights():
    return voteweight.compute_graph_weights(await db.get_graph())


# ---------------------------------------------------------------- authors
@app.get("/api/authors")
async def get_authors():
    return await db.list_authors()


@app.post("/api/authors")
async def create_author(author: AuthorIn):
    if not author.name.strip():
        raise HTTPException(400, "author name is empty")
    author_id = await db.add_author(author.name.strip(), author.reputation, author.color)
    return await db.get_author(author_id)


@app.patch("/api/authors/{author_id}")
async def set_reputation(author_id: int, body: ReputationIn):
    if not await db.update_author_reputation(author_id, body.reputation):
        raise HTTPException(404, f"author {author_id} not found")
    return await db.get_author(author_id)


class TopicPoiIn(BaseModel):
    poi: float                           # 0..100


class ReactionIn(BaseModel):
    node_id: int
    stance: str                          # 'agree' | 'disagree'


# Ten PoI buckets (0–10, 11–20, …, 91–100) plus a "no data" slot.
BUCKET_LABELS = ["0–10"] + [f"{b*10+1}–{b*10+10}" for b in range(1, 10)]


def _bucket_index(poi):
    if poi is None:
        return None
    if poi <= 10:
        return 0
    import math
    return min(9, math.ceil(poi / 10) - 1)


def _bucket_support(pois):
    """List of PoI values -> count, PoI-weighted sum, and a 10-bucket histogram."""
    buckets = [0] * 10
    no_data = 0
    weight = 0.0
    for p in pois:
        bi = _bucket_index(p)
        if bi is None:
            no_data += 1
        else:
            buckets[bi] += 1
            weight += p
    return {
        "count": len(pois),
        "weight": round(weight, 1),
        "buckets": [{"label": BUCKET_LABELS[i], "count": buckets[i]} for i in range(10)],
        "no_data": no_data,
    }


def _aggregate_reactions(rows):
    """Turn raw reaction rows into per-stance counts, PoI-weighted sums, and a
    histogram of reactors across PoI buckets (the thing shown on node open)."""
    out = {}
    for stance in ("agree", "disagree"):
        items = [r for r in rows if r["stance"] == stance]
        buckets = [0] * 10
        no_data = 0
        weight = 0.0
        for r in items:
            bi = _bucket_index(r["poi"])
            if bi is None:
                no_data += 1
            else:
                buckets[bi] += 1
                weight += r["poi"]
        out[stance] = {
            "count": len(items),
            "weight": round(weight, 1),          # sum of reactors' topic-PoI
            "buckets": [{"label": BUCKET_LABELS[i], "count": buckets[i]} for i in range(10)],
            "no_data": no_data,
            "reactors": [{"name": r["name"], "poi": r["poi"], "color": r["color"]} for r in items],
        }
    out["net_weight"] = round(out["agree"]["weight"] - out["disagree"]["weight"], 1)
    return out


# ---------------------------------------------------------------- per-topic PoI
@app.get("/api/topic_poi/{topic_root_id}")
async def get_topic_poi(topic_root_id: int):
    return await db.get_topic_poi(topic_root_id)


# Dev tool: sets the PRIOR (P₀) — the stand-in for the onboarding dialogue's
# score. The current poi is then recomputed by the formula (retroactive).
@app.patch("/api/topic_poi/{topic_root_id}/{author_id}")
async def set_topic_prior(topic_root_id: int, author_id: int, body: TopicPoiIn):
    if await db.get_author(author_id) is None:
        raise HTTPException(404, f"author {author_id} not found")
    await db.set_topic_prior(author_id, topic_root_id, body.poi)
    return await db.get_topic_poi(topic_root_id)


# ---------------------------------------------------------------- reactions
@app.post("/api/reactions")
async def post_reaction(r: ReactionIn, author=Depends(current_author)):
    if r.stance not in ("agree", "disagree"):
        raise HTTPException(400, "stance must be 'agree' or 'disagree'")
    node = await db.get_node(r.node_id)
    if node is None:
        raise HTTPException(404, f"node {r.node_id} not found")
    await db.set_reaction(author["id"], r.node_id, r.stance)
    # reactions nudge the NODE AUTHOR's topic PoI (bounded ±5 by the formula);
    # self-reactions are excluded inside the recompute query
    if node.get("author_id") and node.get("topic_root_id") \
            and node["author_id"] != author["id"]:
        value = await db.recompute_topic_poi(node["author_id"], node["topic_root_id"])
        hub.publish({"type": "topic_poi_updated", "author_id": node["author_id"],
                     "topic_root_id": node["topic_root_id"], "poi": value})
    return {"ok": True}


@app.get("/api/reactions/{node_id}")
async def get_reactions(node_id: int, topic: int):
    # `topic` is the discussion's root node id — PoI is per topic.
    rows = await db.get_reactions(node_id, topic)
    return _aggregate_reactions(rows)


# ---------------------------------------------------------------- positions (Layer 2)
class PositionArgIn(BaseModel):
    text: str


class PositionVoteIn(BaseModel):
    stance: str                          # 'agree' | 'disagree'


def _topic_nodes(graph, root_id):
    """All nodes in the discussion (connected component) containing root_id."""
    adj = {n["id"]: [] for n in graph["nodes"]}
    for l in graph["links"]:
        if l["source"] in adj and l["target"] in adj:
            adj[l["source"]].append(l["target"])
            adj[l["target"]].append(l["source"])
    seen, stack = {root_id}, [root_id]
    while stack:
        x = stack.pop()
        for y in adj.get(x, []):
            if y not in seen:
                seen.add(y)
                stack.append(y)
    return [n for n in graph["nodes"] if n["id"] in seen]


async def _recompute_positions(topic_root_id):
    """Full re-cluster: rebuild a topic's positions from its argument nodes."""
    graph = await db.get_graph()
    nodes = _topic_nodes(graph, topic_root_id)
    arg_nodes = [n for n in nodes if (n.get("kind") or "argument") == "argument"]
    await db.clear_positions(topic_root_id)
    if not arg_nodes:
        return
    clustered = await asyncio.to_thread(
        pools_mod.cluster_arguments,
        [{"id": n["id"], "text": n["text"]} for n in arg_nodes])
    by_id = {n["id"] for n in arg_nodes}
    for c in clustered:
        pid = await db.add_position(
            topic_root_id,
            c.get("headline") or c.get("synthesis", ""),
            c.get("composed") or c.get("synthesis", ""),
            c.get("stance", "mixed"))
        for mid in c.get("member_ids", []):
            if mid in by_id:
                await db.set_node_position(mid, pid)


async def _position_support(position_id, topic_root_id):
    """Supporters of a position = authors of its member args + agree-voters."""
    poi_map = {r["author_id"]: r["poi"] for r in await db.get_topic_poi(topic_root_id)}
    supporters = {}
    for n in await db.position_nodes(position_id, "argument"):
        if n.get("author_id"):
            supporters[n["author_id"]] = poi_map.get(n["author_id"])
    for r in await db.get_position_reactions(position_id, topic_root_id):
        if r["stance"] == "agree":
            supporters[r["author_id"]] = r["poi"]
    return _bucket_support(list(supporters.values()))


async def _position_payload(p):
    members = await db.position_nodes(p["id"], "argument")
    planets_raw = await db.position_planets(p["id"])
    planet_ids = {pl["id"] for pl in planets_raw}
    planets = []
    for pl in planets_raw:
        par = await db.parent_of(pl["id"])
        planets.append({
            "id": pl["id"],
            "kind": pl["kind"],                       # 'question' | 'detail'
            "text": pl["text"],
            "poi": pl["poi_score"],                   # questions & details are scored too
            "parent": par if par in planet_ids else None,
        })
    return {
        "id": p["id"],
        "headline": p["headline"],
        "composed": p["composed"],
        "stance": p["stance"],
        "member_ids": [m["id"] for m in members],
        "member_texts": [m["text"] for m in members],
        "planets": planets,
        "support": await _position_support(p["id"], p["topic_root_id"]),
    }


@app.get("/api/positions/{topic_root_id}")
async def get_positions(topic_root_id: int, recompute: bool = False):
    if recompute or not await db.list_positions(topic_root_id):
        try:
            await _recompute_positions(topic_root_id)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(502, f"clustering failed: {e}")
    return {
        "positions": [await _position_payload(p) for p in await db.list_positions(topic_root_id)],
        "links": await db.list_position_links(topic_root_id),
    }


async def _rep_member(position_id, topic_root_id, exclude=None):
    """A representative member node id (for keeping the audit graph connected)."""
    for m in await db.position_nodes(position_id, "argument"):
        if m["id"] != exclude:
            return m["id"]
    return topic_root_id


class BranchIn(BaseModel):
    text: str
    kind: str = "detail"                 # 'detail' | 'question'


@app.post("/api/positions/{position_id}/continue")
async def continue_position(position_id: int, body: PositionArgIn,
                            author=Depends(current_author)):
    # "Развить": a detail planet orbiting the star (scored, like any contribution).
    pos = await db.get_position(position_id)
    if pos is None:
        raise HTTPException(404, f"position {position_id} not found")
    nid = await db.add_node(body.text, author_id=author["id"], kind="detail",
                            position_id=position_id,
                            topic_root_id=pos["topic_root_id"])
    await db.add_edge(nid, await _rep_member(position_id, pos["topic_root_id"]), "qualify")
    _spawn(_score_later(nid, body.text))
    return await _position_payload(pos)


@app.post("/api/nodes/{node_id}/branch")
async def branch_node(node_id: int, body: BranchIn, author=Depends(current_author)):
    # A planet can branch further: a scored child planet (sub-question / detail).
    parent = await db.get_node(node_id)
    if parent is None:
        raise HTTPException(404, f"node {node_id} not found")
    kind = body.kind if body.kind in ("question", "detail") else "detail"
    pid = parent.get("position_id")
    nid = await db.add_node(body.text, author_id=author["id"], kind=kind,
                            position_id=pid,
                            topic_root_id=parent.get("topic_root_id"))
    await db.add_edge(nid, node_id, "question" if kind == "question" else "qualify")
    _spawn(_score_later(nid, body.text, kind))
    pos = await db.get_position(pid) if pid else None
    return await _position_payload(pos) if pos else {"ok": True}


@app.post("/api/positions/{position_id}/conclude")
async def conclude_position(position_id: int, author=Depends(current_author)):
    # "Сделать вывод": synthesize the star + its orbit into the next node forward.
    pos = await db.get_position(position_id)
    if pos is None:
        raise HTTPException(404, f"position {position_id} not found")
    planets = await db.position_planets(position_id)
    planet_texts = [pl["text"] for pl in planets]
    try:
        c = await asyncio.to_thread(
            pools_mod.conclude, pos["composed"] or pos["headline"], planet_texts)
    except Exception as e:
        raise HTTPException(502, f"conclusion failed: {e}")
    new_pid = await db.add_position(pos["topic_root_id"],
                                    c.get("headline", ""), c.get("composed", ""), "conclusion")
    await db.add_position_link(new_pid, position_id, "conclusion")   # new concludes from old
    return await _position_payload(await db.get_position(new_pid))


async def _compose_later(position_id: int, texts: list[str]):
    """Background: LLM writes the position's headline/synthesis, then notify."""
    try:
        comp = await asyncio.to_thread(pools_mod.compose_one, texts)
    except Exception as e:
        hub.publish({"type": "position_compose_failed", "position_id": position_id,
                     "error": str(e)})
        return
    await db.update_position(position_id, comp.get("headline", ""),
                             comp.get("composed", texts[0] if texts else ""))
    hub.publish({"type": "position_updated", "position_id": position_id})


@app.post("/api/positions/{position_id}/oppose")
async def oppose_position(position_id: int, body: PositionArgIn,
                          author=Depends(current_author)):
    pos = await db.get_position(position_id)
    if pos is None:
        raise HTTPException(404, f"position {position_id} not found")
    # Node + position appear immediately (fallback headline = the raw text);
    # scoring AND the composed headline both arrive later via SSE.
    nid = await db.add_node(body.text, author_id=author["id"], kind="argument",
                            topic_root_id=pos["topic_root_id"])
    await db.add_edge(nid, await _rep_member(position_id, pos["topic_root_id"]), "refute")
    new_pid = await db.add_position(pos["topic_root_id"], body.text[:60], body.text, "oppose")
    await db.set_node_position(nid, new_pid)
    await db.add_position_link(new_pid, position_id, "oppose")
    _spawn(_score_later(nid, body.text))
    _spawn(_compose_later(new_pid, [body.text]))
    return await _position_payload(await db.get_position(new_pid))


@app.post("/api/positions/{position_id}/question")
async def question_position(position_id: int, body: PositionArgIn,
                            author=Depends(current_author)):
    pos = await db.get_position(position_id)
    if pos is None:
        raise HTTPException(404, f"position {position_id} not found")
    # A question is a planet (not a position), scored with the QUESTION rubric —
    # a sharp question that exposes a weak point scores high. Scored in the bg.
    nid = await db.add_node(body.text, author_id=author["id"], kind="question",
                            position_id=position_id,
                            topic_root_id=pos["topic_root_id"])
    await db.add_edge(nid, await _rep_member(position_id, pos["topic_root_id"]), "question")
    _spawn(_score_later(nid, body.text, "question"))
    return await _position_payload(await db.get_position(position_id))


@app.post("/api/positions/{position_id}/vote")
async def vote_position(position_id: int, body: PositionVoteIn,
                        author=Depends(current_author)):
    if body.stance not in ("agree", "disagree"):
        raise HTTPException(400, "stance must be 'agree' or 'disagree'")
    if await db.get_position(position_id) is None:
        raise HTTPException(404, f"position {position_id} not found")
    await db.set_position_reaction(author["id"], position_id, body.stance)
    pos = await db.get_position(position_id)
    return await _position_payload(pos)


@app.post("/api/reset")
async def reset_graph():
    """Wipe and re-seed the graph — a clean slate between test runs."""
    from . import seed
    await seed.run_within_pool()
    return {"ok": True, **await db.get_graph()}


# ---------------------------------------------------------------- PoI dialogue
# The onboarding dialogue (vault: poi-accrual-onboarding). ONE per author, no
# retakes — resumable and continuable. Each finalize scores the accumulated
# transcript and becomes the author's default prior (P₀), applied retroactively.
class DialoguePreIn(BaseModel):
    vote: str                            # 'yes' | 'no'
    reasoning: str
    confidence: str                      # 'leaning' | 'confident' | 'very_sure'


class DialogueMsgIn(BaseModel):
    text: str


class DialogueInformIn(BaseModel):
    accept: bool


class DialoguePostIn(BaseModel):
    vote: str
    reasoning: str
    confidence: str
    reflection: str


def _dlg_user_turns(d):
    """Real user turns (bracketed inform-offer resolutions don't count)."""
    return sum(1 for t in d["turns"]
               if t["role"] == "user" and not t["content"].startswith("["))


def _ingest_ai_reply(turns, raw):
    """Split an interlocutor reply into turns; an INFORM_OFFER becomes a
    pending offer turn the user must accept or decline."""
    m = re.search(r"\[INFORM_OFFER\]([\s\S]*?)\[/INFORM_OFFER\]", raw)
    if m:
        rest = (raw[:m.start()] + raw[m.end():]).strip()
        if rest:
            turns.append({"role": "ai", "content": rest})
        turns.append({"role": "ai", "content": m.group(1).strip(),
                      "meta": "inform_offer_pending"})
    else:
        turns.append({"role": "ai", "content": raw.strip()})


def _dlg_meta(d):
    d["user_turns"] = _dlg_user_turns(d)
    d["min_turns"] = dialogue_mod.MIN_TURNS_TO_FINALIZE
    d["max_turns"] = dialogue_mod.MAX_TURNS
    return d


@app.get("/api/dialogue")
async def get_dialogue_state(author=Depends(current_author)):
    d = await db.get_dialogue(author["id"])
    return _dlg_meta(d) if d else None


@app.post("/api/dialogue/start")
async def start_dialogue(author=Depends(current_author)):
    d = await db.start_dialogue(author["id"], dialogue_mod.TOPIC)
    return _dlg_meta(d)


@app.post("/api/dialogue/pre")
async def dialogue_pre(body: DialoguePreIn, author=Depends(current_author)):
    d = await db.get_dialogue(author["id"])
    if d is None:
        raise HTTPException(404, "диалог не начат")
    if d["phase"] != "pre":
        raise HTTPException(409, "пре-позиция уже зафиксирована")
    if body.vote not in ("yes", "no") or \
            body.confidence not in ("leaning", "confident", "very_sure"):
        raise HTTPException(400, "некорректные vote/confidence")
    if len(body.reasoning.strip()) < 100:
        raise HTTPException(400, "аргументация: минимум 100 символов")
    pre = {"vote": body.vote, "reasoning": body.reasoning.strip(),
           "confidence": body.confidence}
    # first interlocutor turn (blocking LLM — off-loop; the user is waiting
    # for the dialogue to open, so this one is synchronous by design)
    try:
        reply = await asyncio.to_thread(
            dialogue_mod.interlocutor_reply, d["topic"], pre, [])
    except Exception as e:
        raise HTTPException(502, f"собеседник недоступен: {e}")
    turns = []
    _ingest_ai_reply(turns, reply)
    await db.update_dialogue(author["id"], phase="dialogue", pre=pre, turns=turns)
    return _dlg_meta(await db.get_dialogue(author["id"]))


@app.post("/api/dialogue/message")
async def dialogue_message(body: DialogueMsgIn, author=Depends(current_author)):
    d = await db.get_dialogue(author["id"])
    if d is None or d["phase"] != "dialogue":
        raise HTTPException(409, "диалог не в активной фазе")
    if not body.text.strip():
        raise HTTPException(400, "пустое сообщение")
    if d["turns"] and d["turns"][-1].get("meta") == "inform_offer_pending":
        raise HTTPException(409, "сначала ответь на предложение информации")
    if _dlg_user_turns(d) >= dialogue_mod.MAX_TURNS:
        raise HTTPException(409, "бюджет ходов исчерпан — подведи итог")
    turns = d["turns"]
    turns.append({"role": "user", "content": body.text.strip()})
    try:
        reply = await asyncio.to_thread(
            dialogue_mod.interlocutor_reply, d["topic"], d["pre"], turns)
    except Exception as e:
        turns.pop()                       # don't persist a turn the AI never saw
        raise HTTPException(502, f"собеседник недоступен: {e}")
    _ingest_ai_reply(turns, reply)
    await db.update_dialogue(author["id"], turns=turns)
    return _dlg_meta(await db.get_dialogue(author["id"]))


@app.post("/api/dialogue/inform")
async def dialogue_inform(body: DialogueInformIn, author=Depends(current_author)):
    d = await db.get_dialogue(author["id"])
    if d is None or d["phase"] != "dialogue":
        raise HTTPException(409, "диалог не в активной фазе")
    turns = d["turns"]
    pending = next((t for t in reversed(turns)
                    if t.get("meta") == "inform_offer_pending"), None)
    if pending is None:
        raise HTTPException(409, "нет ожидающего предложения информации")
    pending["meta"] = "inform_offer_accepted" if body.accept else "inform_offer_declined"
    turns.append({"role": "user", "content":
                  "[User accepted the information offer. Provide the information now.]"
                  if body.accept else "[User declined the information offer.]"})
    if body.accept:
        try:
            reply = await asyncio.to_thread(
                dialogue_mod.interlocutor_reply, d["topic"], d["pre"], turns)
        except Exception as e:
            raise HTTPException(502, f"собеседник недоступен: {e}")
        _ingest_ai_reply(turns, reply)
    await db.update_dialogue(author["id"], turns=turns)
    return _dlg_meta(await db.get_dialogue(author["id"]))


@app.post("/api/dialogue/finalize")
async def dialogue_finalize(body: DialoguePostIn, author=Depends(current_author)):
    d = await db.get_dialogue(author["id"])
    if d is None or d["phase"] not in ("dialogue", "post"):
        raise HTTPException(409, "диалог не готов к подведению итога")
    if _dlg_user_turns(d) < dialogue_mod.MIN_TURNS_TO_FINALIZE:
        raise HTTPException(400,
            f"минимум {dialogue_mod.MIN_TURNS_TO_FINALIZE} содержательных ходов "
            f"до подведения итога (сейчас {_dlg_user_turns(d)})")
    if body.vote not in ("yes", "no") or \
            body.confidence not in ("leaning", "confident", "very_sure"):
        raise HTTPException(400, "некорректные vote/confidence")
    if len(body.reasoning.strip()) < 100 or len(body.reflection.strip()) < 50:
        raise HTTPException(400, "аргументация ≥100 символов, рефлексия ≥50")
    post = {"vote": body.vote, "reasoning": body.reasoning.strip(),
            "confidence": body.confidence, "reflection": body.reflection.strip()}
    await db.update_dialogue(author["id"], phase="post", post=post)
    try:
        scores = await asyncio.to_thread(
            dialogue_mod.judge_session, d["topic"], d["pre"], d["turns"], post)
        total = float(scores["total"])
    except Exception as e:
        # phase stays 'post' — finalize can be retried without losing anything
        raise HTTPException(502, f"оценка не удалась, попробуй ещё раз: {e}")
    await db.update_dialogue(author["id"], phase="results", scores=scores)
    # the score becomes the author's default prior — retroactively
    await db.set_dialogue_poi(author["id"], total)
    for root in await db.author_topic_roots(author["id"]):
        value = await db.recompute_topic_poi(author["id"], root)
        hub.publish({"type": "topic_poi_updated", "author_id": author["id"],
                     "topic_root_id": root, "poi": value})
    return _dlg_meta(await db.get_dialogue(author["id"]))


@app.post("/api/dialogue/continue")
async def dialogue_continue(author=Depends(current_author)):
    # no retakes — but the SAME dialogue continues; next finalize re-scores
    # the whole accumulated transcript
    d = await db.get_dialogue(author["id"])
    if d is None or d["phase"] != "results":
        raise HTTPException(409, "продолжать можно после подведённого итога")
    if _dlg_user_turns(d) >= dialogue_mod.MAX_TURNS:
        raise HTTPException(409, "бюджет ходов исчерпан")
    await db.update_dialogue(author["id"], phase="dialogue")
    return _dlg_meta(await db.get_dialogue(author["id"]))


# ---------------------------------------------------------------- AI navigator
# Pre-publication check (vault: poi-accrual-onboarding, "ИИ-навигатор"): before
# a draft goes into the graph, the LLM compares it with the topic's POSITIONS
# and, if the point is already made or the question already answered, suggests
# looking there first. A suggestion, never a block: fail-open on any error,
# and the client can always post anyway.
class PrecheckIn(BaseModel):
    text: str


_PRECHECK_NEW = {"verdict": "new", "position_id": None, "note": ""}


@app.post("/api/topics/{topic_root_id}/precheck")
async def precheck_draft(topic_root_id: int, body: PrecheckIn,
                         author=Depends(current_author)):
    if not body.text.strip():
        return _PRECHECK_NEW
    positions = await db.list_positions(topic_root_id)
    if not positions:
        return _PRECHECK_NEW              # nothing to compare against yet
    try:
        result = await asyncio.to_thread(
            pools_mod.precheck_draft, body.text,
            [{"id": p["id"], "headline": p["headline"], "composed": p["composed"]}
             for p in positions])
    except Exception:
        return _PRECHECK_NEW              # fail-open: never stand in the way
    pid = result.get("position_id")
    known = {p["id"]: p for p in positions}
    if result.get("verdict") not in ("similar", "covered") or pid not in known:
        return _PRECHECK_NEW
    return {
        "verdict": result["verdict"],
        "position_id": pid,
        "headline": known[pid]["headline"],
        "note": result.get("note", ""),
    }


# The append-only event log (audit layer, Layer 1): every argument and every
# vote, visible piece by piece. Poll with ?after_id= for incremental reads.
@app.get("/api/log")
async def get_log(after_id: int = 0, limit: int = 200):
    return await db.get_events(after_id, max(1, min(1000, limit)))


@app.get("/api/events")
async def events():
    """Server-Sent Events: one pulse per real node/edge addition."""
    async def gen():
        async for data in hub.subscribe():
            yield f"data: {data}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# Serve the front-ends from static/: index.html is the tree UI, graph.html is
# the force-directed visualization (an optional mode, kept for later).
static_dir = Path(__file__).parent.parent / "static"
if static_dir.exists():
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
