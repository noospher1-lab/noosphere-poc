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
import hashlib
import json
import logging
import os
import re
import secrets
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from pathlib import Path

from . import (auth, db, dialogue as dialogue_mod, material, poi, taxonomy,
               voteweight,
               votedialogue, pools as pools_mod)


@asynccontextmanager
async def lifespan(app):
    # startup: open the pool, ensure schema, bind the SSE hub to this loop
    await db.init_pool()
    await db.init_db()
    hub.bind_loop(asyncio.get_running_loop())
    yield
    # shutdown
    await db.close_pool()


app = FastAPI(title="Noosphere PoC", version="0.2.0", lifespan=lifespan)

# Dev tools (reset, personas, manual PoI priors, raw edges) are opt-in via
# DEV_TOOLS=1 in .env — they must never be reachable through a public tunnel.
DEV_TOOLS = os.environ.get("DEV_TOOLS") == "1"


def dev_only():
    if not DEV_TOOLS:
        raise HTTPException(403, "дев-инструмент отключён (DEV_TOOLS=1 включает)")


# Separate from DEV_TOOLS on purpose: the admin dialogue viewer is read-only
# but exposes testers' full transcripts, and the server may sit behind a
# public tunnel while DEV_TOOLS stays off. Gated by its own bearer secret.
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN")


def admin_only(x_admin_token: str | None = Header(default=None)):
    if not ADMIN_TOKEN or not x_admin_token or not secrets.compare_digest(x_admin_token, ADMIN_TOKEN):
        raise HTTPException(403, "нужен верный заголовок X-Admin-Token (ADMIN_TOKEN в .env)")


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


# each kind is judged by its own rubric — never by another kind's shape
_SCORERS = {"question": poi.score_question, "proposal": poi.score_proposal,
            "exploration": poi.score_exploration, "detail": poi.score_detail}
# rubrics that judge a contribution against the claim it attaches to
_PARENTED_KINDS = {"question", "detail"}


async def _score_later(node_id: int, text: str, kind: str = "argument", parent_text: str | None = None):
    fn = _SCORERS.get(kind, poi.score_argument)
    try:
        if kind in _PARENTED_KINDS:
            score, breakdown = await asyncio.to_thread(fn, text, parent_text)
        else:
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
    re-composes that one pool to integrate the new point. The assign step is
    bounded by the NUMBER OF POSITIONS (a handful), not by the topic's size; the
    re-compose that follows a join grows with that one pool's membership, not the
    topic. Full re-cluster stays an explicit, rare op (?recompute=true).
    """
    try:
        root = await db.topic_root_of(node_id)
        # a dissent position is one author's pinned verbatim stance — never a
        # merge candidate, or a new argument could re-enter someone's own pool
        positions = [p for p in await db.list_positions(root)
                     if p.get("stance") != "dissent"]
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


# ---------------------------------------------------------------- accounts
# The author of EVERY write comes from the session (HTTP-only cookie), never
# from the request body — the client cannot claim to be someone else. Reads
# stay public (observer mode). Seeded personas (username=NULL) cannot log in.
log = logging.getLogger("noosphere")

# Base URL used to build links that leave the server (password resets). Set it
# to the real origin once the PoC is deployed, or the link a tester receives
# points at localhost.
PUBLIC_URL = os.environ.get("PUBLIC_URL", "http://localhost:8000").rstrip("/")

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


# Set COOKIE_SECURE=1 once the PoC is served over HTTPS (a tunnel or a real
# host). Off by default so plain http://localhost still logs in.
COOKIE_SECURE = os.environ.get("COOKIE_SECURE") == "1"


def _set_session(response: Response, token: str):
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax",
                        secure=COOKIE_SECURE,
                        max_age=SESSION_DAYS * 86400, path="/")


# ---- LLM budget: per-author sliding window over every endpoint that spends an
# LLM call. Behind a public tunnel each such endpoint is an open tap on the API
# key; the budget caps the burn without getting in an honest tester's way
# (posting + chatting stays well under the default 10/min).
LLM_BUDGET = int(os.environ.get("LLM_BUDGET_PER_MIN", "10"))
LLM_WINDOW = 60.0
_llm_calls: dict[int, deque] = defaultdict(deque)

# What a new account is granted, in USD, to spend on the shared key. The
# rate limit above caps the burn per minute; this caps it per tester for the
# whole test. Raise for one person with PUT /api/dev/authors/{id}/balance —
# no restart needed.
DEFAULT_BALANCE_USD = float(os.environ.get("DEFAULT_BALANCE_USD", "3"))


def _llm_budget_check(author_id: int):
    q = _llm_calls[author_id]
    now = time.monotonic()
    while q and now - q[0] > LLM_WINDOW:
        q.popleft()
    if len(q) >= LLM_BUDGET:
        raise HTTPException(
            429, f"не больше {LLM_BUDGET} ИИ-запросов в минуту — подожди немного")
    q.append(now)


# ---- brute-force brake on the unauthenticated endpoints. The LLM budget
# above is keyed by author, which is useless here: whoever is guessing a
# password has no session yet. Keyed by client IP instead.
LOGIN_TRIES = int(os.environ.get("LOGIN_TRIES_PER_MIN", "10"))
_login_calls: dict[str, deque] = defaultdict(deque)


def _login_throttle(request: Request, bucket: str):
    ip = (request.headers.get("x-forwarded-for", "").split(",")[0].strip()
          or (request.client.host if request.client else "?"))
    q = _login_calls[f"{bucket}:{ip}"]
    now = time.monotonic()
    while q and now - q[0] > 60.0:
        q.popleft()
    if len(q) >= LOGIN_TRIES:
        raise HTTPException(429, "слишком много попыток — подожди минуту")
    q.append(now)


async def spend_llm(author):
    """Charge one LLM call to this account: rate limit, then grant, then bind
    the key the call will be billed to.

    Split out of the llm_budget dependency for the one endpoint that decides
    mid-handler whether it is really going to call the model — a draft review
    served from cache must cost neither a rate-limit slot nor a cent.
    """
    _llm_budget_check(author["id"])
    key = await db.author_api_key(author["id"])
    if not key:
        # No key of their own: the shared ANTHROPIC_API_KEY pays, so the grant
        # in balance_usd is the only thing between one tester and everyone
        # else's budget. Handing out a key per tester was the earlier answer
        # and it does not scale to a hundred people — each one is manual work
        # in the Anthropic console.
        #
        # Checked BEFORE the call because the price is known only after it:
        # the cap is therefore soft by exactly one call (cents), and the
        # per-minute rate limit above bounds how fast that edge can be hit.
        # DEV_TOOLS is the local bench, where the grant is not the point.
        if not DEV_TOOLS and await db.budget_left(author["id"]) <= 0:
            raise HTTPException(
                402, "ИИ-бюджет аккаунта исчерпан — напиши, пополним")
    poi.current_api_key.set(key)


async def llm_budget(author=Depends(current_author)):
    """Dependency form of spend_llm() for the endpoints that always call the
    model. Must stay `async def`: a sync dependency runs in a threadpool and
    the contextvar set inside would not propagate back to the handler.
    """
    await spend_llm(author)


class RegisterIn(BaseModel):
    username: str
    password: str
    name: str | None = None
    invite: str | None = None
    email: str | None = None


# Deliberately loose: the point is to catch a typo like "ivan@" or a pasted
# username, not to adjudicate RFC 5322. Whether the address actually works is
# proven by the reset link arriving, not by a regex.
EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


class LoginIn(BaseModel):
    username: str
    password: str


@app.post("/api/auth/register")
async def register(body: RegisterIn, response: Response, request: Request):
    _login_throttle(request, "register")
    username = body.username.strip().lower()
    if not re.fullmatch(r"[a-z0-9_]{3,32}", username):
        raise HTTPException(400, "логин: 3–32 символа, латиница/цифры/подчёркивание")
    if len(body.password) < 6:
        raise HTTPException(400, "пароль: минимум 6 символов")
    email = (body.email or "").strip().lower()
    # Required, not optional: without it a forgotten password is a lost
    # account, and asking 100 people for their address after the fact is the
    # thing this is meant to avoid.
    if not EMAIL_RE.fullmatch(email):
        raise HTTPException(400, "нужна почта — по ней восстанавливается доступ")

    color = _PALETTE[sum(username.encode()) % len(_PALETTE)]
    author_id = await db.add_user(
        username, auth.hash_password(body.password),
        (body.name or username).strip() or username, color,
        invite=body.invite, email=email, balance_usd=DEFAULT_BALANCE_USD)
    # Accounts are handed out on request, so registration is invite-only.
    if author_id == "invite":
        raise HTTPException(403, "нужен действующий код приглашения")
    if author_id == "taken":
        raise HTTPException(409, "логин занят")
    if author_id == "email_taken":
        raise HTTPException(409, "на эту почту уже есть аккаунт")
    token = auth.new_token()
    await db.create_session(token, author_id, SESSION_DAYS)
    _set_session(response, token)
    return await db.get_author(author_id)


@app.post("/api/auth/login")
async def login(body: LoginIn, response: Response, request: Request):
    _login_throttle(request, "login")
    a = await db.get_author_by_username(body.username.strip().lower())
    if not a or not a.get("password_hash") or \
            not auth.verify_password(body.password, a["password_hash"]):
        raise HTTPException(401, "неверный логин или пароль")
    token = auth.new_token()
    await db.create_session(token, a["id"], SESSION_DAYS)
    _set_session(response, token)
    return {k: v for k, v in a.items() if k != "password_hash"}


class ForgotIn(BaseModel):
    email: str


class ResetIn(BaseModel):
    token: str
    password: str


def _hash_reset_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@app.post("/api/auth/forgot")
async def forgot_password(body: ForgotIn, request: Request):
    """Start recovery. Always answers the same, whether or not the address is
    registered — otherwise this endpoint becomes a way to test which emails
    have accounts here."""
    _login_throttle(request, "forgot")
    email = (body.email or "").strip().lower()
    same_answer = {"ok": True,
                   "detail": "если аккаунт с такой почтой есть, ссылка отправлена"}
    if not EMAIL_RE.fullmatch(email):
        return same_answer

    token = secrets.token_urlsafe(32)
    author_id = await db.create_password_reset(email, _hash_reset_token(token))
    if author_id is None:
        return same_answer

    link = f"{PUBLIC_URL}/reset.html?token={token}"
    # No mail server yet, so the link goes to the server log and Alex passes it
    # on by hand. When SMTP exists only this branch changes — the token, the
    # expiry and the redemption flow stay as they are.
    log.warning("PASSWORD RESET for author %s <%s>: %s", author_id, email, link)
    return same_answer


@app.post("/api/auth/reset")
async def reset_password(body: ResetIn, request: Request):
    _login_throttle(request, "reset")
    if len(body.password) < 8:
        raise HTTPException(400, "пароль: минимум 8 символов")
    if not await db.redeem_password_reset(_hash_reset_token(body.token.strip()),
                                          auth.hash_password(body.password)):
        raise HTTPException(400, "ссылка недействительна или уже использована")
    return {"ok": True}


@app.post("/api/auth/logout")
async def logout(request: Request, response: Response):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        await db.delete_session(token)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@app.get("/api/me/profile")
async def my_profile(author=Depends(current_author)):
    """The participant's own cabinet: who they are, what they've contributed,
    what their work has cost. Own data only — there is no {id} variant, so one
    tester cannot read another's spend."""
    return {
        "author": {k: v for k, v in author.items() if k != "password_hash"},
        "account": await db.account_summary(author["id"]),
        "activity": await db.activity_summary(author["id"]),
    }


@app.get("/api/auth/me")
async def me(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        return await db.session_author(token)   # author or JSON null
    return None


class AnchorIn(BaseModel):
    """Якорь ответа на УЧАСТОК текста цели: смещения + сохранённая цитата.
    Хеш версии считает сервер по актуальному тексту цели, клиент его не шлёт."""
    start: int
    end: int
    quote: str


class ArgumentIn(BaseModel):
    text: str
    connect_to: int | None = None         # optional: target node id (None = new branch / root)
    edge_type: str | None = "support"     # support / refute / qualify / undercut / question
    kind: str | None = "argument"         # "question" marks a question node (a root can be one)
    title: str | None = None              # required when this opens a new topic (connect_to is None)
    # Ответ на фрагмент: якорь на участок текста цели (только с connect_to).
    # None = ответ на весь узел. undercut (подорвать) без якоря не принимается.
    anchor: AnchorIn | None = None
    # Рубрика — только для новой темы (connect_to is None). Ответу внутри
    # ветки она не нужна: он наследует рубрику корня.
    domain: str | None = None
    sub: str | None = None
    geo: list[str] | None = None
    tags: list[str] | None = None


class EdgeIn(BaseModel):
    source_id: int
    target_id: int
    type: str


class AuthorIn(BaseModel):
    name: str
    color: str | None = None


@app.middleware("http")
async def meter_llm_usage(request: Request, call_next):
    """Bill every LLM call made while handling this request to its author.

    Middleware rather than per-endpoint code: it wraps the handler in the same
    task, so the contextvar set here is visible to poi.complete_messages deep
    inside, and the flush below runs after the handler has finished and all
    usage has accumulated — something a dependency cannot do.
    """
    sink = []
    poi.current_usage.set(sink)
    response = await call_next(request)

    if not sink:
        return response
    token = request.cookies.get(SESSION_COOKIE)
    author = await db.session_author(token) if token else None
    if author is None:
        return response          # anonymous: nothing to bill
    for r in sink:
        r["cost_usd"] = poi.cost_usd(
            r["model"], r["input_tokens"], r["output_tokens"],
            cache_read_tokens=r.get("cache_read_tokens", 0),
            cache_write_tokens=r.get("cache_write_tokens", 0))
    try:
        await db.record_usage(author["id"], sink, request.url.path)
    except Exception:
        pass                     # metering must never break a working request
    return response


@app.get("/api/config")
async def config():
    """What the front-end needs to know about this instance. Only flags —
    never keys or tokens: this is unauthenticated."""
    return {"dev_tools": DEV_TOOLS}


@app.get("/api/graph")
async def get_graph():
    return await db.get_graph()


@app.get("/api/topics")
async def get_topics():
    """Root discussions — the top-level folders of the tree UI."""
    return await db.list_topics()


@app.get("/api/workspace")
async def get_workspace(author=Depends(current_author)):
    """Рабочее дерево — личная подборка тем, а не каталог всего.

    При первом обращении подборка засеивается самыми живыми темами: пустое
    дерево на входе неотличимо от сломанного, человек не понимает, он чего-то
    не сделал или оно не работает.
    """
    await db.seed_workspace_once(author["id"])
    return await db.workspace_topics(author["id"])


@app.get("/api/workspace/ids")
async def get_workspace_ids(author=Depends(current_author)):
    """Что уже добавлено — карте нужно только это, чтобы нарисовать «+» или «✓»."""
    return {"ids": await db.workspace_ids(author["id"])}


@app.post("/api/workspace/{topic_root_id}")
async def add_to_workspace(topic_root_id: int, author=Depends(current_author)):
    node = await db.get_node(topic_root_id)
    if node is None:
        raise HTTPException(404, f"тема {topic_root_id} не найдена")
    if node.get("topic_root_id") != topic_root_id:
        raise HTTPException(400, "в дерево кладётся тема, а не узел внутри неё")
    await db.workspace_add(author["id"], topic_root_id)
    return {"ok": True, "topic": topic_root_id, "in_workspace": True}


@app.delete("/api/workspace/{topic_root_id}")
async def drop_from_workspace(topic_root_id: int, author=Depends(current_author)):
    await db.workspace_remove(author["id"], topic_root_id)
    return {"ok": True, "topic": topic_root_id, "in_workspace": False}


@app.get("/api/taxonomy")
async def get_taxonomy():
    """Направления, подветви и география — один источник для карты и формы.

    Открыто без входа: это справочник, а не данные участников, и карта должна
    рисоваться до того, как человек залогинился.
    """
    return taxonomy.as_dict()


@app.get("/api/map/topics")
async def get_map_topics():
    """Темы с рубрикой и статистикой — то, из чего строится карта."""
    return await db.map_topics()


class FacetsIn(BaseModel):
    domain: str
    sub: str | None = None
    geo: list[str] | None = None
    tags: list[str] | None = None


@app.put("/api/topics/{topic_root_id}/facets")
async def put_topic_facets(topic_root_id: int, body: FacetsIn,
                           author=Depends(current_author)):
    """Проставить или поменять рубрику темы.

    Правит любой участник, а не только автор: рубрика — это навигация, общая
    для всех, и одна чужая опечатка иначе заперла бы тему в неверной ветке.
    Правка попадает в event log, так что видно, кто менял.
    """
    node = await db.get_node(topic_root_id)
    if node is None:
        raise HTTPException(404, f"тема {topic_root_id} не найдена")
    if node.get("topic_root_id") != topic_root_id:
        raise HTTPException(400, "рубрика ставится только корню обсуждения")
    try:
        dom, sub, geo, tags = taxonomy.validate(body.domain, body.sub,
                                                body.geo, body.tags)
    except ValueError as e:
        raise HTTPException(400, str(e))
    await db.set_topic_facets(topic_root_id, dom, sub,
                              sorted(taxonomy.geo_closure(geo)), tags,
                              author_id=author["id"], event="topic_recategorized")
    return {"ok": True, "topic": topic_root_id,
            "domain": dom, "sub": sub, "geo": geo, "tags": tags}


# ---------------------------------------------------------------- problems
OUTCOME_KINDS = {"success", "partial", "failure", "mixed", "unclear"}


class ProblemIn(BaseModel):
    causes: str | None = None            # причины и составные части
    scale_note: str | None = None        # где, сколько — своими словами
    scale_url: str | None = None         # несущая внешняя ссылка на данные
    scale_excerpt: str | None = None     # сохранённая выдержка (текст, не блоб)


class InterventionIn(BaseModel):
    what: str                            # что пробовали (вмешательство)
    actor: str | None = None             # кто
    geo: str | None = None               # где (метка географии)
    when_text: str | None = None         # когда (год/период, свободный текст)
    outcome: str | None = None           # что вышло
    outcome_kind: str = "unclear"        # success/partial/failure/mixed/unclear
    conditions: str | None = None        # от каких условий зависело
    source_url: str | None = None        # несущая внешняя ссылка
    source_excerpt: str | None = None    # сохранённая выдержка (текст, не блоб)


async def _problem_root_or_404(topic_root_id: int):
    """Проблема ставится только КОРНЮ обсуждения, и корень должен быть kind='problem'."""
    node = await db.get_node(topic_root_id)
    if node is None:
        raise HTTPException(404, f"тема {topic_root_id} не найдена")
    if node.get("topic_root_id") != topic_root_id:
        raise HTTPException(400, "проблема — это корень обсуждения, не узел внутри")
    return node


@app.get("/api/problems/{topic_root_id}")
async def read_problem(topic_root_id: int):
    """Состояние проблемы: авторская рамка (причины, масштаб), сводка исходов и
    сам реестр вмешательств. Открыто без входа — это корпус для чтения."""
    node = await db.get_node(topic_root_id)
    if node is None:
        raise HTTPException(404, f"проблема {topic_root_id} не найдена")
    problem = await db.get_problem(topic_root_id)
    problem["interventions"] = await db.list_interventions(topic_root_id)
    problem["kind"] = node.get("kind")
    return problem


@app.put("/api/problems/{topic_root_id}")
async def put_problem(topic_root_id: int, body: ProblemIn,
                      author=Depends(current_author)):
    """Проставить/поменять состояние проблемы. Правит любой участник, как и
    рубрику: постановка проблемы — общее навигационное благо, а правки видны в
    event log."""
    await _problem_root_or_404(topic_root_id)
    retrieved = datetime.now(timezone.utc) if body.scale_url else None
    await db.set_problem(topic_root_id, causes=body.causes,
                         scale_note=body.scale_note, scale_url=body.scale_url,
                         scale_excerpt=body.scale_excerpt,
                         scale_retrieved_at=retrieved, author_id=author["id"])
    return {"ok": True, **await db.get_problem(topic_root_id)}


@app.post("/api/problems/{topic_root_id}/interventions")
async def post_intervention(topic_root_id: int, body: InterventionIn,
                            author=Depends(current_author)):
    """Внести запись в накопитель решений. Запись факта, свободно, без гейта:
    провал регистрируется наравне с успехом."""
    await _problem_root_or_404(topic_root_id)
    if not body.what.strip():
        raise HTTPException(400, "нужно описать, что пробовали (поле what)")
    if body.outcome_kind not in OUTCOME_KINDS:
        raise HTTPException(400, f"outcome_kind ∈ {sorted(OUTCOME_KINDS)}")
    if body.geo and body.geo not in taxonomy.VALID_GEO:
        raise HTTPException(400, f"неизвестная география: {body.geo!r}")
    retrieved = datetime.now(timezone.utc) if body.source_url else None
    row = await db.add_intervention(
        topic_root_id, what=body.what.strip(), actor=body.actor,
        geo=body.geo, when_text=body.when_text, outcome=body.outcome,
        outcome_kind=body.outcome_kind, conditions=body.conditions,
        source_url=body.source_url, source_excerpt=body.source_excerpt,
        source_retrieved_at=retrieved, author_id=author["id"])
    return row


# The tree UI's read contract (scaling draft, principle 3): the client asks for
# the NODE it looks at and a RANKED PAGE of children — never the whole graph.
# /api/graph stays only for the force-directed visualization mode.
@app.get("/api/nodes/{node_id}")
async def get_node(node_id: int):
    node = await db.get_node_full(node_id)
    if node is None:
        raise HTTPException(404, f"node {node_id} not found")
    # under which problems this node appears (belonging edges) — the home first
    node["belongings"] = await db.node_topics_of(node_id)
    # replies anchored to SPANS of this node's text — the margin markers
    node["fragment_replies"] = await db.node_anchors(node_id)
    return node


@app.post("/api/nodes/{node_id}/belong/{topic_root_id}")
async def belong_node(node_id: int, topic_root_id: int,
                      author=Depends(current_author)):
    """Положить существующий узел под ещё одну проблему — многодомность без копии.

    Ребро несёт автора (кто положил) и время. Класть можно чужой довод под свою
    проблему: «этот аргумент из X бьёт и по Y». Домашняя тема узла не трогается.
    """
    node = await db.get_node(node_id)
    if node is None:
        raise HTTPException(404, f"node {node_id} not found")
    target = await db.get_node(topic_root_id)
    if target is None:
        raise HTTPException(404, f"проблема {topic_root_id} не найдена")
    if target.get("topic_root_id") != topic_root_id:
        raise HTTPException(400, "узел кладётся под КОРЕНЬ проблемы, не под узел внутри")
    created = await db.add_belonging(node_id, topic_root_id, author_id=author["id"])
    return {"ok": True, "node_id": node_id, "topic_root_id": topic_root_id,
            "created": created, "belongings": await db.node_topics_of(node_id)}


@app.delete("/api/nodes/{node_id}/belong/{topic_root_id}")
async def unbelong_node(node_id: int, topic_root_id: int,
                        author=Depends(current_author)):
    """Снять дополнительную принадлежность. Домашнюю снять нельзя."""
    removed = await db.remove_belonging(node_id, topic_root_id)
    if not removed:
        raise HTTPException(400, "нельзя снять домашнюю принадлежность или её нет")
    return {"ok": True, "node_id": node_id, "topic_root_id": topic_root_id,
            "belongings": await db.node_topics_of(node_id)}


@app.get("/api/topics/{topic_root_id}/nodes")
async def topic_corpus(topic_root_id: int):
    """Корпус проблемы через принадлежность-рёбра — включая многодомные узлы,
    принесённые из других проблем. Открыто без входа: это корпус для чтения."""
    if await db.get_node(topic_root_id) is None:
        raise HTTPException(404, f"проблема {topic_root_id} не найдена")
    return await db.topic_nodes(topic_root_id)


@app.get("/api/nodes/{node_id}/children")
async def get_children(node_id: int, limit: int = 20, offset: int = 0):
    if await db.get_node(node_id) is None:
        raise HTTPException(404, f"node {node_id} not found")
    limit = max(1, min(100, limit))
    offset = max(0, offset)
    return await db.get_children(node_id, limit, offset)


@app.post("/api/argument", dependencies=[Depends(llm_budget)])
async def add_argument(arg: ArgumentIn, author=Depends(current_author)):
    if not arg.text.strip():
        raise HTTPException(400, "argument text is empty")

    # 0. resolve the discussion this argument joins (None = it opens a new topic)
    root_id = None
    title = None
    parent_text = None
    if arg.connect_to is not None:
        parent = await db.get_node(arg.connect_to)
        if parent is None:
            raise HTTPException(404, f"connect_to node {arg.connect_to} not found")
        root_id = parent.get("topic_root_id") or await db.topic_root_of(arg.connect_to)
        parent_text = parent["text"]
    else:
        # a new topic root needs a short title distinct from the body text
        title = (arg.title or "").strip()
        if not title:
            raise HTTPException(400, "title is required to open a new topic")
        # Рубрика проверяется ДО создания узла: тема, созданная и тут же
        # оставшаяся без рубрики из-за отказа валидации, потерялась бы на
        # карте — видимая в дереве, но не находимая ни одним фильтром.
        if arg.domain:
            try:
                facets = taxonomy.validate(arg.domain, arg.sub, arg.geo, arg.tags)
            except ValueError as e:
                raise HTTPException(400, str(e))
        else:
            facets = None

    # questions, proposals and explorations are first-class contributions
    # anywhere — as a reply (edge_type) or as a topic root (kind, no edge)
    _SPECIAL = {"question", "proposal", "exploration"}
    # 'problem' is a ROOT-ONLY kind: a discussion that carries STATE (postановка,
    # причины, реестр попыток, спор, решения), never a reply edge type. Only a
    # new topic (connect_to is None) may open as a problem.
    if arg.connect_to is None and arg.kind == "problem":
        kind = "problem"
    else:
        kind = (arg.kind if arg.kind in _SPECIAL
                else arg.edge_type if arg.edge_type in _SPECIAL else "argument")

    # ответ на фрагмент: тип ребра и валидация якоря ДО создания узла (как с
    # рубрикой — не плодим узел, который тут же отклонён валидацией).
    edge_type = None
    anchor_hash = anchor_start = anchor_end = anchor_quote = None
    if arg.connect_to is not None:
        edge_type = kind if kind in _SPECIAL else (arg.edge_type or "support")
        if arg.anchor is not None:
            t = parent_text or ""
            a = arg.anchor
            if not (0 <= a.start < a.end <= len(t)):
                raise HTTPException(400, "смещения якоря вне текста цели")
            if t[a.start:a.end] != a.quote:
                raise HTTPException(400, "цитата якоря не совпадает с текстом по смещениям")
            anchor_start, anchor_end, anchor_quote = a.start, a.end, a.quote
            anchor_hash = db.text_hash(t)
        # подрыв целится в конкретное предложение — без якоря это refute
        if edge_type == "undercut" and anchor_hash is None:
            raise HTTPException(400, "подрыв (undercut) целится в участок — нужен якорь")

    # 1. persist the node RIGHT AWAY, unscored (poi_score = NULL)
    node_id = await db.add_node(arg.text, author_id=author["id"], kind=kind,
                                topic_root_id=root_id, title=title)

    # 1b. рубрика новой темы — сразу после узла, тем же запросом-цепочкой
    if arg.connect_to is None and facets:
        dom, sub, geo, tags = facets
        await db.set_topic_facets(node_id, dom, sub,
                                  sorted(taxonomy.geo_closure(geo)), tags)

    # 1c. участие само кладёт тему в рабочее дерево. Убирать приходится
    # осознанно, добавляется по факту участия — иначе человек написал ответ и
    # потерял ветку, потому что забыл подписаться.
    await db.workspace_add(author["id"], root_id or node_id)

    # 2. optional typed edge to an existing node (None = a new root branch).
    #    edge_type + anchor were resolved and validated above.
    edge = None
    if arg.connect_to is not None:
        await db.add_edge(node_id, arg.connect_to, edge_type,
                          anchor_hash=anchor_hash, anchor_start=anchor_start,
                          anchor_end=anchor_end, anchor_quote=anchor_quote)
        edge = {
            "source_id": node_id,
            "target_id": arg.connect_to,
            "type": edge_type,
            "anchor_start": anchor_start,
            "anchor_end": anchor_end,
            "anchor_quote": anchor_quote,
        }

    # 3. scoring AND pool assignment happen in the background
    # (questions get the QUESTION rubric and never join position pools;
    #  a problem root is a framing with STATE, not a claim judged for weight —
    #  it stays unscored, its "state" is the registry summary, not a PoI)
    if kind != "problem":
        _spawn(_score_later(node_id, arg.text, kind, parent_text))
    if kind == "argument":
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


@app.post("/api/edge", dependencies=[Depends(dev_only)])
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


@app.post("/api/authors", dependencies=[Depends(dev_only)])
async def create_author(author: AuthorIn):
    if not author.name.strip():
        raise HTTPException(400, "author name is empty")
    author_id = await db.add_author(author.name.strip(), author.color)
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
    scored = len(pois) - no_data
    return {
        "count": len(pois),
        "weight": round(weight, 1),
        # avg over SCORED supporters only — poi-as-lens: this reads the group,
        # it is NOT a vote weight (the sum would smuggle "more heads = more power"
        # back in). None when nobody here has a computed PoI yet.
        "avg": round(weight / scored, 1) if scored else None,
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
        scored = len(items) - no_data
        out[stance] = {
            "count": len(items),
            "weight": round(weight, 1),          # sum of reactors' topic-PoI
            # avg over SCORED reactors only — poi-as-lens: reads the group, it is
            # NOT a vote weight. None when none of them has a computed PoI yet.
            "avg": round(weight / scored, 1) if scored else None,
            "buckets": [{"label": BUCKET_LABELS[i], "count": buckets[i]} for i in range(10)],
            "no_data": no_data,
            "reactors": [{"name": r["name"], "poi": r["poi"], "color": r["color"]} for r in items],
        }
    # kept for replayability of the event log, but the UI no longer shows a
    # PoI-weighted sum — a reaction is one head, not PoI-weighted (poi-as-lens).
    out["net_weight"] = round(out["agree"]["weight"] - out["disagree"]["weight"], 1)
    return out


# ---------------------------------------------------------------- per-topic PoI
@app.get("/api/topic_poi/{topic_root_id}")
async def get_topic_poi(topic_root_id: int):
    return await db.get_topic_poi(topic_root_id)


# Dev tool: sets the PRIOR (P₀) — the stand-in for the onboarding dialogue's
# score. The current poi is then recomputed by the formula (retroactive).
@app.patch("/api/topic_poi/{topic_root_id}/{author_id}", dependencies=[Depends(dev_only)])
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
    # freeze the reactor's topic PoI at cast time — the reaction counts this
    # value forever, so a later change to the reactor's PoI can't retroactively
    # (and order-dependently) reweight votes already cast
    root = node.get("topic_root_id") or await db.topic_root_of(r.node_id)
    reactor_weight = await db.topic_poi_of(author["id"], root) if root else None
    await db.set_reaction(author["id"], r.node_id, r.stance, reactor_weight)
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


async def _recompute_positions(topic_root_id):
    """Full re-cluster: rebuild a topic's positions from its argument nodes."""
    # read the topic's arguments straight from the materialized topic_root_id
    # (atoms excluded) — no whole-graph load, no risk of a stray edge dragging in
    # another topic's nodes
    arg_nodes = await db.topic_argument_nodes(topic_root_id)
    # dissented args are pinned: they never go through the LLM re-cluster, they
    # are re-created as their own verbatim positions afterwards (п.10)
    free = [n for n in arg_nodes if not n.get("dissented")]
    pinned = [n for n in arg_nodes if n.get("dissented")]
    await db.clear_positions(topic_root_id)
    if free:
        clustered = await asyncio.to_thread(
            pools_mod.cluster_arguments,
            [{"id": n["id"], "text": n["text"]} for n in free])
        by_id = {n["id"] for n in free}
        for c in clustered:
            pid = await db.add_position(
                topic_root_id,
                c.get("headline") or c.get("synthesis", ""),
                c.get("composed") or c.get("synthesis", ""),
                c.get("stance", "mixed"))
            for mid in c.get("member_ids", []):
                if mid in by_id:
                    await db.set_node_position(mid, pid)
    for n in pinned:
        pid = await db.add_position(topic_root_id, n["text"][:60], n["text"], "dissent")
        await db.set_node_position(n["id"], pid)


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
    # a signed position (a conclusion) names its author; a clustered pool doesn't
    author = None
    if p.get("author_id"):
        a = await db.get_author(p["author_id"])
        author = a["name"] if a else None
    return {
        "id": p["id"],
        "headline": p["headline"],
        "composed": p["composed"],
        "stance": p["stance"],
        "author": author,
        "member_ids": [m["id"] for m in members],
        "member_texts": [m["text"] for m in members],
        "planets": planets,
        "support": await _position_support(p["id"], p["topic_root_id"]),
    }


# The full re-cluster is reachable through an unauthenticated GET, so the
# per-author budget can't cover it; a per-topic cooldown caps how often the
# expensive path can run. Initial clustering (no positions yet) is exempt.
_RECLUSTER_COOLDOWN = 60.0
_last_recluster: dict[int, float] = {}


@app.get("/api/positions/{topic_root_id}")
async def get_positions(topic_root_id: int, recompute: bool = False):
    existing = await db.list_positions(topic_root_id)
    now = time.monotonic()
    if recompute and existing and \
            now - _last_recluster.get(topic_root_id, 0.0) < _RECLUSTER_COOLDOWN:
        recompute = False                 # too soon — serve the existing view
    if recompute or not existing:
        _last_recluster[topic_root_id] = now
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


@app.post("/api/positions/{position_id}/continue",
          dependencies=[Depends(llm_budget)])
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
    # score with the DETAIL rubric against the position it develops (was being
    # scored as a plain argument, the wrong genre for a qualification)
    _spawn(_score_later(nid, body.text, "detail",
                        pos.get("composed") or pos.get("headline")))
    return await _position_payload(pos)


@app.post("/api/nodes/{node_id}/branch", dependencies=[Depends(llm_budget)])
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
    _spawn(_score_later(nid, body.text, kind, parent["text"]))
    pos = await db.get_position(pid) if pid else None
    return await _position_payload(pos) if pos else {"ok": True}


@app.post("/api/positions/{position_id}/conclude",
          dependencies=[Depends(llm_budget)])
async def conclude_position(position_id: int, author=Depends(current_author)):
    """
    "Сделать вывод" — PREVIEW ONLY (п.9). The LLM PROPOSES a conclusion from the
    position and its orbit; nothing is written. Like atomization, the author then
    edits and signs it via .../conclude/confirm, so the conclusion enters the map
    as THEIR scored, attributed claim — the platform never posts anonymous,
    unscored LLM text that shapes the positions map, and the button can no longer
    spawn conclusions on its own.
    """
    pos = await db.get_position(position_id)
    if pos is None:
        raise HTTPException(404, f"position {position_id} not found")
    planet_texts = [pl["text"] for pl in await db.position_planets(position_id)]
    try:
        c = await asyncio.to_thread(
            pools_mod.conclude, pos["composed"] or pos["headline"], planet_texts)
    except Exception as e:
        raise HTTPException(502, f"conclusion failed: {e}")
    return {"headline": c.get("headline", ""), "composed": c.get("composed", "")}


class ConcludeConfirmIn(BaseModel):
    composed: str                        # the author's edited conclusion text
    headline: str = ""                   # optional short title


@app.post("/api/positions/{position_id}/conclude/confirm",
          dependencies=[Depends(llm_budget)])
async def conclude_confirm(position_id: int, body: ConcludeConfirmIn,
                           author=Depends(current_author)):
    """The author signs the (edited) conclusion. It becomes THEIR forward claim:
    a scored, attributed argument node backing a conclusion position linked to
    the source — attribution + scoring instead of anonymous LLM content."""
    pos = await db.get_position(position_id)
    if pos is None:
        raise HTTPException(404, f"position {position_id} not found")
    text = body.composed.strip()
    if not text:
        raise HTTPException(400, "вывод не может быть пустым")
    root = pos["topic_root_id"]
    # the conclusion is the author's claim: a real scored node, connected into
    # the audit graph, backing a conclusion position they signed
    nid = await db.add_node(text, author_id=author["id"], kind="argument",
                            topic_root_id=root)
    await db.add_edge(nid, await _rep_member(position_id, root), "support")
    new_pid = await db.add_position(root, body.headline.strip() or text[:60],
                                    text, "conclusion", author_id=author["id"])
    await db.set_node_position(nid, new_pid)
    await db.add_position_link(new_pid, position_id, "conclusion")
    _spawn(_score_later(nid, text))       # scored like any contribution
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


@app.post("/api/positions/{position_id}/oppose",
          dependencies=[Depends(llm_budget)])
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


@app.post("/api/positions/{position_id}/question",
          dependencies=[Depends(llm_budget)])
async def question_position(position_id: int, body: PositionArgIn,
                            author=Depends(current_author)):
    pos = await db.get_position(position_id)
    if pos is None:
        raise HTTPException(404, f"position {position_id} not found")
    # A question is a planet (not a position), scored with the QUESTION rubric —
    # a sharp question that exposes a weak point scores high. Scored in the bg.
    rep_id = await _rep_member(position_id, pos["topic_root_id"])
    rep_node = await db.get_node(rep_id)
    nid = await db.add_node(body.text, author_id=author["id"], kind="question",
                            position_id=position_id,
                            topic_root_id=pos["topic_root_id"])
    await db.add_edge(nid, rep_id, "question")
    _spawn(_score_later(nid, body.text, "question", rep_node["text"] if rep_node else None))
    return await _position_payload(await db.get_position(position_id))


@app.post("/api/nodes/{node_id}/dissent", dependencies=[Depends(llm_budget)])
async def dissent_position(node_id: int, author=Depends(current_author)):
    """
    "Не согласен с трактовкой" (п.10): the author pulls their argument OUT of the
    pool it was auto-clustered into. Consent by opt-out — clustering stays
    automatic (that is the product), but no one is counted as supporting a
    composed text they reject.

    The argument becomes its OWN position with its VERBATIM text (a position that
    literally repeats the author's words cannot misrepresent them), and is pinned
    so re-clustering never folds it back in. The pool it left is re-composed from
    its remaining members — so it stops speaking for the dissenter — or deleted
    if it is now empty.
    """
    node = await db.get_node(node_id)
    if node is None:
        raise HTTPException(404, f"node {node_id} not found")
    if node.get("author_id") != author["id"]:
        raise HTTPException(403, "выйти из позиции может только автор аргумента")
    if (node.get("kind") or "argument") != "argument":
        raise HTTPException(400, "из позиции выходит только аргумент")
    old_pid = node.get("position_id")
    if not old_pid:
        raise HTTPException(400, "этот аргумент не входит ни в одну позицию")
    if node.get("dissented"):
        raise HTTPException(409, "аргумент уже вынесен в собственную позицию")

    root = node.get("topic_root_id") or await db.topic_root_of(node_id)
    # 1. spin the argument into its own verbatim position and pin it
    new_pid = await db.add_position(root, node["text"][:60], node["text"], "dissent")
    await db.set_node_position(node_id, new_pid)
    await db.mark_dissented(node_id)
    # 2. the pool it left must stop claiming the dissenter's point
    remaining = await db.position_nodes(old_pid, "argument")
    if remaining:
        try:
            comp = await asyncio.to_thread(
                pools_mod.compose_one, [m["text"] for m in remaining])
            await db.update_position(old_pid, comp.get("headline", ""),
                                     comp.get("composed", ""))
        except Exception as e:
            hub.publish({"type": "position_compose_failed",
                         "position_id": old_pid, "error": str(e)})
    else:
        await db.delete_position(old_pid)
    hub.publish({"type": "position_updated", "position_id": new_pid})
    hub.publish({"type": "position_updated", "position_id": old_pid})
    return await _position_payload(await db.get_position(new_pid))


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


# ---------------------------------------------------------------- decisions
class DecisionIn(BaseModel):
    topic_root_id: int
    question: str


class OptionIn(BaseModel):
    position_id: int | None = None
    label: str | None = None
    origin: str = "initial"           # initial | proposed | reframe


class VoteMsgIn(BaseModel):
    text: str


class VoteInformIn(BaseModel):
    accept: bool


class CastIn(BaseModel):
    option_ids: list[int]


class NavigateIn(BaseModel):
    question: str
    anchor: str | None = None


async def _decision_or_404(decision_id):
    d = await db.get_decision(decision_id)
    if d is None:
        raise HTTPException(404, f"decision {decision_id} not found")
    return d


async def _render_material(topic_root_id, question):
    m = await db.topic_material(topic_root_id)
    return material.render(question, m["positions"], m["questions"],
                           m["atoms"], m["dissents"])


@app.post("/api/decisions", dependencies=[Depends(dev_only)])
async def create_decision(body: DecisionIn, author=Depends(current_author)):
    return await db.create_decision(body.topic_root_id, body.question.strip(),
                                    created_by=author["id"])


@app.post("/api/decisions/{decision_id}/options",
          dependencies=[Depends(dev_only)])
async def add_decision_option(decision_id: int, body: OptionIn,
                              author=Depends(current_author)):
    await _decision_or_404(decision_id)
    if body.origin not in ("initial", "proposed", "reframe"):
        raise HTTPException(400, "origin must be initial|proposed|reframe")
    rev = await db.current_revision(decision_id)
    return await db.add_option(decision_id, body.position_id, body.label,
                               origin=body.origin, proposed_by=author["id"],
                               revision=rev["revision"] if rev else 1)


@app.post("/api/decisions/{decision_id}/open", dependencies=[Depends(dev_only)])
async def open_decision(decision_id: int):
    """Freeze the material and the judge model, then accept votes."""
    d = await _decision_or_404(decision_id)
    snapshot = await _render_material(d["topic_root_id"], d["question"])
    row = await db.open_decision(decision_id, snapshot,
                                 votedialogue.DEFAULT_JUDGE_MODEL)
    if row is None:
        raise HTTPException(409, "decision is not in draft")
    prefix = (material.estimate_tokens(votedialogue.INTERLOCUTOR_SYSTEM)
              + material.estimate_tokens(snapshot))
    return {**row,
            "cache": material.cache_outlook(
                prefix, votedialogue.INTERLOCUTOR_MODEL)}


@app.get("/api/decisions/{decision_id}")
async def read_decision(decision_id: int):
    d = await _decision_or_404(decision_id)
    return {"decision": d,
            "options": await db.list_options(decision_id),
            "tally": await db.tally(decision_id)}


@app.post("/api/decisions/{decision_id}/dialogue/start",
          dependencies=[Depends(llm_budget)])
async def vote_dialogue_start(decision_id: int, author=Depends(current_author)):
    d = await _decision_or_404(decision_id)
    if d["status"] != "open":
        raise HTTPException(409, "голосование не открыто")
    existing = await db.get_vote_dialogue(decision_id, author["id"])
    if existing:
        return _vote_dlg_meta(existing)
    rev = await db.current_revision(decision_id)
    try:
        opening = await asyncio.to_thread(
            votedialogue.opening_turn, rev["material_snapshot"])
    except Exception as e:
        raise HTTPException(502, f"собеседник недоступен: {e}")
    turns = []
    _ingest_vote_reply(turns, opening)
    return _vote_dlg_meta(await db.start_vote_dialogue(
        decision_id, author["id"], rev["revision"], turns))


@app.post("/api/decisions/{decision_id}/dialogue/message",
          dependencies=[Depends(llm_budget)])
async def vote_dialogue_message(decision_id: int, body: VoteMsgIn,
                                author=Depends(current_author)):
    d, dlg, rev = await _vote_dlg_or_409(decision_id, author)
    if not body.text.strip():
        raise HTTPException(400, "пустое сообщение")
    turns = dlg["transcript"]
    if turns and turns[-1].get("meta") == "inform_offer_pending":
        raise HTTPException(409, "сначала ответь на предложение информации")
    if votedialogue.user_turns(turns) >= votedialogue.MAX_TURNS:
        raise HTTPException(409, "бюджет ходов исчерпан — можно завершать")
    turns.append({"role": "user", "content": body.text.strip()})
    try:
        raw = await asyncio.to_thread(
            votedialogue.reply, rev["material_snapshot"], turns)
    except Exception as e:
        turns.pop()                  # don't persist a turn the AI never saw
        raise HTTPException(502, f"собеседник недоступен: {e}")
    _ingest_vote_reply(turns, raw)
    await db.save_vote_transcript(decision_id, author["id"], turns)
    return _vote_dlg_meta(await db.get_vote_dialogue(decision_id, author["id"]))


@app.post("/api/decisions/{decision_id}/dialogue/inform",
          dependencies=[Depends(llm_budget)])
async def vote_dialogue_inform(decision_id: int, body: VoteInformIn,
                               author=Depends(current_author)):
    d, dlg, rev = await _vote_dlg_or_409(decision_id, author)
    turns = dlg["transcript"]
    if not turns or turns[-1].get("meta") != "inform_offer_pending":
        raise HTTPException(409, "нет активного предложения информации")
    turns[-1]["meta"] = ("inform_offer_accepted" if body.accept
                         else "inform_offer_declined")
    turns.append({"role": "user",
                  "content": ("[User accepted the information offer. Provide "
                              "the information now.]" if body.accept
                              else "[User declined the information offer.]"),
                  "meta": "inform_resolution"})
    try:
        raw = await asyncio.to_thread(
            votedialogue.reply, rev["material_snapshot"], turns)
    except Exception as e:
        turns.pop()
        raise HTTPException(502, f"собеседник недоступен: {e}")
    _ingest_vote_reply(turns, raw)
    await db.save_vote_transcript(decision_id, author["id"], turns)
    return _vote_dlg_meta(await db.get_vote_dialogue(decision_id, author["id"]))


@app.post("/api/decisions/{decision_id}/dialogue/finalize",
          dependencies=[Depends(llm_budget)])
async def vote_dialogue_finalize(decision_id: int,
                                 author=Depends(current_author)):
    """Phase 2. The PERSON decides they are ready — never the interlocutor."""
    d, dlg, rev = await _vote_dlg_or_409(decision_id, author)
    turns = dlg["transcript"]
    n = votedialogue.user_turns(turns)
    if n < votedialogue.MIN_TURNS_TO_FINALIZE:
        raise HTTPException(
            409, f"нужно минимум {votedialogue.MIN_TURNS_TO_FINALIZE} ходов, "
                 f"сейчас {n}")
    try:
        total, criteria, summary, weight = await asyncio.to_thread(
            votedialogue.judge, rev["material_snapshot"], turns,
            d["judge_model"])
    except Exception as e:
        raise HTTPException(502, f"судья недоступен: {e}")
    row = await db.finish_vote_dialogue(decision_id, author["id"], total,
                                        criteria, summary, weight)
    return _vote_dlg_meta(row, full=True)


@app.post("/api/decisions/{decision_id}/vote")
async def cast_vote(decision_id: int, body: CastIn,
                    author=Depends(current_author)):
    d = await _decision_or_404(decision_id)
    if d["status"] != "open":
        raise HTTPException(409, "голосование не открыто")
    if not body.option_ids:
        raise HTTPException(400, "не выбрано ни одного варианта")
    valid = {o["id"] for o in await db.list_options(decision_id)}
    unknown = set(body.option_ids) - valid
    if unknown:
        raise HTTPException(400, f"неизвестные варианты: {sorted(unknown)}")
    # No dialogue, or an unfinished one, still votes — at weight 1. The
    # dialogue reveals, it does not gate.
    dlg = await db.get_vote_dialogue(decision_id, author["id"])
    weight = (dlg or {}).get("weight") or votedialogue.MIN_WEIGHT
    rev = (dlg or {}).get("revision") or 1
    await db.cast_vote(decision_id, author["id"], body.option_ids, weight,
                       rev, (dlg or {}).get("id"))
    return {"ok": True, "weight": weight, "revision": rev,
            "tally": await db.tally(decision_id)}


@app.post("/api/decisions/{decision_id}/navigate",
          dependencies=[Depends(llm_budget)])
async def navigate_decision(decision_id: int, body: NavigateIn):
    """The 'ask the AI' button — a navigator, never an adviser."""
    await _decision_or_404(decision_id)
    if not body.question.strip():
        raise HTTPException(400, "пустой вопрос")
    rev = await db.current_revision(decision_id)
    if rev is None:
        raise HTTPException(409, "голосование ещё не открыто")
    try:
        text = await asyncio.to_thread(
            votedialogue.navigate, rev["material_snapshot"],
            body.question.strip(), body.anchor)
    except Exception as e:
        raise HTTPException(502, f"навигатор недоступен: {e}")
    return {"answer": text.strip()}


async def _vote_dlg_or_409(decision_id, author):
    d = await _decision_or_404(decision_id)
    if d["status"] != "open":
        raise HTTPException(409, "голосование не открыто")
    dlg = await db.get_vote_dialogue(decision_id, author["id"])
    if dlg is None:
        raise HTTPException(409, "диалог не начат")
    rev = await db.current_revision(decision_id)
    return d, dlg, rev


def _ingest_vote_reply(turns, raw):
    """Split an interlocutor reply; an INFORM_OFFER becomes a pending turn."""
    before, offer = votedialogue.split_inform_offer(raw)
    if before:
        turns.append({"role": "assistant", "content": before})
    if offer:
        turns.append({"role": "assistant", "content": offer,
                      "meta": "inform_offer_pending"})


def _vote_dlg_meta(dlg, full=False):
    """
    What the client may see. The score is withheld until the dialogue is
    finalised — showing it mid-conversation would turn preparation into a game
    of maximising a number instead of understanding a question.
    """
    if dlg is None:
        raise HTTPException(404, "диалог не найден")
    out = {
        "decision_id": dlg["decision_id"],
        "revision": dlg["revision"],
        "transcript": dlg["transcript"],
        "user_turns": votedialogue.user_turns(dlg["transcript"]),
        "min_turns": votedialogue.MIN_TURNS_TO_FINALIZE,
        "max_turns": votedialogue.MAX_TURNS,
        "finished": dlg["finished_at"] is not None,
    }
    if dlg["finished_at"] is not None:
        out["weight"] = dlg["weight"]
        out["summary"] = dlg["summary"]
        if full:
            out["score"] = dlg["score"]
            out["criteria"] = dlg["criteria"]
    return out


@app.post("/api/reset", dependencies=[Depends(dev_only)])
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


def _turn_ts():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ingest_ai_reply(turns, raw):
    """Split an interlocutor reply into turns; an INFORM_OFFER becomes a
    pending offer turn the user must accept or decline."""
    ts = _turn_ts()
    m = re.search(r"\[INFORM_OFFER\]([\s\S]*?)\[/INFORM_OFFER\]", raw)
    if m:
        rest = (raw[:m.start()] + raw[m.end():]).strip()
        if rest:
            turns.append({"role": "ai", "content": rest, "ts": ts})
        turns.append({"role": "ai", "content": m.group(1).strip(),
                      "ts": ts, "meta": "inform_offer_pending"})
    else:
        turns.append({"role": "ai", "content": raw.strip(), "ts": ts})


def _dlg_meta(d):
    d["user_turns"] = _dlg_user_turns(d)
    d["min_turns"] = dialogue_mod.MIN_TURNS_TO_FINALIZE
    d["max_turns"] = dialogue_mod.MAX_TURNS
    return d


@app.get("/api/dialogue")
async def get_dialogue_state(author=Depends(current_author)):
    d = await db.get_dialogue(author["id"])
    return _dlg_meta(d) if d else None


@app.get("/api/dialogue/topics")
async def list_dialogue_topics(author=Depends(current_author)):
    return [{"id": t["id"], "title": t["title"]} for t in dialogue_mod.TOPICS]


class DialogueStartIn(BaseModel):
    topic_id: str


@app.post("/api/dialogue/start")
async def start_dialogue(body: DialogueStartIn, author=Depends(current_author)):
    title = dialogue_mod.topic_by_id(body.topic_id)
    if title is None:
        raise HTTPException(400, f"unknown topic_id {body.topic_id!r}")
    d = await db.start_dialogue(author["id"], title)
    return _dlg_meta(d)


@app.post("/api/dialogue/pre", dependencies=[Depends(llm_budget)])
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


@app.post("/api/dialogue/message", dependencies=[Depends(llm_budget)])
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
    turns.append({"role": "user", "content": body.text.strip(),
                  "ts": _turn_ts()})
    try:
        reply = await asyncio.to_thread(
            dialogue_mod.interlocutor_reply, d["topic"], d["pre"], turns)
    except Exception as e:
        turns.pop()                       # don't persist a turn the AI never saw
        raise HTTPException(502, f"собеседник недоступен: {e}")
    _ingest_ai_reply(turns, reply)
    await db.update_dialogue(author["id"], turns=turns)
    return _dlg_meta(await db.get_dialogue(author["id"]))


@app.post("/api/dialogue/inform", dependencies=[Depends(llm_budget)])
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
                  if body.accept else "[User declined the information offer.]",
                  "ts": _turn_ts()})
    if body.accept:
        try:
            reply = await asyncio.to_thread(
                dialogue_mod.interlocutor_reply, d["topic"], d["pre"], turns)
        except Exception as e:
            raise HTTPException(502, f"собеседник недоступен: {e}")
        _ingest_ai_reply(turns, reply)
    await db.update_dialogue(author["id"], turns=turns)
    return _dlg_meta(await db.get_dialogue(author["id"]))


@app.post("/api/dialogue/finalize", dependencies=[Depends(llm_budget)])
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


# ---- handing out accounts: invite codes and per-tester API keys. Behind
# ADMIN_TOKEN rather than DEV_TOOLS, because these are needed on the live
# instance while the dev tools stay off.
class InviteIn(BaseModel):
    note: str | None = None       # who it is for, so a spent code is traceable
    count: int = 1


@app.post("/api/dev/invites", dependencies=[Depends(admin_only)])
async def dev_mint_invites(body: InviteIn):
    if not 1 <= body.count <= 200:
        raise HTTPException(400, "count: 1..200")
    codes = []
    for _ in range(body.count):
        code = secrets.token_urlsafe(9)
        if await db.add_invite(code, body.note):
            codes.append(code)
    return {"codes": codes}


@app.get("/api/dev/invites", dependencies=[Depends(admin_only)])
async def dev_list_invites():
    return await db.list_invites()


class BalanceIn(BaseModel):
    balance_usd: float


@app.put("/api/dev/authors/{author_id}/balance",
         dependencies=[Depends(admin_only)])
async def dev_set_balance(author_id: int, body: BalanceIn):
    if not 0 <= body.balance_usd <= 1000:
        raise HTTPException(400, "balance_usd: 0..1000")
    if not await db.set_balance(author_id, body.balance_usd):
        raise HTTPException(404, "нет такого автора")
    return {"ok": True, "author_id": author_id, "balance_usd": body.balance_usd}


class ApiKeyIn(BaseModel):
    api_key: str


@app.put("/api/dev/authors/{author_id}/api_key",
         dependencies=[Depends(admin_only)])
async def dev_set_api_key(author_id: int, body: ApiKeyIn):
    key = body.api_key.strip()
    if not key:
        raise HTTPException(400, "пустой ключ")
    if not await db.set_api_key(author_id, key):
        raise HTTPException(404, "нет такого автора")
    return {"ok": True, "author_id": author_id}


@app.get("/api/dev/dialogues", dependencies=[Depends(admin_only)])
async def dev_list_dialogues():
    return await db.list_dialogues()


@app.get("/api/dev/dialogues/{author_id}", dependencies=[Depends(admin_only)])
async def dev_get_dialogue(author_id: int):
    d = await db.get_dialogue(author_id)
    if d is None:
        raise HTTPException(404, "у этого автора нет диалога")
    return _dlg_meta(d)


# ---------------------------------------------------------------- AI navigator
# ONE navigator (vault: ai-navigator-draft-review). `precheck` is a THIN VIEW of
# the same `review_draft` pass — a draft compared against a topic's POSITIONS —
# used by the in-topic position actions (oppose/question), where all we need is
# the overlap verdict. The reply/new-topic forms use /draft/review for the full
# pass. Previously each had its own LLM prompt (pools.precheck_draft); they now
# share the single review prompt. A suggestion, never a block: fail-open.
class PrecheckIn(BaseModel):
    text: str


_PRECHECK_NEW = {"verdict": "new", "position_id": None, "note": ""}


@app.post("/api/topics/{topic_root_id}/precheck",
          dependencies=[Depends(llm_budget)])
async def precheck_draft(topic_root_id: int, body: PrecheckIn,
                         author=Depends(current_author)):
    if not body.text.strip():
        return _PRECHECK_NEW
    positions = await db.list_positions(topic_root_id)
    if not positions:
        return _PRECHECK_NEW              # nothing to compare against yet
    try:
        # the single navigator, reduced to the position-overlap question
        # (no parent node / branch: this is a draft vs the topic's positions)
        result = await asyncio.to_thread(
            pools_mod.review_draft, body.text, None, [],
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


# Draft review (vault: ai-navigator-draft-review): the navigator's full
# pre-publication pass — type fit, one quality suggestion, and overlap with the
# topic's NODES (answered/countered) and POSITIONS (similar/covered). Replaces
# precheck in the reply/new-topic forms; a suggestion, never a block — fail-open
# on any error, and every id the LLM names is validated server-side.
class DraftReviewIn(BaseModel):
    text: str
    connect_to: int | None = None         # reply target (None = new topic root)
    edge_type: str | None = None          # reply type: support/refute/qualify/question
    kind: str | None = None               # root type: argument/question


_REVIEW_CLEAN = {
    "type_ok": True, "suggested_type": None, "type_note": "",
    "quality_note": "", "verdict": "new", "node_id": None,
    "position_id": None, "headline": "", "target_text": "", "note": "",
    "split": None,
}
_REVIEW_TYPES = {"support", "refute", "qualify", "question",
                 "proposal", "exploration"}
_ROOT_KINDS = {"argument", "question", "proposal", "exploration"}
_SPLIT_TYPES = {"support", "refute", "qualify", "question", "proposal"}

# The LLM classifies the draft's actual_type from the TEXT alone (vault:
# ai-navigator-type-loop — feeding the author's declared type into the prompt
# made the classification itself depend on it, so switching to the suggested
# type could flip the verdict back the other way, an oscillating loop with no
# way to land). Caching keyed on (connect_to, text) means flipping the type
# dropdown and resending the same draft compares locally against the same
# cached actual_type instead of re-asking the LLM — the comparison is what
# changes, not the classification.
_REVIEW_CACHE: dict[tuple, tuple[float, dict]] = {}
_REVIEW_CACHE_TTL = 300
_REVIEW_CACHE_MAX = 500


def _review_cache_get(key):
    hit = _REVIEW_CACHE.get(key)
    if hit is None:
        return None
    ts, result = hit
    if time.monotonic() - ts > _REVIEW_CACHE_TTL:
        _REVIEW_CACHE.pop(key, None)
        return None
    return result


def _review_cache_set(key, result):
    if len(_REVIEW_CACHE) >= _REVIEW_CACHE_MAX:
        oldest = min(_REVIEW_CACHE, key=lambda k: _REVIEW_CACHE[k][0])
        _REVIEW_CACHE.pop(oldest, None)
    _REVIEW_CACHE[key] = (time.monotonic(), result)


@app.post("/api/draft/review")
async def review_draft(body: DraftReviewIn, author=Depends(current_author)):
    text = body.text.strip()
    if not text:
        return _REVIEW_CLEAN
    parent, branch, positions = None, [], []
    if body.connect_to is not None:
        parent = await db.get_node(body.connect_to)
        if parent is None:
            raise HTTPException(404, f"connect_to node {body.connect_to} not found")
        root_id = parent.get("topic_root_id") or await db.topic_root_of(body.connect_to)
        branch = await db.topic_subtree(root_id)
        positions = await db.list_positions(root_id)
        declared = body.edge_type or "support"
    else:
        declared = body.kind or "argument"
    cache_key = (body.connect_to, text)
    result = _review_cache_get(cache_key)
    if result is None:
        # budget is charged only on a real LLM call — cache hits (the author
        # flipping the type dropdown on the same draft) stay free. Outside the
        # try: an exhausted grant must reach the tester as 402, not vanish
        # into the fail-open below and leave the navigator silently dead.
        await spend_llm(author)
        try:
            result = await asyncio.to_thread(
                pools_mod.review_draft, text, parent, branch,
                [{"id": p["id"], "headline": p["headline"], "composed": p["composed"]}
                 for p in positions])
        except Exception:
            return dict(_REVIEW_CLEAN)    # fail-open: never stand in the way
        _review_cache_set(cache_key, result)
    out = dict(_REVIEW_CLEAN)
    actual = result.get("actual_type")
    # a topic root has kinds, not reply types — fold the reply types back
    if body.connect_to is None and actual in _REVIEW_TYPES:
        actual = actual if actual in _ROOT_KINDS else "argument"
    if actual in (_REVIEW_TYPES | _ROOT_KINDS) and actual != declared:
        out.update(type_ok=False, suggested_type=actual,
                   type_note=str(result.get("type_note") or ""))
    out["quality_note"] = str(result.get("quality_note") or "")
    # split: exactly two non-empty parts of different reply types, replies only
    sp = result.get("split")
    if body.connect_to is not None and isinstance(sp, list) and len(sp) == 2:
        parts = [{"type": p.get("type"), "text": str(p.get("text") or "").strip()}
                 for p in sp if isinstance(p, dict)]
        if (len(parts) == 2 and all(p["type"] in _SPLIT_TYPES and p["text"] for p in parts)
                and parts[0]["type"] != parts[1]["type"]):
            out["split"] = parts
    verdict = result.get("verdict")
    nid, pid = result.get("node_id"), result.get("position_id")
    nodes = {n["id"]: n for n in branch}
    known = {p["id"]: p for p in positions}
    # the parent itself is what the draft replies to — pointing at it is noise
    if verdict in ("answered", "countered") and nid in nodes and nid != body.connect_to:
        out.update(verdict=verdict, node_id=nid,
                   target_text=nodes[nid]["text"],
                   note=str(result.get("note") or ""))
    elif verdict in ("similar", "covered") and pid in known:
        out.update(verdict=verdict, position_id=pid,
                   headline=known[pid]["headline"],
                   note=str(result.get("note") or ""))
    return out


# Atomization (vault: exploration-atomization): an EXPLORATION is cut — with
# the author's consent — into typed atoms grouped by theme, so the community
# can engage each point separately. Two steps: /atomize proposes a preview
# (nothing written), the author edits it client-side, /atomize/confirm
# materializes the approved atoms. Atoms carry NO LLM base score — the
# exploration was scored as a whole and the accrual formula averages rather
# than sums, so unscored atoms neither farm nor drag PoI; they live on
# community reactions. atom_group marks them as points under investigation,
# not positions the author has taken.
class AtomIn(BaseModel):
    type: str                              # argument / question / detail / proposal
    text: str
    group: str | None = None


class AtomizeConfirmIn(BaseModel):
    atoms: list[AtomIn]


_ATOM_KINDS = {"argument", "question", "detail", "proposal"}


async def _own_exploration_or_403(node_id: int, author):
    node = await db.get_node(node_id)
    if node is None:
        raise HTTPException(404, f"node {node_id} not found")
    if node.get("kind") != "exploration":
        raise HTTPException(400, "атомизация применима только к исследованию")
    if node.get("author_id") != author["id"]:
        raise HTTPException(403, "атомизировать разбор может только его автор")
    return node


@app.post("/api/nodes/{node_id}/atomize", dependencies=[Depends(llm_budget)])
async def atomize_preview(node_id: int, author=Depends(current_author)):
    node = await _own_exploration_or_403(node_id, author)
    # already atomized -> don't spend a (paid) LLM call proposing a second cut
    # the confirm would refuse to materialize anyway (idempotent)
    if await db.atom_children(node_id):
        return {"groups": [], "already_atomized": True}
    try:
        result = await asyncio.to_thread(pools_mod.atomize, node["text"])
    except Exception as e:
        raise HTTPException(502, f"атомизация не удалась: {e}")
    groups = []
    for g in (result.get("groups") or []):
        atoms = [{"type": a.get("type"), "text": str(a.get("text") or "").strip()}
                 for a in (g.get("atoms") or []) if isinstance(a, dict)]
        atoms = [a for a in atoms if a["type"] in _ATOM_KINDS and a["text"]]
        if atoms:
            groups.append({"title": str(g.get("title") or "").strip() or "разбор",
                           "atoms": atoms})
    return {"groups": groups}


@app.post("/api/nodes/{node_id}/atomize/confirm")
async def atomize_confirm(node_id: int, body: AtomizeConfirmIn,
                          author=Depends(current_author)):
    node = await _own_exploration_or_403(node_id, author)
    if not (1 <= len(body.atoms) <= 24):
        raise HTTPException(400, "нужно от 1 до 24 атомов")
    for a in body.atoms:
        if a.type not in _ATOM_KINDS or not a.text.strip():
            raise HTTPException(400, "каждый атом: непустой текст и тип "
                                     "argument/question/detail/proposal")
    root_id = node.get("topic_root_id") or await db.topic_root_of(node_id)
    atoms = [{"text": a.text.strip(), "kind": a.type,
              "group": (a.group or "разбор").strip() or "разбор"}
             for a in body.atoms]
    # idempotent: a repeat confirm (double-click / retry) returns the atoms
    # already created instead of duplicating them; all writes are one transaction
    created, already = await db.add_atoms_once(node_id, root_id, author["id"], atoms)
    if not already:
        hub.publish({"type": "atoms_created", "node_id": node_id,
                     "count": len(created)})
    return {"created": created, "already_atomized": already}


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
#
# graph.html and dialogue-admin.html are the solo test bench: they carry an
# author dropdown, "new persona" and "reseed the graph". The server rejects all
# of that with 403 when DEV_TOOLS is off, but a tester who opens the page still
# sees admin controls and a screen full of failures. Routes declared before the
# mount win, so these two are gated here rather than in the pages themselves.
static_dir = Path(__file__).parent.parent / "static"


def _dev_page(name: str):
    """Serve an admin-only page when DEV_TOOLS is on; 404 otherwise.

    Declared as an explicit route, NOT a catch-all `/{page}`: a catch-all
    would shadow the mount below and break every other root-level asset
    (app.js, tree.js, index.html).

    Only dialogue-admin.html lives here — it shows testers' full transcripts.
    graph.html stays open to everyone (it is a real view of the graph, and the
    header links to it); its test-only controls are hidden client-side instead,
    driven by /api/config.
    """
    async def handler():
        if not DEV_TOOLS:
            raise HTTPException(404)
        return FileResponse(static_dir / name)
    return handler


app.get("/dialogue-admin.html", include_in_schema=False)(
    _dev_page("dialogue-admin.html"))

if static_dir.exists():
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
