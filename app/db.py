"""
Async Postgres storage for the Noosphere PoC.

Typed argument graph: nodes are claims, edges are typed relations
(support / refute / qualify / question). Positions (pools) sit on top as
persistent Layer-2 objects.

Why Postgres + async: many users must be able to WRITE concurrently. SQLite
serialises writers behind one lock; Postgres + a connection pool lets writers
proceed in parallel, and `async` keeps a slow LLM scoring call from blocking
every other request on the single event loop.

All access goes through a shared asyncpg pool created at startup.
"""

import os
import json

import asyncpg

from . import poiformula

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://noosphere:noosphere@localhost/noosphere"
)

EDGE_TYPES = ("support", "refute", "qualify", "question")

_pool: asyncpg.Pool | None = None


async def init_pool():
    """Create the shared connection pool (idempotent)."""
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=10)
    return _pool


async def close_pool():
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def _pool_or_raise() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialised — call init_pool() at startup")
    return _pool


# Tables in dependency order: Postgres, unlike SQLite, requires a referenced
# table to already exist. Fresh schema — no in-place migrations needed here.
_SCHEMA = [
    # An account IS an author with login credentials. Seeded test personas have
    # username = NULL and cannot log in; registered users can.
    """
    CREATE TABLE IF NOT EXISTS authors (
        id            SERIAL PRIMARY KEY,
        name          TEXT NOT NULL,
        reputation    DOUBLE PRECISION NOT NULL DEFAULT 50,
        color         TEXT,
        username      TEXT UNIQUE,
        password_hash TEXT,
        created_at    TIMESTAMPTZ DEFAULT now()
    )
    """,
    # migrations for a database created before accounts existed
    "ALTER TABLE authors ADD COLUMN IF NOT EXISTS username TEXT UNIQUE",
    "ALTER TABLE authors ADD COLUMN IF NOT EXISTS password_hash TEXT",
    # the author's PoI-dialogue score: the default P₀ for topics without an
    # explicit per-topic prior (vault: poi-accrual-onboarding)
    "ALTER TABLE authors ADD COLUMN IF NOT EXISTS dialogue_poi DOUBLE PRECISION",
    # ONE resumable PoI dialogue per author (no retakes — it accumulates).
    """
    CREATE TABLE IF NOT EXISTS dialogues (
        author_id  INTEGER PRIMARY KEY REFERENCES authors(id) ON DELETE CASCADE,
        topic      TEXT NOT NULL,
        phase      TEXT NOT NULL DEFAULT 'pre',   -- pre|dialogue|post|results
        pre        JSONB,
        post       JSONB,
        turns      JSONB NOT NULL DEFAULT '[]',
        scores     JSONB,
        updated_at TIMESTAMPTZ DEFAULT now()
    )
    """,
    # Opaque session tokens (HTTP-only cookie holds the token, nothing else).
    """
    CREATE TABLE IF NOT EXISTS sessions (
        token      TEXT PRIMARY KEY,
        author_id  INTEGER NOT NULL REFERENCES authors(id) ON DELETE CASCADE,
        created_at TIMESTAMPTZ DEFAULT now(),
        expires_at TIMESTAMPTZ NOT NULL
    )
    """,
    # topic_root_id: which discussion the node belongs to, materialized at
    # insert (a root points to itself). Makes "the author's contributions in
    # this topic" an index lookup — needed by the PoI accrual recompute and,
    # per the scaling draft, the future shard key.
    """
    CREATE TABLE IF NOT EXISTS nodes (
        id            SERIAL PRIMARY KEY,
        text          TEXT NOT NULL,
        poi_score     DOUBLE PRECISION,
        poi_breakdown TEXT,
        author_id     INTEGER REFERENCES authors(id) ON DELETE SET NULL,
        kind          TEXT DEFAULT 'argument',
        position_id   INTEGER,
        topic_root_id INTEGER,
        created_at    TIMESTAMPTZ DEFAULT now()
    )
    """,
    "ALTER TABLE nodes ADD COLUMN IF NOT EXISTS topic_root_id INTEGER",
    """
    CREATE TABLE IF NOT EXISTS edges (
        id        SERIAL PRIMARY KEY,
        source_id INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
        target_id INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
        type      TEXT NOT NULL CHECK (type IN ('support','refute','qualify','question'))
    )
    """,
    # poi = the CURRENT computed value (poiformula.topic_poi over the event-
    # logged history). prior = P₀: onboarding-dialogue score / seeded test
    # value; NULL means the default prior (10).
    """
    CREATE TABLE IF NOT EXISTS author_topic_poi (
        author_id     INTEGER NOT NULL REFERENCES authors(id) ON DELETE CASCADE,
        topic_root_id INTEGER NOT NULL,
        poi           DOUBLE PRECISION NOT NULL DEFAULT 10,
        prior         DOUBLE PRECISION,
        PRIMARY KEY (author_id, topic_root_id)
    )
    """,
    "ALTER TABLE author_topic_poi ADD COLUMN IF NOT EXISTS prior DOUBLE PRECISION",
    """
    CREATE TABLE IF NOT EXISTS reactions (
        author_id  INTEGER NOT NULL REFERENCES authors(id) ON DELETE CASCADE,
        node_id    INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
        stance     TEXT NOT NULL CHECK (stance IN ('agree','disagree')),
        created_at TIMESTAMPTZ DEFAULT now(),
        PRIMARY KEY (author_id, node_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS positions (
        id            SERIAL PRIMARY KEY,
        topic_root_id INTEGER NOT NULL,
        headline      TEXT,
        composed      TEXT,
        stance        TEXT,
        created_at    TIMESTAMPTZ DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS position_links (
        from_position INTEGER NOT NULL REFERENCES positions(id) ON DELETE CASCADE,
        to_position   INTEGER NOT NULL REFERENCES positions(id) ON DELETE CASCADE,
        type          TEXT NOT NULL,
        PRIMARY KEY (from_position, to_position)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS position_reactions (
        author_id   INTEGER NOT NULL REFERENCES authors(id) ON DELETE CASCADE,
        position_id INTEGER NOT NULL REFERENCES positions(id) ON DELETE CASCADE,
        stance      TEXT NOT NULL CHECK (stance IN ('agree','disagree')),
        PRIMARY KEY (author_id, position_id)
    )
    """,
    # APPEND-ONLY event log (scaling draft, principle 1). Every write lands here
    # as an immutable event, in the SAME transaction as the state change. This
    # is (a) the audit layer's substrate — every argument and vote visible
    # piece by piece — and (b) what makes the vote-weight formula replayable:
    # when the formula changes, history is re-run from events, not migrated.
    # No update/delete path exists in code; rows are only ever inserted.
    """
    CREATE TABLE IF NOT EXISTS events (
        id        BIGSERIAL PRIMARY KEY,
        ts        TIMESTAMPTZ NOT NULL DEFAULT now(),
        type      TEXT NOT NULL,
        author_id INTEGER,
        payload   JSONB NOT NULL DEFAULT '{}'::jsonb
    )
    """,
]


async def _log(conn, type_, payload, author_id=None):
    """Append one immutable event (call inside the write's transaction)."""
    await conn.execute(
        "INSERT INTO events (type, author_id, payload) VALUES ($1, $2, $3)",
        type_, author_id, json.dumps(payload))


# The read contract is "a node + a RANKED PAGE of its children" — the client
# never loads the whole graph (scaling draft, principle 3). These indexes are
# what make that query an index lookup instead of a scan.
_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_id)",
    "CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id)",
    "CREATE INDEX IF NOT EXISTS idx_nodes_position ON nodes(position_id)",
]


async def init_db():
    pool = await init_pool()
    async with pool.acquire() as conn:
        for stmt in _SCHEMA:
            await conn.execute(stmt)
        for stmt in _INDEXES:
            await conn.execute(stmt)
        # backfill topic_root_id for rows that predate the column (walk up once)
        orphans = [r["id"] for r in await conn.fetch(
            "SELECT id FROM nodes WHERE topic_root_id IS NULL")]
    for nid in orphans:
        root = await topic_root_of(nid)
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE nodes SET topic_root_id = $1 WHERE id = $2", root, nid)


async def wipe():
    """Delete all rows (clean slate between test/seed runs)."""
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE position_reactions, position_links, positions, "
            "author_topic_poi, reactions, edges, nodes, sessions, dialogues, "
            "authors, events RESTART IDENTITY CASCADE"
        )


# ---------------------------------------------------------------- nodes
async def add_node(text, poi_score=None, poi_breakdown=None, author_id=None,
                   kind="argument", position_id=None, topic_root_id=None):
    """topic_root_id=None means the node IS a new topic root (points to itself)."""
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        async with conn.transaction():
            node_id = await conn.fetchval(
                "INSERT INTO nodes (text, poi_score, poi_breakdown, author_id, kind, "
                "position_id, topic_root_id) VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING id",
                text, poi_score,
                json.dumps(poi_breakdown) if poi_breakdown else None,
                author_id, kind, position_id, topic_root_id,
            )
            if topic_root_id is None:
                await conn.execute(
                    "UPDATE nodes SET topic_root_id = id WHERE id = $1", node_id)
            await _log(conn, "node_added",
                       {"node_id": node_id, "text": text, "kind": kind,
                        "position_id": position_id, "poi_score": poi_score,
                        "topic_root_id": topic_root_id or node_id},
                       author_id)
    return node_id


async def set_node_position(node_id, position_id):
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "UPDATE nodes SET position_id = $1 WHERE id = $2", position_id, node_id)
            await _log(conn, "node_position_set",
                       {"node_id": node_id, "position_id": position_id})


async def update_node_score(node_id, poi_score, poi_breakdown):
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "UPDATE nodes SET poi_score = $1, poi_breakdown = $2 WHERE id = $3",
                poi_score, json.dumps(poi_breakdown), node_id)
            await _log(conn, "node_scored",
                       {"node_id": node_id, "poi_score": poi_score,
                        "breakdown": poi_breakdown})


async def get_node(node_id):
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM nodes WHERE id = $1", node_id)
    return dict(row) if row else None


async def get_node_full(node_id):
    """One node with author info and parsed breakdown — the detail-panel read."""
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT n.*, a.name AS author, a.color AS author_color,
                   (SELECT count(*) FROM edges e WHERE e.target_id = n.id) AS reply_count
            FROM nodes n
            LEFT JOIN authors a ON a.id = n.author_id
            WHERE n.id = $1
            """, node_id)
    if row is None:
        return None
    d = dict(row)
    if d.get("poi_breakdown"):
        d["poi_breakdown"] = json.loads(d["poi_breakdown"])
    return d


async def get_children(node_id, limit=20, offset=0):
    """
    A RANKED PAGE of a node's children (replies), ordered by the child's own
    PoI. This — not get_graph — is the read path the tree UI uses: the client
    only ever asks for the slice it is looking at.
    """
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        total = await conn.fetchval(
            "SELECT count(*) FROM edges WHERE target_id = $1", node_id)
        rows = await conn.fetch(
            """
            SELECT n.id, n.text, n.poi_score, n.kind, e.type AS rel,
                   a.name AS author, a.color AS author_color,
                   (SELECT count(*) FROM edges e2 WHERE e2.target_id = n.id) AS reply_count
            FROM edges e
            JOIN nodes n ON n.id = e.source_id
            LEFT JOIN authors a ON a.id = n.author_id
            WHERE e.target_id = $1
            ORDER BY n.poi_score DESC NULLS LAST, n.id
            LIMIT $2 OFFSET $3
            """, node_id, limit, offset)
    return {"total": total, "children": [dict(r) for r in rows]}


# ---------------------------------------------------------------- authors
# public author shape — password_hash never leaves the DB layer
_AUTHOR_COLS = "id, name, reputation, color, username, created_at, dialogue_poi"


async def list_authors():
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        rows = await conn.fetch(f"SELECT {_AUTHOR_COLS} FROM authors ORDER BY id")
    return [dict(r) for r in rows]


async def add_author(name, reputation=50, color=None):
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        async with conn.transaction():
            author_id = await conn.fetchval(
                "INSERT INTO authors (name, reputation, color) VALUES ($1, $2, $3) RETURNING id",
                name, reputation, color)
            await _log(conn, "author_added",
                       {"name": name, "reputation": reputation}, author_id)
    return author_id


async def get_author(author_id):
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT {_AUTHOR_COLS} FROM authors WHERE id = $1", author_id)
    return dict(row) if row else None


async def update_author_reputation(author_id, reputation):
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        async with conn.transaction():
            result = await conn.execute(
                "UPDATE authors SET reputation = $1 WHERE id = $2", reputation, author_id)
            changed = result.split()[-1] != "0"   # asyncpg returns e.g. "UPDATE 1"
            if changed:
                await _log(conn, "author_reputation_set",
                           {"reputation": reputation}, author_id)
    return changed


# ---------------------------------------------------------------- accounts
async def add_user(username, password_hash, name, color=None):
    """Register: an account is an author with credentials. None if taken."""
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        async with conn.transaction():
            author_id = await conn.fetchval(
                """
                INSERT INTO authors (name, color, username, password_hash)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (username) DO NOTHING
                RETURNING id
                """, name, color, username, password_hash)
            if author_id is not None:
                await _log(conn, "author_added",
                           {"name": name, "username": username, "reputation": 50},
                           author_id)
    return author_id


async def get_author_by_username(username):
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM authors WHERE username = $1", username)
    return dict(row) if row else None


async def create_session(token, author_id, days=30):
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO sessions (token, author_id, expires_at) "
            "VALUES ($1, $2, now() + make_interval(days => $3))",
            token, author_id, days)


async def session_author(token):
    """The author behind a live session token, or None (expired ones purged)."""
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM sessions WHERE expires_at < now()")
        row = await conn.fetchrow(
            f"""
            SELECT {', '.join('a.' + c.strip() for c in _AUTHOR_COLS.split(','))}
            FROM sessions s
            JOIN authors a ON a.id = s.author_id
            WHERE s.token = $1 AND s.expires_at >= now()
            """, token)
    return dict(row) if row else None


async def delete_session(token):
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM sessions WHERE token = $1", token)


# ---------------------------------------------------------------- PoI dialogue
def _parse_dialogue(row):
    if row is None:
        return None
    d = dict(row)
    for f in ("pre", "post", "turns", "scores"):
        if isinstance(d.get(f), str):
            d[f] = json.loads(d[f])
    if d.get("updated_at") is not None:
        d["updated_at"] = d["updated_at"].isoformat()
    return d


async def get_dialogue(author_id):
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM dialogues WHERE author_id = $1", author_id)
    return _parse_dialogue(row)


async def start_dialogue(author_id, topic):
    """Create the author's dialogue if it doesn't exist yet (one per author)."""
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                INSERT INTO dialogues (author_id, topic)
                VALUES ($1, $2)
                ON CONFLICT (author_id) DO NOTHING
                RETURNING *
                """, author_id, topic)
            if row is not None:
                await _log(conn, "dialogue_started", {"topic": topic}, author_id)
    if row is None:
        return await get_dialogue(author_id)
    return _parse_dialogue(row)


async def update_dialogue(author_id, **fields):
    """Update phase/pre/post/turns/scores; JSON fields are serialized here."""
    allowed = {"phase", "pre", "post", "turns", "scores"}
    sets, vals = [], []
    for key, val in fields.items():
        if key not in allowed:
            raise ValueError(f"unknown dialogue field {key}")
        if key != "phase":
            val = json.dumps(val)
        vals.append(val)
        sets.append(f"{key} = ${len(vals)}")
    vals.append(author_id)
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        await conn.execute(
            f"UPDATE dialogues SET {', '.join(sets)}, updated_at = now() "
            f"WHERE author_id = ${len(vals)}", *vals)


async def set_dialogue_poi(author_id, value):
    """Store the dialogue score — the author's default prior (P₀)."""
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "UPDATE authors SET dialogue_poi = $1 WHERE id = $2", value, author_id)
            await _log(conn, "dialogue_poi_set", {"dialogue_poi": value}, author_id)


async def author_topic_roots(author_id):
    """Topics the author has state in (contributions or a PoI row)."""
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT topic_root_id FROM (
                SELECT topic_root_id FROM nodes
                WHERE author_id = $1 AND topic_root_id IS NOT NULL
                UNION
                SELECT topic_root_id FROM author_topic_poi WHERE author_id = $1
            ) t
            """, author_id)
    return [r["topic_root_id"] for r in rows]


# ---------------------------------------------------------------- per-topic PoI
async def set_topic_prior(author_id, topic_root_id, prior):
    """
    Set P₀ — the prior: a seeded test value now, the onboarding-dialogue score
    later. The CURRENT poi is then recomputed from the formula, so a prior set
    late applies retroactively (decision 2026-07-03).
    """
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO author_topic_poi (author_id, topic_root_id, poi, prior)
                VALUES ($1, $2, $3, $3)
                ON CONFLICT (author_id, topic_root_id) DO UPDATE SET prior = EXCLUDED.prior
                """, author_id, topic_root_id, prior)
            await _log(conn, "topic_prior_set",
                       {"topic_root_id": topic_root_id, "prior": prior}, author_id)
    return await recompute_topic_poi(author_id, topic_root_id)


async def recompute_topic_poi(author_id, topic_root_id):
    """
    Recompute the author's CURRENT topic PoI from first principles
    (poiformula over prior + scored contributions + received reactions).
    Called after a contribution is scored and after a reaction lands.
    """
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        # prior precedence: explicit per-topic prior > dialogue score > default
        prior = await conn.fetchval(
            """
            SELECT COALESCE(tp.prior, a.dialogue_poi)
            FROM authors a
            LEFT JOIN author_topic_poi tp
                   ON tp.author_id = a.id AND tp.topic_root_id = $2
            WHERE a.id = $1
            """, author_id, topic_root_id)
        contribs = await conn.fetch(
            "SELECT poi_score, kind FROM nodes "
            "WHERE author_id = $1 AND topic_root_id = $2 AND poi_score IS NOT NULL",
            author_id, topic_root_id)
        # reactions on the author's nodes in this topic, self-reactions excluded,
        # each weighted by the REACTOR's current topic PoI (default prior if none)
        reacts = await conn.fetch(
            """
            SELECT COALESCE(tp.poi, ra.dialogue_poi, 10) AS reactor_poi, r.stance
            FROM reactions r
            JOIN nodes n ON n.id = r.node_id
            JOIN authors ra ON ra.id = r.author_id
            LEFT JOIN author_topic_poi tp
                   ON tp.author_id = r.author_id AND tp.topic_root_id = $2
            WHERE n.author_id = $1 AND n.topic_root_id = $2 AND r.author_id <> $1
            """, author_id, topic_root_id)
        value = poiformula.topic_poi(
            prior,
            [(r["poi_score"], r["kind"]) for r in contribs],
            [(r["reactor_poi"], r["stance"]) for r in reacts])
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO author_topic_poi (author_id, topic_root_id, poi)
                VALUES ($1, $2, $3)
                ON CONFLICT (author_id, topic_root_id) DO UPDATE SET poi = EXCLUDED.poi
                """, author_id, topic_root_id, value)
            await _log(conn, "topic_poi_set",
                       {"topic_root_id": topic_root_id, "poi": value,
                        "computed": True}, author_id)
    return value


async def get_topic_poi(topic_root_id):
    """Every author's CURRENT PoI in the topic (no row -> default prior 10)."""
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT a.id AS author_id, a.name, a.color,
                   COALESCE(tp.poi, a.dialogue_poi, 10) AS poi,
                   COALESCE(tp.prior, a.dialogue_poi) AS prior
            FROM authors a
            LEFT JOIN author_topic_poi tp
                   ON tp.author_id = a.id AND tp.topic_root_id = $1
            ORDER BY a.id
            """, topic_root_id)
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- reactions
async def set_reaction(author_id, node_id, stance):
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO reactions (author_id, node_id, stance)
                VALUES ($1, $2, $3)
                ON CONFLICT (author_id, node_id) DO UPDATE SET stance = EXCLUDED.stance
                """, author_id, node_id, stance)
            # the log keeps EVERY vote change; the table keeps only the latest
            await _log(conn, "reaction_set",
                       {"node_id": node_id, "stance": stance}, author_id)


async def get_reactions(node_id, topic_root_id):
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT r.author_id, a.name, a.color, r.stance,
                   COALESCE(tp.poi, 10) AS poi
            FROM reactions r
            JOIN authors a ON a.id = r.author_id
            LEFT JOIN author_topic_poi tp
                   ON tp.author_id = r.author_id AND tp.topic_root_id = $1
            WHERE r.node_id = $2
            ORDER BY r.author_id
            """, topic_root_id, node_id)
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- positions
async def clear_positions(topic_root_id):
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "UPDATE nodes SET position_id = NULL WHERE position_id IN "
                "(SELECT id FROM positions WHERE topic_root_id = $1)", topic_root_id)
            await conn.execute(
                "DELETE FROM positions WHERE topic_root_id = $1", topic_root_id)
            await _log(conn, "positions_cleared", {"topic_root_id": topic_root_id})


async def add_position(topic_root_id, headline, composed, stance):
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        async with conn.transaction():
            pid = await conn.fetchval(
                "INSERT INTO positions (topic_root_id, headline, composed, stance) "
                "VALUES ($1, $2, $3, $4) RETURNING id",
                topic_root_id, headline, composed, stance)
            await _log(conn, "position_added",
                       {"position_id": pid, "topic_root_id": topic_root_id,
                        "headline": headline, "stance": stance})
    return pid


async def get_position(position_id):
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM positions WHERE id = $1", position_id)
    return dict(row) if row else None


async def list_positions(topic_root_id):
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM positions WHERE topic_root_id = $1 ORDER BY id", topic_root_id)
    return [dict(r) for r in rows]


async def update_position(position_id, headline, composed):
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "UPDATE positions SET headline = $1, composed = $2 WHERE id = $3",
                headline, composed, position_id)
            await _log(conn, "position_updated",
                       {"position_id": position_id, "headline": headline})


async def position_nodes(position_id, kind="argument"):
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM nodes WHERE position_id = $1 AND kind = $2 ORDER BY id",
            position_id, kind)
    out = []
    for r in rows:
        d = dict(r)
        if d.get("poi_breakdown"):
            d["poi_breakdown"] = json.loads(d["poi_breakdown"])
        out.append(d)
    return out


async def position_planets(position_id):
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM nodes WHERE position_id = $1 AND kind IN ('question','detail') "
            "ORDER BY id", position_id)
    return [dict(r) for r in rows]


async def topic_root_of(node_id):
    """Walk up reply edges (source -> target) to the discussion's root node."""
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            """
            WITH RECURSIVE up AS (
                SELECT $1::int AS id, 0 AS depth
                UNION ALL
                SELECT e.target_id, up.depth + 1
                FROM up JOIN edges e ON e.source_id = up.id
                WHERE up.depth < 100
            )
            SELECT id FROM up ORDER BY depth DESC LIMIT 1
            """, node_id)


async def topic_subtree(topic_root_id, limit=80):
    """
    The whole discussion under a root — the AI navigator's reading context for
    the pre-publication draft review. Bounded and breadth-first (shallow nodes
    first): at PoC scale the LLM reads the full topic; embeddings-based
    candidate selection replaces this cap when topics outgrow it.
    """
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            WITH RECURSIVE down AS (
                SELECT n.id, n.text, n.kind,
                       NULL::int AS parent_id, NULL::text AS rel, 0 AS depth
                FROM nodes n WHERE n.id = $1
                UNION ALL
                SELECT n.id, n.text, n.kind,
                       e.target_id, e.type, down.depth + 1
                FROM down
                JOIN edges e ON e.target_id = down.id
                JOIN nodes n ON n.id = e.source_id
                WHERE down.depth < 50
            )
            SELECT id, text, kind, parent_id, rel, depth
            FROM down ORDER BY depth, id LIMIT $2
            """, topic_root_id, limit)
    return [dict(r) for r in rows]


async def parent_of(node_id):
    """The node this one points to (its single outgoing edge), or None."""
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT target_id FROM edges WHERE source_id = $1 LIMIT 1", node_id)
    return row["target_id"] if row else None


async def add_position_link(from_position, to_position, type_):
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO position_links (from_position, to_position, type)
                VALUES ($1, $2, $3)
                ON CONFLICT (from_position, to_position) DO UPDATE SET type = EXCLUDED.type
                """, from_position, to_position, type_)
            await _log(conn, "position_link_added",
                       {"from_position": from_position, "to_position": to_position,
                        "link_type": type_})


async def list_position_links(topic_root_id):
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT pl.* FROM position_links pl
            JOIN positions p ON p.id = pl.from_position
            WHERE p.topic_root_id = $1
            """, topic_root_id)
    return [dict(r) for r in rows]


async def set_position_reaction(author_id, position_id, stance):
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO position_reactions (author_id, position_id, stance)
                VALUES ($1, $2, $3)
                ON CONFLICT (author_id, position_id) DO UPDATE SET stance = EXCLUDED.stance
                """, author_id, position_id, stance)
            await _log(conn, "position_vote_set",
                       {"position_id": position_id, "stance": stance}, author_id)


async def get_position_reactions(position_id, topic_root_id):
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT pr.author_id, a.name, a.color, pr.stance,
                   COALESCE(tp.poi, 10) AS poi
            FROM position_reactions pr
            JOIN authors a ON a.id = pr.author_id
            LEFT JOIN author_topic_poi tp
                   ON tp.author_id = pr.author_id AND tp.topic_root_id = $1
            WHERE pr.position_id = $2
            """, topic_root_id, position_id)
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- edges / graph
async def add_edge(source_id, target_id, edge_type):
    if edge_type not in EDGE_TYPES:
        raise ValueError(f"edge type must be one of {EDGE_TYPES}")
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        async with conn.transaction():
            edge_id = await conn.fetchval(
                "INSERT INTO edges (source_id, target_id, type) VALUES ($1, $2, $3) RETURNING id",
                source_id, target_id, edge_type)
            await _log(conn, "edge_added",
                       {"edge_id": edge_id, "source_id": source_id,
                        "target_id": target_id, "edge_type": edge_type})
    return edge_id


# ---------------------------------------------------------------- event log
async def get_events(after_id=0, limit=200):
    """Read the append-only log, ascending — the audit layer's raw feed."""
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, ts, type, author_id, payload FROM events "
            "WHERE id > $1 ORDER BY id LIMIT $2", after_id, limit)
    out = []
    for r in rows:
        d = dict(r)
        d["ts"] = d["ts"].isoformat()
        d["payload"] = json.loads(d["payload"])
        out.append(d)
    return out


async def get_graph():
    """Whole graph as {nodes, links} — consumed by both the tree and the viz UI."""
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        node_rows = await conn.fetch(
            """
            SELECT n.*, a.name AS author, a.reputation AS reputation,
                   a.color AS author_color
            FROM nodes n
            LEFT JOIN authors a ON a.id = n.author_id
            ORDER BY n.id
            """)
        edge_rows = await conn.fetch("SELECT * FROM edges")
    nodes = [dict(r) for r in node_rows]
    for n in nodes:
        if n.get("poi_breakdown"):
            n["poi_breakdown"] = json.loads(n["poi_breakdown"])
    return {
        "nodes": nodes,
        "links": [
            {"source": e["source_id"], "target": e["target_id"], "type": e["type"]}
            for e in edge_rows
        ],
    }


async def list_topics():
    """
    Top-level discussions = ROOT nodes: argument nodes that don't reply to
    anything (no outgoing edge). Each root is one topic / one folder in the tree.
    """
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT n.id, n.text, n.poi_score, n.kind, a.name AS author, a.color AS author_color,
                   (SELECT count(*) FROM edges e2
                    JOIN nodes cn ON cn.id = e2.source_id
                    WHERE e2.target_id = n.id) AS reply_count
            FROM nodes n
            LEFT JOIN authors a ON a.id = n.author_id
            WHERE n.kind IN ('argument', 'question')
              AND NOT EXISTS (SELECT 1 FROM edges e WHERE e.source_id = n.id)
            ORDER BY n.id
            """)
    return [dict(r) for r in rows]
