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
import hashlib
from datetime import datetime, timedelta, timezone

import asyncpg

from . import poiformula


def text_hash(text):
    """Хеш версии текста узла — снимок, к которому привязывается якорь на участок.
    Если текст потом изменится, хеш разойдётся, и якорь считается устаревшим
    (цитата остаётся для перепривязки)."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://noosphere:noosphere@localhost/noosphere"
)

# undercut (подорвать) — новый тип: целится в конкретное предложение (участок),
# а не в узел целиком, чем и отличается от refute (опровергнуть). Именно ответ на
# фрагмент делает различие REBUTS/UNDERCUTS различимым на практике.
EDGE_TYPES = ("support", "refute", "qualify", "question",
              "proposal", "exploration", "atom", "undercut")

# Окно, в которое автор ещё распоряжается своим высказыванием: правит текст или
# снимает его целиком (vault: decisions/edit-delete-window). Час — потолок, а не
# гарантия: первый же ответ закрывает окно досрочно, потому что дальше текст
# держит чужое рассуждение. По истечении окна высказывание фиксируется, и
# остаётся только примечание сбоку (node_addenda) — приписка, не правка.
EDIT_WINDOW = timedelta(hours=1)

# «Онлайн» = у аккаунта была живая сессия за последние ONLINE_WINDOW. Пятнадцать
# минут, а не пять: человек, который читает длинную ветку или пишет ответ, не
# шлёт запросов, но никуда не уходил — узкое окно выдавало бы ноль при живых
# читателях. TOUCH_EVERY — как часто отметка вообще пишется в базу; точность
# «онлайна» упирается именно в него, поэтому минута.
ONLINE_WINDOW = timedelta(minutes=15)
TOUCH_EVERY = timedelta(minutes=1)


class Locked(Exception):
    """Высказывание больше не принадлежит автору: окно вышло, или уже ответили,
    или правит не автор. Несёт человекочитаемую причину — она идёт в ответ API
    как есть, потому что «нельзя» без «почему» здесь бесполезно."""

_pool: asyncpg.Pool | None = None

# Есть ли триграммное сходство (pg_trgm) для подсказки дублей при создании.
# Ставится best-effort в init_db: если расширение не поднялось (нет прав), падаем
# на фолбэк по совпадению слов (ILIKE) — подсказка не должна валить старт.
_has_trgm = False


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


async def ping():
    """Дёшево проверить, что БД отвечает — для healthcheck платформы."""
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        await conn.fetchval("SELECT 1")


# Tables in dependency order: Postgres, unlike SQLite, requires a referenced
# table to already exist. Fresh schema — no in-place migrations needed here.
_SCHEMA = [
    # An account IS an author with login credentials. Seeded test personas have
    # username = NULL and cannot log in; registered users can.
    """
    CREATE TABLE IF NOT EXISTS authors (
        id            SERIAL PRIMARY KEY,
        name          TEXT NOT NULL,
        color         TEXT,
        username      TEXT UNIQUE,
        password_hash TEXT,
        created_at    TIMESTAMPTZ DEFAULT now()
    )
    """,
    # reputation was a global, lossy label that never fed any mechanic (vote
    # weight is per-argument PoI, influence is per-topic PoI). It only confused,
    # so it is dropped (migration for databases that still have the column).
    "ALTER TABLE authors DROP COLUMN IF EXISTS reputation",
    # migrations for a database created before accounts existed
    "ALTER TABLE authors ADD COLUMN IF NOT EXISTS username TEXT UNIQUE",
    "ALTER TABLE authors ADD COLUMN IF NOT EXISTS password_hash TEXT",
    # the author's PoI-dialogue score: the default P₀ for topics without an
    # explicit per-topic prior (vault: poi-accrual-onboarding)
    "ALTER TABLE authors ADD COLUMN IF NOT EXISTS dialogue_poi DOUBLE PRECISION",
    # Per-account Anthropic key: each tester gets their own, topped up to a few
    # dollars. One person burning their budget cannot spend anyone else's, and
    # the cap needs no code to enforce it.
    "ALTER TABLE authors ADD COLUMN IF NOT EXISTS api_key TEXT",
    # Email exists for exactly one reason: without it a forgotten password
    # means a lost account, and with it the person's texts and PoI are
    # recoverable. Nullable because accounts created before this column
    # existed have none; required at registration from here on.
    "ALTER TABLE authors ADD COLUMN IF NOT EXISTS email TEXT",
    # Case-insensitive uniqueness: Ivan@x.ru and ivan@x.ru are one person, and
    # recovery has to resolve to a single account.
    "CREATE UNIQUE INDEX IF NOT EXISTS authors_email_key "
    "ON authors (lower(email)) WHERE email IS NOT NULL",
    # Single-use, expiring password-reset tokens. Only the hash is stored, so
    # a database leak does not hand over working reset links.
    """
    CREATE TABLE IF NOT EXISTS password_resets (
        token_hash TEXT PRIMARY KEY,
        author_id  INTEGER NOT NULL REFERENCES authors(id) ON DELETE CASCADE,
        created_at TIMESTAMPTZ DEFAULT now(),
        expires_at TIMESTAMPTZ NOT NULL,
        used_at    TIMESTAMPTZ
    )
    """,
    # Whether the address was proven to belong to the person. Registration is
    # open to anyone from 2026-08, so an unproven address is now the normal
    # case rather than the exception: reading needs nothing, publishing needs
    # this true.
    #
    # DEFAULT TRUE and then SET DEFAULT FALSE is not a typo — it is the whole
    # migration. The ADD backfills every existing row with TRUE (those accounts
    # came through hand-issued invites and would otherwise silently lose the
    # right to post, Alex's own account first), and the second statement makes
    # FALSE the default from here on. Both are idempotent, which matters
    # because init_db() runs on every boot.
    "ALTER TABLE authors ADD COLUMN IF NOT EXISTS email_verified BOOLEAN "
    "NOT NULL DEFAULT TRUE",
    "ALTER TABLE authors ALTER COLUMN email_verified SET DEFAULT FALSE",
    # Address-confirmation tokens. Same shape and same reasoning as
    # password_resets: hash only, single use, expiring.
    """
    CREATE TABLE IF NOT EXISTS email_verifications (
        token_hash TEXT PRIMARY KEY,
        author_id  INTEGER NOT NULL REFERENCES authors(id) ON DELETE CASCADE,
        email      TEXT NOT NULL,
        created_at TIMESTAMPTZ DEFAULT now(),
        expires_at TIMESTAMPTZ NOT NULL,
        used_at    TIMESTAMPTZ
    )
    """,
    # "Ask for a starting balance". A new account is granted nothing, because
    # every published argument costs an LLM call on Alex's card; the button
    # this table backs is what stands between an open registration and a wall.
    """
    CREATE TABLE IF NOT EXISTS balance_requests (
        id          SERIAL PRIMARY KEY,
        author_id   INTEGER NOT NULL REFERENCES authors(id) ON DELETE CASCADE,
        note        TEXT,
        created_at  TIMESTAMPTZ DEFAULT now(),
        resolved_at TIMESTAMPTZ,
        granted_usd NUMERIC(10,4)
    )
    """,
    # One open request per account, enforced here rather than in a handler:
    # the button is a mail to Alex, and a loop over it would be a way to flood
    # his inbox. A partial index lets the same person ask again once the
    # previous request was answered.
    "CREATE UNIQUE INDEX IF NOT EXISTS balance_requests_open_key "
    "ON balance_requests (author_id) WHERE resolved_at IS NULL",
    # What the participant has been given to spend, in USD. Separate from
    # api_key on purpose: the key says WHOSE budget pays, the balance says how
    # much is left — the cabinet needs the second to show anything useful, and
    # it is what a future top-up would credit.
    "ALTER TABLE authors ADD COLUMN IF NOT EXISTS balance_usd NUMERIC(10,4) "
    "NOT NULL DEFAULT 0",
    # One row per LLM call. Kept as events rather than a running total so the
    # cabinet can show "where did my money go", not just a number going down.
    """
    CREATE TABLE IF NOT EXISTS usage_events (
        id            SERIAL PRIMARY KEY,
        author_id     INTEGER NOT NULL REFERENCES authors(id) ON DELETE CASCADE,
        model         TEXT NOT NULL,
        input_tokens  INTEGER NOT NULL DEFAULT 0,
        output_tokens INTEGER NOT NULL DEFAULT 0,
        cost_usd      NUMERIC(12,6) NOT NULL DEFAULT 0,
        endpoint      TEXT,
        created_at    TIMESTAMPTZ DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS usage_events_author_idx "
    "ON usage_events (author_id, created_at DESC)",
    # Cached prefix tokens, billed at ~0.1x (read) and ~1.25x (write) rather
    # than 1x. Recorded separately so the cabinet can show a vote dialogue for
    # what it is — mostly cheap re-reads of one shared prefix — instead of
    # inflating it tenfold by counting them as fresh input.
    "ALTER TABLE usage_events ADD COLUMN IF NOT EXISTS "
    "cache_read_tokens INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE usage_events ADD COLUMN IF NOT EXISTS "
    "cache_write_tokens INTEGER NOT NULL DEFAULT 0",
    # Платил ли за этот вызов общий ключ инстанса (а не собственный ключ
    # аккаунта). Пишется в момент траты, а не выводится джойном по authors:
    # аккаунт может обзавестись своим ключом позже, и тогда джойн задним числом
    # вычел бы его прошлые траты из общего счётчика — то есть ослабил бы кран
    # ровно в ту сторону, в которую предохранителю ошибаться нельзя.
    # DEFAULT TRUE верен для истории: ключей на тестера не выдавали.
    "ALTER TABLE usage_events ADD COLUMN IF NOT EXISTS "
    "shared_key BOOLEAN NOT NULL DEFAULT TRUE",
    # Registration is invite-only: accounts are handed out on request, so the
    # code is consumed in the same transaction that creates the account (two
    # people racing on one code must not both get in).
    """
    CREATE TABLE IF NOT EXISTS invites (
        code       TEXT PRIMARY KEY,
        note       TEXT,
        used_by    INTEGER REFERENCES authors(id) ON DELETE SET NULL,
        created_at TIMESTAMPTZ DEFAULT now(),
        used_at    TIMESTAMPTZ
    )
    """,
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
    # РАЗГОВОР С ИИ ДО ПУБЛИКАЦИИ. Решение ai-navigator-draft-review сделало его
    # эфемерным: черновик и разбор жили в браузере автора и умирали вместе с
    # публикацией. На время закрытого теста с живыми людьми этого мало — понять,
    # где человек споткнулся, постфактум нечем: опубликованный текст показывает
    # результат и молчит о том, что ему предшествовало.
    #
    # Поэтому здесь лежат ВСЕ обращения к ИИ вокруг черновика: разбор
    # (`review`), разговор с компаньоном (`companion`) и быстрая проверка места
    # (`precheck`). Файловый лог COMPANION_LOG остаётся, но на Railway файл
    # внутри контейнера не переживает передеплой — наблюдать по нему нельзя.
    #
    # CASCADE, а не SET NULL: удаление аккаунта стирает и его черновики
    # (vault: data-deletion-model — аккаунт стираем; безотзывная лицензия
    # покрывает ОПУБЛИКОВАННОЕ, а черновик человек не публиковал).
    """
    CREATE TABLE IF NOT EXISTS companion_log (
        id          SERIAL PRIMARY KEY,
        author_id   INTEGER REFERENCES authors(id) ON DELETE CASCADE,
        created_at  TIMESTAMPTZ DEFAULT now(),
        kind        TEXT NOT NULL,        -- review | companion | precheck
        connect_to  INTEGER,              -- узел-родитель (NULL = корень)
        root_kind   TEXT,                 -- вид корня, когда черновик открывает
        draft       TEXT,                 -- текст черновика на этот ход
        history     JSONB,                -- реплики разговора до этого хода
        result      JSONB                 -- что ответил ИИ
    )
    """,
    "CREATE INDEX IF NOT EXISTS companion_log_author_idx "
    "ON companion_log (author_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS companion_log_time_idx "
    "ON companion_log (created_at DESC)",
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
    # title: short, author-supplied label for a TOPIC ROOT (question/proposal/
    # exploration/thesis that opens a discussion). NULL for replies — they are
    # not discussions in their own right, so the tree falls back to an excerpt
    # of their text. Required at creation time for new roots (see main.py).
    "ALTER TABLE nodes ADD COLUMN IF NOT EXISTS title TEXT",
    # atom_group marks a node CUT OUT of an exploration (vault:
    # exploration-atomization): non-NULL = "this is an atom of a разбор,
    # grouped under this heading, NOT a position its author has taken".
    "ALTER TABLE nodes ADD COLUMN IF NOT EXISTS atom_group TEXT",
    # dissented: the author rejected how their argument was composed into a pool
    # and pulled it out into its OWN verbatim position. A dissented argument is
    # never auto-merged again — neither incremental assignment nor a full
    # re-cluster may fold it back into someone else's composed text (п.10).
    "ALTER TABLE nodes ADD COLUMN IF NOT EXISTS dissented BOOLEAN NOT NULL DEFAULT FALSE",
    """
    CREATE TABLE IF NOT EXISTS edges (
        id        SERIAL PRIMARY KEY,
        source_id INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
        target_id INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
        type      TEXT NOT NULL CHECK (type IN
            ('support','refute','qualify','question','proposal','exploration','atom','undercut'))
    )
    """,
    # existing databases carry the older CHECK — recreate it (idempotent pair)
    "ALTER TABLE edges DROP CONSTRAINT IF EXISTS edges_type_check",
    """ALTER TABLE edges ADD CONSTRAINT edges_type_check CHECK (type IN
       ('support','refute','qualify','question','proposal','exploration','atom','undercut'))""",
    # ОТВЕТ НА ФРАГМЕНT: якорь ребра на УЧАСТОК текста цели, а не на узел целиком.
    # Все nullable — NULL означает ответ на весь узел (прежнее поведение). Якорь =
    # хеш версии текста + смещения + сохранённая цитата: цитата переживает правку
    # текста (по ней перепривязываемся), хеш ловит расхождение версий. Пишется в
    # append-only лог вместе с ребром. undercut без якоря не имеет смысла (см. route).
    "ALTER TABLE edges ADD COLUMN IF NOT EXISTS anchor_hash  TEXT",
    "ALTER TABLE edges ADD COLUMN IF NOT EXISTS anchor_start INTEGER",
    "ALTER TABLE edges ADD COLUMN IF NOT EXISTS anchor_end   INTEGER",
    "ALTER TABLE edges ADD COLUMN IF NOT EXISTS anchor_quote TEXT",
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
    # reactor_weight: the reactor's topic PoI FROZEN at the moment the reaction
    # was cast (snapshot semantics). The reaction channel counts this frozen
    # value forever after — NOT the reactor's live PoI. Without it, a reaction's
    # contribution changed whenever the reactor's PoI later moved AND the recipient
    # happened to be recomputed, so a stored PoI depended on event ORDER rather
    # than on current state. Freezing makes author_topic_poi a pure function of
    # the event log (prior + scored contributions + frozen reaction weights).
    """
    CREATE TABLE IF NOT EXISTS reactions (
        author_id      INTEGER NOT NULL REFERENCES authors(id) ON DELETE CASCADE,
        node_id        INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
        stance         TEXT NOT NULL CHECK (stance IN ('agree','disagree')),
        reactor_weight DOUBLE PRECISION,
        created_at     TIMESTAMPTZ DEFAULT now(),
        PRIMARY KEY (author_id, node_id)
    )
    """,
    "ALTER TABLE reactions ADD COLUMN IF NOT EXISTS reactor_weight DOUBLE PRECISION",
    """
    CREATE TABLE IF NOT EXISTS positions (
        id            SERIAL PRIMARY KEY,
        topic_root_id INTEGER NOT NULL,
        headline      TEXT,
        composed      TEXT,
        stance        TEXT,
        author_id     INTEGER REFERENCES authors(id) ON DELETE SET NULL,
        created_at    TIMESTAMPTZ DEFAULT now()
    )
    """,
    # author_id: who SIGNED this position. NULL for LLM-clustered pools (a pool
    # has no single author — its supporters are its member args' authors). Set
    # for a conclusion, which is one person's forward claim they edited and
    # signed (п.9), never anonymous LLM text on the map.
    "ALTER TABLE positions ADD COLUMN IF NOT EXISTS author_id INTEGER "
    "REFERENCES authors(id) ON DELETE SET NULL",
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
    # A DECISION is a question put to a vote over a topic's positions
    # (vault: decisions/vote-dialogue-weight). Two fields carry the design:
    #
    # material_snapshot: block B — the debate material, rendered ONCE when the
    # decision opens and never touched again. Two reasons, the second the
    # bigger one: (a) it is the cached prompt prefix shared by every voter, and
    # any byte change invalidates that cache for all of them at once;
    # (b) a live slice would judge different voters against different material,
    # which makes their weights incomparable inside one vote. If the debate
    # moves on substantially, that is a NEW decision, not an edit to this one.
    #
    # judge_model: frozen with the material for the same reason. Half the
    # voters scored by one model and half by another is not a 1..5 spread of
    # understanding, it is a spread of which model they happened to get.
    """
    CREATE TABLE IF NOT EXISTS decisions (
        id                SERIAL PRIMARY KEY,
        topic_root_id     INTEGER NOT NULL,
        question          TEXT NOT NULL,
        status            TEXT NOT NULL DEFAULT 'draft'
                          CHECK (status IN ('draft','open','closed')),
        material_snapshot TEXT,
        judge_model       TEXT,
        opens_at          TIMESTAMPTZ,
        closes_at         TIMESTAMPTZ,
        created_by        INTEGER REFERENCES authors(id) ON DELETE SET NULL,
        created_at        TIMESTAMPTZ DEFAULT now()
    )
    """,
    # A decision is NOT a ballot with a fixed set of answers. The whole point of
    # the preparation dialogue is that a person may come out of it seeing the
    # question differently than whoever posed it — including seeing that it is
    # the wrong question (vault: drafts/alignment-and-forking, "хочет третье —
    # синтез, а не бинар"). A closed option list would throw away the most
    # valuable thing the process produces.
    #
    # origin:
    #   'initial'  — an option the decision opened with
    #   'proposed' — a synthesis a voter added DURING the vote (a new position
    #                in the graph, so it carries its own arguments)
    #   'reframe'  — "this question is posed wrongly". Not an answer: it
    #                rejects the frame. Tallied and reported SEPARATELY, never
    #                folded into a 'no' — a reframe with weight behind it is a
    #                signal to re-open the debate, not a vote against.
    """
    CREATE TABLE IF NOT EXISTS decision_options (
        id           SERIAL PRIMARY KEY,
        decision_id  INTEGER NOT NULL REFERENCES decisions(id) ON DELETE CASCADE,
        position_id  INTEGER REFERENCES positions(id) ON DELETE SET NULL,
        label        TEXT,
        origin       TEXT NOT NULL DEFAULT 'initial'
                     CHECK (origin IN ('initial','proposed','reframe')),
        proposed_by  INTEGER REFERENCES authors(id) ON DELETE SET NULL,
        revision     INTEGER NOT NULL DEFAULT 1,
        sort         INTEGER NOT NULL DEFAULT 0
    )
    """,
    # Freezing the material and letting the question be reframed pull in
    # opposite directions. REVISIONS resolve it: each revision has its own
    # immutable snapshot, so the prompt prefix stays byte-stable and voters
    # remain comparable WITHIN a revision, while a new option that materially
    # changes the debate opens the next one.
    #
    # This also turns reframing into readable data instead of noise: it becomes
    # visible that at revision 2 a synthesis appeared and how the weighted
    # picture moved after it — the decision-level form of the opinion
    # trajectory in drafts/alignment-and-forking.
    """
    CREATE TABLE IF NOT EXISTS decision_revisions (
        id                SERIAL PRIMARY KEY,
        decision_id       INTEGER NOT NULL REFERENCES decisions(id) ON DELETE CASCADE,
        revision          INTEGER NOT NULL,
        material_snapshot TEXT NOT NULL,
        opened_by_option  INTEGER REFERENCES decision_options(id) ON DELETE SET NULL,
        created_at        TIMESTAMPTZ DEFAULT now(),
        UNIQUE (decision_id, revision)
    )
    """,
    # One preparation dialogue per (decision, author). No retakes, but
    # resumable until the deadline: each finalize re-scores the WHOLE
    # accumulated transcript and updates the weight (same contract as the
    # onboarding dialogue in dialogue.py).
    #
    # transcript is kept in full, deliberately: the person paid for this
    # conversation and it is theirs to re-read, and it is the only auditable
    # record of what the interlocutor actually said to a voter.
    """
    CREATE TABLE IF NOT EXISTS vote_dialogues (
        id          SERIAL PRIMARY KEY,
        decision_id INTEGER NOT NULL REFERENCES decisions(id) ON DELETE CASCADE,
        author_id   INTEGER NOT NULL REFERENCES authors(id) ON DELETE CASCADE,
        transcript  JSONB NOT NULL DEFAULT '[]'::jsonb,
        score       INTEGER,
        criteria    JSONB,
        weight      DOUBLE PRECISION,
        summary     TEXT,
        revision    INTEGER NOT NULL DEFAULT 1,
        finished_at TIMESTAMPTZ,
        created_at  TIMESTAMPTZ DEFAULT now(),
        UNIQUE (decision_id, author_id)
    )
    """,
    # weight is FROZEN at cast time (same snapshot semantics as
    # reactions.reactor_weight): re-finalizing the dialogue later must not
    # silently re-weight a vote already counted.
    #
    # Floor is 1, never 0 — a voter who skipped or failed the dialogue still
    # votes, they just do not get the multiplier. The dialogue reveals, it does
    # not gate (vault: poi-accrual-onboarding, raised to tier 2).
    # Keyed per (decision, author, OPTION), not per (decision, author):
    # positions in this graph are clustered as "the same OR COMPLEMENTARY
    # point" (pools.py), so supporting several of them at once is a coherent
    # thing to mean, not a spoiled ballot. Approval-shaped by default; a
    # single-choice decision is just the case where everyone marks one.
    #
    # revision: which material snapshot this voter was actually judged against.
    # Without it, a tally silently mixes people who saw different debates.
    """
    CREATE TABLE IF NOT EXISTS votes (
        decision_id      INTEGER NOT NULL REFERENCES decisions(id) ON DELETE CASCADE,
        author_id        INTEGER NOT NULL REFERENCES authors(id) ON DELETE CASCADE,
        option_id        INTEGER NOT NULL REFERENCES decision_options(id) ON DELETE CASCADE,
        weight           DOUBLE PRECISION NOT NULL DEFAULT 1.0,
        revision         INTEGER NOT NULL DEFAULT 1,
        vote_dialogue_id INTEGER REFERENCES vote_dialogues(id) ON DELETE SET NULL,
        cast_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (decision_id, author_id, option_id)
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
    # Рабочее дерево: какие темы человек держит у себя. Дерево перестало быть
    # каталогом всего (это работа карты) и стало личной подборкой — иначе на
    # сотне участников оно превращается в простыню из сотен корней.
    """
    CREATE TABLE IF NOT EXISTS workspace (
        author_id     INTEGER NOT NULL REFERENCES authors(id) ON DELETE CASCADE,
        topic_root_id INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
        added_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (author_id, topic_root_id)
    )
    """,
    # Отличает «никогда не было подборки» от «человек всё вычистил сам».
    # Без этого флага засев при первом входе возвращался бы после каждой
    # уборки, и «убрать» переставало бы работать как убрать.
    "ALTER TABLE authors ADD COLUMN IF NOT EXISTS workspace_seeded BOOLEAN NOT NULL DEFAULT FALSE",
    # СЛУЖЕБНЫЙ АККАУНТ (NOOSPHERE AI BOT и подобные): голос самой платформы, а
    # не участник. Он публикует служебные тексты — вопросы о правилах, объявления —
    # и при этом НЕ пользуется ИИ вообще: ни компаньоном, ни оценкой своих текстов.
    #
    # Почему без оценки: PoI существует, чтобы поднимать проработанные доводы
    # ЛЮДЕЙ. Текст, написанный машиной и оценённый машиной, стоял бы в том же
    # рейтинге выше человеческих просто потому, что писавший знает рубрику
    # изнутри. Служебные узлы остаются с пустым PoI и в соревновании не участвуют.
    "ALTER TABLE authors ADD COLUMN IF NOT EXISTS is_service BOOLEAN NOT NULL DEFAULT FALSE",
    # СОГЛАСИЕ С УСЛОВИЯМИ. Регистрация открыта незнакомым людям, и лицензия на
    # вклады с политикой данных должны быть приняты тем, кому их показали
    # (vault: data-deletion-model). Согласие фиксируется, а не подразумевается:
    # запоминаем момент и версию текста, иначе через год нечем будет показать,
    # с чем именно человек соглашался.
    "ALTER TABLE authors ADD COLUMN IF NOT EXISTS terms_accepted_at TIMESTAMPTZ",
    "ALTER TABLE authors ADD COLUMN IF NOT EXISTS terms_version TEXT",
    # Рубрикация темы. Отдельной таблицей, а не колонками в nodes: рубрика
    # есть только у КОРНЯ обсуждения, и держать её на всех узлах значило бы
    # хранить пустоту в 99% строк.
    """
    CREATE TABLE IF NOT EXISTS topic_facets (
        topic_root_id INTEGER PRIMARY KEY REFERENCES nodes(id) ON DELETE CASCADE,
        domain        TEXT NOT NULL,
        sub           TEXT,
        created_at    TIMESTAMPTZ DEFAULT now()
    )
    """,
    # География — многие-ко-многим, и сюда пишется ЗАМЫКАНИЕ: вместе со
    # страной попадают её регионы и части света. Россия лежит и под «Европа»,
    # и под «Азия», поэтому фильтр по континенту остаётся одним сравнением по
    # индексу, а не обходом дерева на каждый запрос.
    """
    CREATE TABLE IF NOT EXISTS topic_geo (
        topic_root_id INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
        geo           TEXT NOT NULL,
        PRIMARY KEY (topic_root_id, geo)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS topic_tags (
        topic_root_id INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
        tag           TEXT NOT NULL,
        PRIMARY KEY (topic_root_id, tag)
    )
    """,
    # ПРОБЛЕМА — надстройка над корнем обсуждения (nodes.kind='problem'). У темы
    # нет состояния, у проблемы есть: постановка (это text/title узла), причины
    # и составные части, масштаб. «Что пробовали» вынесено в реестр
    # (interventions), «где спор» — это сам граф, «решения на столе» — позиции.
    # Отдельной таблицей по той же причине, что topic_facets: поле есть только у
    # КОРНЯ-проблемы, держать его на всех узлах — хранить пустоту в 99% строк.
    #
    # Масштаб (где, сколько) — это ДАННЫЕ извне, а не аргумент: несущая внешняя
    # ссылка + сохранённая ВЫДЕРЖКА текстом (блоб не хостим — «только текст»),
    # чтобы страница пережила исчезновение источника и дала превью без перехода.
    """
    CREATE TABLE IF NOT EXISTS problems (
        topic_root_id       INTEGER PRIMARY KEY REFERENCES nodes(id) ON DELETE CASCADE,
        causes              TEXT,
        scale_note          TEXT,
        scale_url           TEXT,
        scale_excerpt       TEXT,
        scale_retrieved_at  TIMESTAMPTZ,
        updated_at          TIMESTAMPTZ DEFAULT now()
    )
    """,
    # ЧЕГО НЕ ХВАТАЕТ — что во всех записях реестра не покрыто и что не
    # переносится. Отдельное поле, а не часть постановки: постановка описывает
    # проблему, а этот текст описывает ДЫРУ в накопленных попытках, и переписывать
    # его придётся при каждой новой записи реестра. Без него накопитель читается
    # как список ссылок; с ним видно, куда писать следующий довод.
    "ALTER TABLE problems ADD COLUMN IF NOT EXISTS gap TEXT",
    # МАСШТАБ СТРОКАМИ: «где встречается и в каких объёмах». Одна строка — один
    # регион с одной цифрой и своим источником. Раньше масштаб был одним абзацем
    # (problems.scale_*): для проблемы, которая живёт в четырёх странах с разными
    # числами, это склеивало несравнимое в одно предложение и не давало сослаться
    # на каждый источник отдельно. Старые поля остаются — данные, записанные до
    # разделения, никуда не деваются и показываются, пока строк нет.
    """
    CREATE TABLE IF NOT EXISTS problem_scale (
        id             SERIAL PRIMARY KEY,
        topic_root_id  INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
        region         TEXT NOT NULL,
        figure         TEXT NOT NULL,
        source_url     TEXT,
        source_excerpt TEXT,
        retrieved_at   TIMESTAMPTZ,
        author_id      INTEGER REFERENCES authors(id) ON DELETE SET NULL,
        created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
        deleted_at     TIMESTAMPTZ
    )
    """,
    "CREATE INDEX IF NOT EXISTS problem_scale_root ON problem_scale (topic_root_id)",
    # НАКОПИТЕЛЬ РЕШЕНИЙ — реестр вмешательств. Запись ФАКТА, не аргумент: что
    # пробовали, где, когда, кто, что вышло, от каких условий зависело. Копит
    # провалы наравне с успехами — outcome_kind='failure' первоклассное значение,
    # «пробовали там, не сработало, из-за чего» самое ценное. Растёт в цене со
    # временем и переносится между странами (отсюда индекс по geo).
    #
    # Атрибуция («благодаря чему сработало») сюда НЕ пишется — это оспоримое
    # причинное утверждение, ему место в графе (REBUTS/UNDERCUTS), следующим
    # заходом. Здесь только регистрируемый факт вмешательства и его исход.
    #
    # source_* — та же несущая внешняя ссылка + выдержка текстом: доказательство
    # живёт снаружи, здесь его текстовый след, переживающий линк-рот.
    """
    CREATE TABLE IF NOT EXISTS interventions (
        id                  SERIAL PRIMARY KEY,
        topic_root_id       INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
        what                TEXT NOT NULL,
        actor               TEXT,
        geo                 TEXT,
        when_text           TEXT,
        outcome             TEXT,
        outcome_kind        TEXT NOT NULL DEFAULT 'unclear'
                            CHECK (outcome_kind IN
                                ('success','partial','failure','mixed','unclear')),
        conditions          TEXT,
        source_url          TEXT,
        source_excerpt      TEXT,
        source_retrieved_at TIMESTAMPTZ,
        author_id           INTEGER REFERENCES authors(id) ON DELETE SET NULL,
        created_at          TIMESTAMPTZ DEFAULT now()
    )
    """,
    # ПРИНАДЛЕЖНОСТЬ как РЕБРО, а не скаляр. Раньше узел жил ровно в одной теме
    # (nodes.topic_root_id). Теперь принадлежность — строка node→проблема с
    # АВТОРОМ и ВРЕМЕНЕМ: один канонический узел может стоять под несколькими
    # проблемами без копии-цитаты (многодомность аргумента). author_id здесь —
    # кто ПОЛОЖИЛ узел под эту проблему (для домашней = автор узла; для доп.
    # проблемы может быть другой человек: «этот довод из X бьёт и по Y»).
    #
    # nodes.topic_root_id НЕ удаляется: он остаётся указателем на ДОМАШНЮЮ
    # проблему — ту, где начисляется PoI автора (ключ author_topic_poi). node_topics
    # — истина принадлежности для КОРПУСА (где довод появляется); домашняя тема —
    # для НАЧИСЛЕНИЯ (где копится компетенция). Второе завязано на решение по
    # области PoI, которое отложено, поэтому скаляр пока живёт как home-указатель.
    """
    CREATE TABLE IF NOT EXISTS node_topics (
        node_id       INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
        topic_root_id INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
        author_id     INTEGER REFERENCES authors(id) ON DELETE SET NULL,
        created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (node_id, topic_root_id)
    )
    """,
    # АТРИБУЦИЯ как аргумент о вмешательстве. Запись в реестре (interventions) —
    # это ФАКТ. Утверждение «сработало благодаря X» / «переносимо на страну Y» —
    # оспоримое ПРИЧИННОЕ утверждение, и место ему в графе, а не в реестре. Узел
    # kind='attribution' ССЫЛАЕТСЯ на запись реестра (intervention_id) и живёт в
    # теме проблемы; по нему бьют обычными рёбрами (refute/undercut на участок) —
    # здесь REBUTS/UNDERCUTS применяются по прямому назначению. CASCADE: атрибуция
    # о стёртом факте теряет смысл, поэтому уходит вместе с записью.
    "ALTER TABLE nodes ADD COLUMN IF NOT EXISTS intervention_id INTEGER "
    "REFERENCES interventions(id) ON DELETE CASCADE",
    # ПРАВИЛО ЧАСА (vault: decisions/edit-delete-window). Опубликованный текст
    # НЕ правится никогда: работа над формулировкой идёт до отправки, с ИИ-
    # компаньоном. Автор может лишь СНЯТЬ своё высказывание — час после
    # публикации и только пока никто не ответил.
    # Час — это окно ДО фиксации: дальше высказывание уходит в цепочку (Merkle-
    # корень пачки в Solana) и в снапшот корпуса, где неизменяемо уже физически.
    #
    # deleted_at: удаление МЯГКОЕ — строка остаётся, для всех выдач её нет.
    # Жёсткий DELETE рвал бы event log и лишал возможности откатить случайное
    # нажатие; ссылочная целостность тоже дешевле так.
    "ALTER TABLE nodes ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ",
    "ALTER TABLE interventions ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ",
    # ОТЗЫВ: «больше не настаиваю». Не удаление — узел остаётся на месте вместе
    # с ответами, только помечен. Доступен автору всегда, потому что это не
    # изменение сказанного, а сообщение о нынешнем отношении к сказанному.
    "ALTER TABLE nodes ADD COLUMN IF NOT EXISTS retracted_at  TIMESTAMPTZ",
    "ALTER TABLE nodes ADD COLUMN IF NOT EXISTS retract_note  TEXT",
    "ALTER TABLE interventions ADD COLUMN IF NOT EXISTS retracted_at TIMESTAMPTZ",
    "ALTER TABLE interventions ADD COLUMN IF NOT EXISTS retract_note TEXT",
    # ПРИМЕЧАНИЕ АВТОРА — что остаётся, когда час истёк. Исходный текст
    # неприкосновенен (на него уже отвечали, его хеш уходит в цепочку), но
    # автор может дописать датированную приписку «уточняю» / «здесь ошибся».
    # Отдельная таблица, а не поле: приписок может быть несколько, каждая со
    # своим временем, и ни одна не переписывает сказанное.
    """
    CREATE TABLE IF NOT EXISTS node_addenda (
        id         SERIAL PRIMARY KEY,
        node_id    INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
        author_id  INTEGER NOT NULL REFERENCES authors(id) ON DELETE CASCADE,
        text       TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS node_addenda_node_idx ON node_addenda (node_id)",
    # ПОСЛЕДНЯЯ АКТИВНОСТЬ СЕССИИ — из неё считается «сколько человек сейчас
    # онлайн». Живёт на сессии, а не на аккаунте: у одного человека может быть
    # открыт телефон и ноутбук, и «онлайн» тогда честнее считать по
    # DISTINCT author_id, а не по последней перезаписанной отметке.
    # Пишется не на каждый запрос, а не чаще раза в минуту (см. session_author).
    "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS last_seen TIMESTAMPTZ",
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
    "CREATE INDEX IF NOT EXISTS topic_geo_idx ON topic_geo (geo)",
    "CREATE INDEX IF NOT EXISTS topic_tags_idx ON topic_tags (tag)",
    "CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_id)",
    "CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id)",
    "CREATE INDEX IF NOT EXISTS idx_nodes_position ON nodes(position_id)",
    # PoI accrual recompute and the clustering read both filter by
    # (topic_root_id[, author_id]) — index it so they don't scan the table
    "CREATE INDEX IF NOT EXISTS idx_nodes_topic_author "
    "ON nodes(topic_root_id, author_id)",
    # the registry is read per-problem, and its whole value is transfer ACROSS
    # problems/countries — so it is also read by geo (see the interventions comment)
    "CREATE INDEX IF NOT EXISTS interventions_topic_idx "
    "ON interventions(topic_root_id, id)",
    "CREATE INDEX IF NOT EXISTS interventions_geo_idx ON interventions(geo)",
    # "какие узлы под этой проблемой" (включая многодомные) и "под какими
    # проблемами этот узел" — оба должны быть индексным поиском, не сканом
    "CREATE INDEX IF NOT EXISTS node_topics_topic_idx "
    "ON node_topics(topic_root_id, node_id)",
    "CREATE INDEX IF NOT EXISTS node_topics_node_idx ON node_topics(node_id)",
    # атрибуции одной записи реестра — индексный поиск, не скан таблицы узлов
    "CREATE INDEX IF NOT EXISTS idx_nodes_intervention ON nodes(intervention_id)",
    # «кто онлайн» — запрос по окну последней активности, а не скан всех сессий
    "CREATE INDEX IF NOT EXISTS sessions_last_seen_idx ON sessions(last_seen)",
]


async def init_db():
    global _has_trgm
    pool = await init_pool()
    async with pool.acquire() as conn:
        for stmt in _SCHEMA:
            await conn.execute(stmt)
        for stmt in _INDEXES:
            await conn.execute(stmt)
        # триграммы для подсказки дублей — best-effort, старт не зависит от них
        try:
            await conn.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
            _has_trgm = True
        except Exception:
            _has_trgm = False
        # backfill topic_root_id for rows that predate the column (walk up once)
        orphans = [r["id"] for r in await conn.fetch(
            "SELECT id FROM nodes WHERE topic_root_id IS NULL")]
    for nid in orphans:
        root = await topic_root_of(nid)
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE nodes SET topic_root_id = $1 WHERE id = $2", root, nid)
    # backfill belonging edges: each existing node's scalar topic_root_id becomes
    # one node_topics row (its HOME belonging), carrying the node's own author and
    # creation time. Idempotent — re-running init_db never duplicates. Runs after
    # the topic_root_id backfill above so no node is missing its home edge.
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO node_topics (node_id, topic_root_id, author_id, created_at)
            SELECT id, topic_root_id, author_id, created_at
            FROM nodes WHERE topic_root_id IS NOT NULL
            ON CONFLICT (node_id, topic_root_id) DO NOTHING
            """)
    # backfill reactor_weight for reactions cast before the column existed:
    # a best-effort one-time freeze of the reactor's CURRENT topic PoI (the
    # true value is unrecoverable, but this beats treating every legacy vote as
    # the default 10). New reactions freeze correctly at cast time.
    async with pool.acquire() as conn:
        stale = await conn.fetch(
            "SELECT r.author_id, r.node_id, n.topic_root_id "
            "FROM reactions r JOIN nodes n ON n.id = r.node_id "
            "WHERE r.reactor_weight IS NULL")
    for row in stale:
        w = await topic_poi_of(row["author_id"], row["topic_root_id"])
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE reactions SET reactor_weight = $1 "
                "WHERE author_id = $2 AND node_id = $3",
                w, row["author_id"], row["node_id"])


async def wipe(force=False):
    """Delete all rows (clean slate between test/seed runs).

    Refuses once real accounts exist. This TRUNCATEs `authors` along with every
    node, dialogue and score — on a live instance that is not a reset, it is
    the permanent loss of the testers' accounts and everything they wrote.
    Seeding a fresh database still works (no registered users yet); to wipe an
    instance that has them, set NOOSPHERE_ALLOW_WIPE=1 deliberately.
    """
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        if not force and os.environ.get("NOOSPHERE_ALLOW_WIPE") != "1":
            registered = await conn.fetchval(
                "SELECT count(*) FROM authors WHERE username IS NOT NULL")
            if registered:
                raise RuntimeError(
                    f"отказ: в базе {registered} зарегистрированных аккаунтов. "
                    "wipe() удалит их вместе со всеми текстами и оценками. "
                    "Если это действительно нужно — NOOSPHERE_ALLOW_WIPE=1.")
        # Коды приглашений переживают сброс. Они не данные обсуждения, а КЛЮЧИ
        # от входа: регистрация закрыта кодом (INVITE_REQUIRED), а invites.used_by
        # ссылается на authors — значит CASCADE уносит и неиспользованные коды
        # вместе с аккаунтами. Инстанс после пересева оказался бы читаемым и
        # закрытым на запись для всех, включая владельца: завести аккаунт стало
        # бы нечем. Сохраняем только НЕиспользованные — использованные указывают
        # на авторов, которых больше нет.
        kept = await conn.fetch(
            "SELECT code, note FROM invites WHERE used_by IS NULL")
        await conn.execute(
            "TRUNCATE position_reactions, position_links, positions, "
            "author_topic_poi, reactions, edges, nodes, sessions, dialogues, "
            "usage_events, authors, events RESTART IDENTITY CASCADE"
        )
        if kept:
            await conn.executemany(
                "INSERT INTO invites (code, note) VALUES ($1, $2) "
                "ON CONFLICT (code) DO NOTHING",
                [(r["code"], r["note"]) for r in kept])


# ---------------------------------------------------------------- nodes
async def add_node(text, poi_score=None, poi_breakdown=None, author_id=None,
                   kind="argument", position_id=None, topic_root_id=None,
                   atom_group=None, title=None, intervention_id=None):
    """topic_root_id=None means the node IS a new topic root (points to itself).
    intervention_id set marks the node as an ATTRIBUTION claim about a registry
    record (a disputable causal statement, argued against in the graph)."""
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        async with conn.transaction():
            node_id = await conn.fetchval(
                "INSERT INTO nodes (text, poi_score, poi_breakdown, author_id, kind, "
                "position_id, topic_root_id, atom_group, title, intervention_id) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10) RETURNING id",
                text, poi_score,
                json.dumps(poi_breakdown) if poi_breakdown else None,
                author_id, kind, position_id, topic_root_id, atom_group, title,
                intervention_id,
            )
            if topic_root_id is None:
                await conn.execute(
                    "UPDATE nodes SET topic_root_id = id WHERE id = $1", node_id)
            # home belonging edge (node_topics is the belonging truth; the scalar
            # above stays as the home pointer). For a root, home is itself.
            home = topic_root_id or node_id
            await conn.execute(
                "INSERT INTO node_topics (node_id, topic_root_id, author_id) "
                "VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
                node_id, home, author_id)
            await _log(conn, "node_added",
                       {"node_id": node_id, "text": text, "kind": kind,
                        "position_id": position_id, "poi_score": poi_score,
                        "topic_root_id": home,
                        "atom_group": atom_group},
                       author_id)
    return node_id


async def atom_children(exploration_id):
    """The atoms already cut from an exploration (source of its 'atom' edges),
    oldest first. Empty means the exploration has not been atomized yet."""
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT source_id AS id FROM edges "
            "WHERE target_id = $1 AND type = 'atom' ORDER BY source_id",
            exploration_id)
    return [r["id"] for r in rows]


async def add_atoms_once(exploration_id, root_id, author_id, atoms):
    """
    Materialize an exploration's atoms EXACTLY ONCE, atomically.

    Idempotent: if the exploration already has atoms (a double-submit or a
    retry after a dropped response), no new rows are written and the existing
    atom ids are returned. The check-and-create runs in one transaction with
    the exploration row locked (FOR UPDATE), so two concurrent confirms
    serialize — the second sees the first's atoms instead of duplicating them.
    All nodes + edges land in that single transaction, so a mid-way failure
    leaves nothing behind rather than half a разбор.

    atoms: list of {text, kind, group}. Returns (atom_ids, already_atomized).
    """
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # lock the exploration so concurrent confirms can't both pass the
            # "not atomized yet" check
            await conn.execute(
                "SELECT id FROM nodes WHERE id = $1 FOR UPDATE", exploration_id)
            existing = await conn.fetch(
                "SELECT source_id AS id FROM edges "
                "WHERE target_id = $1 AND type = 'atom' ORDER BY source_id",
                exploration_id)
            if existing:
                return [r["id"] for r in existing], True
            created = []
            for a in atoms:
                nid = await conn.fetchval(
                    "INSERT INTO nodes (text, author_id, kind, topic_root_id, "
                    "atom_group) VALUES ($1, $2, $3, $4, $5) RETURNING id",
                    a["text"], author_id, a["kind"], root_id, a["group"])
                await conn.execute(
                    "INSERT INTO node_topics (node_id, topic_root_id, author_id) "
                    "VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
                    nid, root_id, author_id)
                await _log(conn, "node_added",
                           {"node_id": nid, "text": a["text"], "kind": a["kind"],
                            "position_id": None, "poi_score": None,
                            "topic_root_id": root_id, "atom_group": a["group"]},
                           author_id)
                edge_id = await conn.fetchval(
                    "INSERT INTO edges (source_id, target_id, type) "
                    "VALUES ($1, $2, 'atom') RETURNING id", nid, exploration_id)
                await _log(conn, "edge_added",
                           {"edge_id": edge_id, "source_id": nid,
                            "target_id": exploration_id, "edge_type": "atom"})
                created.append(nid)
            return created, False


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
        row = await conn.fetchrow(
            "SELECT * FROM nodes WHERE id = $1 AND deleted_at IS NULL", node_id)
    return dict(row) if row else None


# ------------------------------------------------- правило часа (edit-delete-window)
async def _node_replied(conn, node_id, is_root, author_id):
    """Есть ли на узле чужой след, из-за которого текст уже не принадлежит автору.

    Три разных сигнала, и каждого достаточно:
      • ЛЮБОЙ ответ, включая собственный ответ автора — продолжив ветку, автор
        сам сделал этот текст опорой для следующего;
      • ЧУЖАЯ реакция — это уже работа другого человека по оценке;
      • ЧУЖОЕ belonging-ребро — кто-то принёс узел под свою проблему, и текст
        стал частью чужого рассуждения.
    Домашнее ребро и собственные реакции автора не в счёт: это его же след.
    """
    if await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM edges WHERE target_id = $1)", node_id):
        return "на него уже ответили"
    if await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM reactions "
            "WHERE node_id = $1 AND author_id IS DISTINCT FROM $2)",
            node_id, author_id):
        return "на него уже отреагировали"
    if await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM node_topics "
            "WHERE node_id = $1 AND author_id IS DISTINCT FROM $2)",
            node_id, author_id):
        return "его уже положили под другую проблему"
    if is_root and await conn.fetchval(
            """
            SELECT EXISTS (SELECT 1 FROM nodes
                           WHERE topic_root_id = $1 AND id <> $1
                             AND deleted_at IS NULL)
                OR EXISTS (SELECT 1 FROM node_topics
                           WHERE topic_root_id = $1 AND node_id <> $1)
            """, node_id):
        return "в теме уже есть доводы"
    return None


async def _removable_node(conn, node_id, author_id):
    """Взять узел под замок и проверить, вправе ли автор его ещё снять.

    Вызывать ТОЛЬКО внутри транзакции: FOR UPDATE держит строку до конца, иначе
    ответ, пришедший ровно в момент удаления, проскочил бы мимо проверки.
    """
    row = await conn.fetchrow(
        "SELECT * FROM nodes WHERE id = $1 FOR UPDATE", node_id)
    if row is None or row["deleted_at"] is not None:
        raise Locked("узла не существует")
    if row["author_id"] is None or row["author_id"] != author_id:
        raise Locked("снять высказывание может только его автор")
    age = datetime.now(timezone.utc) - row["created_at"]
    if age > EDIT_WINDOW:
        raise Locked("прошёл час — высказывание зафиксировано; "
                     "его можно отозвать или дополнить примечанием")
    reason = await _node_replied(conn, node_id, row["id"] == row["topic_root_id"],
                                 author_id)
    if reason:
        raise Locked(f"{reason} — текст уже держит чужое рассуждение")
    return dict(row)


def removable_until(created_at):
    """Момент, до которого высказывание ещё можно снять — UI показывает остаток."""
    return created_at + EDIT_WINDOW if created_at else None


async def node_removability(node_id, author_id):
    """Может ли этот человек ещё снять узел, и если нет — почему.
    Читающая версия проверки: для UI, без блокировки строки. Отзыв и примечание
    ею не управляются — они автору доступны всегда."""
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        try:
            row = await conn.fetchrow(
                "SELECT * FROM nodes WHERE id = $1 AND deleted_at IS NULL", node_id)
            if row is None:
                raise Locked("узла не существует")
            if author_id is None or row["author_id"] != author_id:
                raise Locked("снять высказывание может только его автор")
            if datetime.now(timezone.utc) - row["created_at"] > EDIT_WINDOW:
                raise Locked("прошёл час — высказывание зафиксировано; "
                             "его можно отозвать или дополнить примечанием")
            reason = await _node_replied(
                conn, node_id, row["id"] == row["topic_root_id"], author_id)
            if reason:
                raise Locked(f"{reason} — текст уже держит чужое рассуждение")
        except Locked as e:
            return {"can_remove": False, "reason": str(e),
                    "removable_until": removable_until(
                        row["created_at"] if row else None)}
    return {"can_remove": True, "reason": None,
            "removable_until": removable_until(row["created_at"])}


# Правки текста после публикации НЕТ и не будет (решение 2026-08-14): вся работа
# над формулировкой происходит ДО отправки, в диалоге с ИИ-компаньоном. Опубликованное
# слово неизменно — на нём строят ответы, его хеш уходит в цепочку. Автору остаются
# три действия: снять (внутри часа, пока не ответили), отозвать (всегда) и приписать
# примечание (всегда).


async def retract_node(node_id, author_id, note=None):
    """«Больше не настаиваю» — отзыв высказывания его автором.

    Отличие от снятия: узел ОСТАЁТСЯ виден вместе со всеми ответами на него, но
    помечен как отозванный. Иначе передумавший автор уносил бы с собой чужие
    возражения, а признание собственной неправоты — самое ценное, что бывает в
    споре, и прятать его незачем.

    Доступен всегда: срок и наличие ответов тут ни при чём — это не изменение
    сказанного, а сообщение о своём нынешнем отношении к сказанному.
    """
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT author_id, deleted_at, retracted_at FROM nodes "
                "WHERE id = $1 FOR UPDATE", node_id)
            if row is None or row["deleted_at"] is not None:
                raise Locked("узла не существует")
            if row["author_id"] != author_id:
                raise Locked("отозвать высказывание может только его автор")
            if row["retracted_at"] is not None:
                raise Locked("высказывание уже отозвано")
            await conn.execute(
                "UPDATE nodes SET retracted_at = now(), retract_note = $2 "
                "WHERE id = $1", node_id, (note or "").strip() or None)
            await _log(conn, "node_retracted",
                       {"node_id": node_id, "note": note}, author_id)
            updated = await conn.fetchrow(
                "SELECT * FROM nodes WHERE id = $1", node_id)
    return dict(updated)


async def soft_delete_node(node_id, author_id):
    """Снять своё высказывание внутри окна. Строка остаётся — для выдач её нет."""
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await _removable_node(conn, node_id, author_id)
            await conn.execute(
                "UPDATE nodes SET deleted_at = now() WHERE id = $1", node_id)
            # тема уходит из чужих рабочих деревьев: подборка не должна
            # показывать то, чего в графе больше нет
            if row["id"] == row["topic_root_id"]:
                await conn.execute(
                    "DELETE FROM workspace WHERE topic_root_id = $1", node_id)
            await _log(conn, "node_deleted",
                       {"node_id": node_id, "text": row["text"],
                        "kind": row["kind"],
                        "was_root": row["id"] == row["topic_root_id"]},
                       author_id)
    return True


async def add_addendum(node_id, author_id, text):
    """Датированная приписка к своему зафиксированному высказыванию.

    Работает ПОСЛЕ окна и только для автора: исходный текст неприкосновенен (на
    него отвечали, его хеш уходит в цепочку), но сказать «здесь я ошибся» автор
    вправе всегда. Приписка ничего не переписывает и не трогает оценку.
    """
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT author_id, deleted_at FROM nodes WHERE id = $1", node_id)
            if row is None or row["deleted_at"] is not None:
                raise Locked("узла не существует")
            if row["author_id"] != author_id:
                raise Locked("примечание к своему высказыванию добавляет только автор")
            added = await conn.fetchrow(
                "INSERT INTO node_addenda (node_id, author_id, text) "
                "VALUES ($1, $2, $3) RETURNING *", node_id, author_id, text)
            await _log(conn, "node_addendum_added",
                       {"node_id": node_id, "addendum_id": added["id"],
                        "text": text}, author_id)
    return _row_with_iso(added, "created_at")


async def node_addenda(node_id):
    """Примечания автора к узлу, старые сверху — читаются как хронология уточнений."""
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT ad.id, ad.text, ad.created_at, a.name AS author, a.color AS author_color
            FROM node_addenda ad
            LEFT JOIN authors a ON a.id = ad.author_id
            WHERE ad.node_id = $1 ORDER BY ad.id
            """, node_id)
    return [_row_with_iso(r, "created_at") for r in rows]


def _row_with_iso(row, *fields):
    d = dict(row)
    for f in fields:
        if d.get(f) is not None:
            d[f] = d[f].isoformat()
    return d


async def get_node_full(node_id):
    """One node with author info and parsed breakdown — the detail-panel read."""
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT n.*, a.name AS author, a.color AS author_color,
                   a.is_service AS author_is_service,
                   (a.username IS NULL AND NOT a.is_service) AS author_is_seed,
                   p.headline AS position_headline,
                   p.composed AS position_composed,
                   p.stance   AS position_stance,
                   (SELECT count(*) FROM edges e WHERE e.target_id = n.id) AS reply_count
            FROM nodes n
            LEFT JOIN authors a ON a.id = n.author_id
            LEFT JOIN positions p ON p.id = n.position_id
            WHERE n.id = $1 AND n.deleted_at IS NULL
            """, node_id)
    if row is None:
        return None
    d = dict(row)
    if d.get("poi_breakdown"):
        d["poi_breakdown"] = json.loads(d["poi_breakdown"])
    return d


async def get_children(node_id, limit=20, offset=0):
    """
    A PAGE of a node's children (replies), oldest first. This — not get_graph —
    is the read path the tree UI uses: the client only ever asks for the slice
    it is looking at.

    Порядок ХРОНОЛОГИЧЕСКИЙ, а не по PoI. На взаимоисключающих предложениях
    сортировка по числу читается как ответ («вот это победило»), и никакая
    подсказка рядом этого не переигрывает — читатель видит первый пункт списка.
    Время нейтрально: оно говорит только «это написали раньше».
    """
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        total = await conn.fetchval(
            "SELECT count(*) FROM edges e JOIN nodes n ON n.id = e.source_id "
            "WHERE e.target_id = $1 AND n.deleted_at IS NULL", node_id)
        rows = await conn.fetch(
            """
            SELECT n.id, n.text, n.poi_score, n.kind, n.atom_group, e.type AS rel,
                   n.retracted_at, n.retract_note,
                   e.anchor_start, e.anchor_end, e.anchor_quote, n.author_id,
                   a.name AS author, a.color AS author_color,
                   a.is_service AS author_is_service,
                   (a.username IS NULL AND NOT a.is_service) AS author_is_seed,
                   (SELECT count(*) FROM edges e2 WHERE e2.target_id = n.id) AS reply_count
            FROM edges e
            JOIN nodes n ON n.id = e.source_id
            LEFT JOIN authors a ON a.id = n.author_id
            WHERE e.target_id = $1 AND n.deleted_at IS NULL
            ORDER BY n.created_at, n.id
            LIMIT $2 OFFSET $3
            """, node_id, limit, offset)
    return {"total": total, "children": [dict(r) for r in rows]}


# ---------------------------------------------------------------- authors
# public author shape — password_hash never leaves the DB layer
_AUTHOR_COLS = "id, name, color, username, created_at, dialogue_poi"


async def list_authors():
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        rows = await conn.fetch(f"SELECT {_AUTHOR_COLS} FROM authors ORDER BY id")
    return [dict(r) for r in rows]


async def add_author(name, color=None):
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        async with conn.transaction():
            author_id = await conn.fetchval(
                "INSERT INTO authors (name, color) VALUES ($1, $2) RETURNING id",
                name, color)
            await _log(conn, "author_added", {"name": name}, author_id)
    return author_id


async def author_api_key(author_id):
    """Deliberately NOT part of _AUTHOR_COLS: those columns go out over the
    public GET /api/authors, and a key there would leak every tester's."""
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT api_key FROM authors WHERE id = $1", author_id)


async def get_author(author_id):
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT {_AUTHOR_COLS} FROM authors WHERE id = $1", author_id)
    return dict(row) if row else None


# ---------------------------------------------------------------- accounts
async def create_password_reset(email, token_hash, ttl_hours=48):
    """Bind a reset token to whoever owns this email. None if nobody does —
    the caller must not reveal which case it was."""
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        author_id = await conn.fetchval(
            "SELECT id FROM authors WHERE lower(email) = lower($1)", email)
        if author_id is None:
            return None
        await conn.execute(
            "INSERT INTO password_resets (token_hash, author_id, expires_at) "
            "VALUES ($1, $2, now() + ($3 || ' hours')::interval)",
            token_hash, author_id, str(ttl_hours))
        return author_id


async def redeem_password_reset(token_hash, new_password_hash):
    """Spend the token and set the new password, atomically. False if the
    token is unknown, expired or already used."""
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT author_id FROM password_resets WHERE token_hash = $1 "
                "AND used_at IS NULL AND expires_at > now() FOR UPDATE",
                token_hash)
            if row is None:
                return False
            await conn.execute(
                "UPDATE authors SET password_hash = $1 WHERE id = $2",
                new_password_hash, row["author_id"])
            await conn.execute(
                "UPDATE password_resets SET used_at = now() WHERE token_hash = $1",
                token_hash)
            # every existing session is invalidated: if the reset was a
            # recovery from someone else having the password, leaving their
            # session alive would defeat the whole exercise
            await conn.execute("DELETE FROM sessions WHERE author_id = $1",
                               row["author_id"])
            return True


async def create_email_verification(author_id, email, token_hash, ttl_hours=48):
    """Issue a confirmation token for this account's address."""
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO email_verifications "
            "(token_hash, author_id, email, expires_at) "
            "VALUES ($1, $2, $3, now() + ($4 || ' hours')::interval)",
            token_hash, author_id, email, str(ttl_hours))
        return True


async def change_password(author_id, new_hash, keep_token=None):
    """Сменить пароль вошедшего. Остальные сессии закрываются.

    Смена пароля — это чаще всего «кажется, меня взломали». Оставить чужой
    сессии доступ значило бы сделать её бессмысленной, поэтому живой остаётся
    только та, из которой пароль меняли.
    """
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "UPDATE authors SET password_hash = $1 WHERE id = $2",
                new_hash, author_id)
            await conn.execute(
                "DELETE FROM sessions WHERE author_id = $1 "
                "AND ($2::text IS NULL OR token <> $2)", author_id, keep_token)
            await _log(conn, "password_changed", {}, author_id)
    return True


async def set_author_name(author_id, name):
    """Сменить отображаемое имя. Логин (username) не трогаем: он публичный
    идентификатор, на него ссылаются адреса профилей и упоминания."""
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "UPDATE authors SET name = $2 WHERE id = $1 RETURNING name",
                author_id, name)
            await _log(conn, "author_renamed", {"name": name}, author_id)
    return row["name"] if row else None


async def email_taken_by_other(email, author_id):
    """Занят ли адрес ЧУЖИМ аккаунтом. Свой же адрес занятым не считается —
    иначе повторная отправка подтверждения на него выглядела бы как конфликт."""
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        return bool(await conn.fetchval(
            "SELECT 1 FROM authors WHERE lower(email) = lower($1) AND id <> $2",
            email, author_id))


async def pending_email_change(author_id):
    """Адрес, на который человек уже запросил смену и ещё не подтвердил.
    Нужен кабинету, чтобы сказать «ждём подтверждения на …», а не молчать."""
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            """
            SELECT v.email FROM email_verifications v
            JOIN authors a ON a.id = v.author_id
            WHERE v.author_id = $1 AND v.used_at IS NULL
              AND v.expires_at > now()
              AND lower(v.email) <> lower(coalesce(a.email, ''))
            ORDER BY v.created_at DESC LIMIT 1
            """, author_id)


async def redeem_email_verification(token_hash):
    """Spend the token and mark the address confirmed. Returns the author id,
    or None if the token is unknown, expired or already used.

    The address is re-checked against the account rather than trusted from the
    token row: between issuing and clicking, the person may have changed their
    address, and confirming the old one would mark the new one proven.
    """
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT author_id, email FROM email_verifications "
                "WHERE token_hash = $1 AND used_at IS NULL "
                "AND expires_at > now() FOR UPDATE", token_hash)
            if row is None:
                # A second click on the same link is the common case, not an
                # attack: people double-click, and a used token whose account
                # is already confirmed should read as success. Anything else —
                # unknown or expired token, or an account still unconfirmed —
                # stays a failure.
                return await conn.fetchval(
                    "SELECT a.id FROM email_verifications v "
                    "JOIN authors a ON a.id = v.author_id "
                    "WHERE v.token_hash = $1 AND a.email_verified", token_hash)
            await conn.execute(
                "UPDATE email_verifications SET used_at = now() "
                "WHERE token_hash = $1", token_hash)
            # Адрес из токена СТАНОВИТСЯ адресом аккаунта. Так одна и та же
            # ссылка закрывает два случая: подтверждение своего адреса при
            # регистрации и смену почты — там в токене лежит новый адрес, и
            # именно переход по письму на него делает смену состоявшейся.
            # Занятость чужим аккаунтом проверена при выдаче токена, но между
            # выдачей и кликом адрес мог занять кто-то ещё — тогда UPDATE
            # упрётся в уникальный индекс, и смена честно не состоится.
            try:
                ok = await conn.fetchval(
                    "UPDATE authors SET email = $2, email_verified = TRUE "
                    "WHERE id = $1 RETURNING id",
                    row["author_id"], row["email"])
            except asyncpg.UniqueViolationError:
                return None
            # Остальные висящие токены этого аккаунта относятся к прежнему
            # состоянию: неиспользованная ссылка с регистрации после смены
            # адреса выглядела бы в кабинете как «ждём подтверждения» старого
            # адреса, а перейдя по ней, человек откатил бы смену назад.
            await conn.execute(
                "UPDATE email_verifications SET used_at = now() "
                "WHERE author_id = $1 AND used_at IS NULL "
                "AND token_hash <> $2", row["author_id"], token_hash)
            return ok


async def create_balance_request(author_id, note=None):
    """Ask for a starting balance. False if this account already has an open
    request — the partial unique index is what enforces it, so two clicks
    racing cannot produce two letters."""
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        try:
            return await conn.fetchval(
                "INSERT INTO balance_requests (author_id, note) "
                "VALUES ($1, $2) RETURNING id", author_id, note)
        except asyncpg.UniqueViolationError:
            return False


async def open_balance_request(author_id):
    """The account's unanswered request, if any. Used to show the button as
    'already asked' rather than letting someone click into an error."""
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, created_at, note FROM balance_requests "
            "WHERE author_id = $1 AND resolved_at IS NULL", author_id)
    return dict(row) if row else None


async def list_balance_requests(include_resolved=False):
    """The queue Alex works through. Carries enough of the person with it to
    decide without opening another page: how long they have been here, whether
    the address is confirmed, and how much they have actually written."""
    pool = _pool_or_raise()
    where = "" if include_resolved else "WHERE r.resolved_at IS NULL"
    async with pool.acquire() as conn:
        rows = await conn.fetch(f"""
            SELECT r.id, r.author_id, r.note, r.created_at, r.resolved_at,
                   r.granted_usd,
                   a.username, a.name, a.email, a.email_verified,
                   a.balance_usd, a.created_at AS registered_at,
                   a.dialogue_poi,
                   (SELECT count(*) FROM nodes n WHERE n.author_id = a.id
                    AND n.deleted_at IS NULL) AS nodes_count
            FROM balance_requests r
            JOIN authors a ON a.id = r.author_id
            {where}
            ORDER BY r.created_at
        """)
    return [dict(r) for r in rows]


async def resolve_balance_request(author_id, granted_usd):
    """Close whatever request this account has open. Separate from set_balance
    on purpose: granting money and answering a request are two facts, and a
    top-up made for another reason should not silently close a request."""
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "UPDATE balance_requests SET resolved_at = now(), granted_usd = $2 "
            "WHERE author_id = $1 AND resolved_at IS NULL RETURNING id",
            author_id, granted_usd) is not None


async def set_email(author_id, email):
    """Attach or change an account's email. False if another account has it.

    Changing the address drops email_verified: the new one is unproven, and
    carrying the old flag over would let anyone move a confirmed account onto
    an address they merely typed.
    """
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        try:
            return await conn.fetchval(
                "UPDATE authors SET email = $1, "
                "email_verified = (lower(email) = lower($1)) "
                "WHERE id = $2 RETURNING id",
                email, author_id) is not None
        except asyncpg.UniqueViolationError:
            return False


async def accounts_without_email():
    """Who still can't recover their account."""
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, username, name FROM authors "
            "WHERE username IS NOT NULL AND email IS NULL ORDER BY id")
        return [dict(r) for r in rows]


async def budget_left(author_id):
    """USD still available to this account: granted minus everything spent.

    Deliberately not account_summary(): this runs before every LLM call, and
    the cabinet's extra counters are dead weight on that path.
    """
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT a.balance_usd
                   - COALESCE((SELECT SUM(cost_usd) FROM usage_events u
                               WHERE u.author_id = a.id), 0) AS left_usd
            FROM authors a WHERE a.id = $1
            """, author_id)
    # No such author: refuse rather than wave through. A missing row here can
    # only mean a session outliving its account, which is not a reason to
    # start spending the shared key.
    return float(row["left_usd"]) if row else 0.0


async def shared_key_spend():
    """Сколько всего потрачено с общего ключа инстанса, $.

    Считается по всем аккаунтам сразу: по-аккаунтный грант ограничивает одного
    тестера, а этот счётчик — сумму, которая уходит с одной карты. Гоняется
    перед каждым LLM-вызовом, но таблица на порядок мельче тысяч строк, и SUM
    по ней дешевле, чем риск проснуться с пустым ключом.
    """
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        return float(await conn.fetchval(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM usage_events "
            "WHERE shared_key"))


async def add_user(username, password_hash, name, color=None, invite=None,
                   email=None, balance_usd=0, invite_required=True,
                   terms_version=None, invite_balance_usd=None):
    """Register: an account is an author with credentials.

    Returns the new author id, or a string error tag: "taken" (username in
    use), "email_taken" (email already registered) or "invite" (code missing,
    unknown or already spent). The invite is claimed inside the same
    transaction as the INSERT, so two people racing on one code cannot both
    get an account.

    With invite_required false the code is ignored entirely, not merely made
    optional: once anyone can register, a code grants nothing, and validating
    one would only turn a stale link into a confusing refusal. What keeps an
    open door from costing money is the zero balance — a new account can read
    and write, but spends nothing on the LLM until Alex tops it up by hand.
    """
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = None
            if invite_required:
                # SELECT ... FOR UPDATE: the row is locked until this
                # transaction ends, so the second racer blocks here and then
                # sees used_at set.
                row = await conn.fetchrow(
                    "SELECT code, used_at FROM invites WHERE code = $1 "
                    "FOR UPDATE", (invite or "").strip())
                if row is None or row["used_at"] is not None:
                    return "invite"

            # Пришёл по коду — грант инвайтовый. Проверка кода прошла выше,
            # row не None ровно тогда, когда код настоящий и не потрачен.
            if row is not None and invite_balance_usd is not None:
                balance_usd = invite_balance_usd
            try:
                author_id = await conn.fetchval(
                    """
                    INSERT INTO authors (name, color, username, password_hash,
                                         email, balance_usd,
                                         terms_accepted_at, terms_version)
                    VALUES ($1, $2, $3, $4, $5, $6,
                            CASE WHEN $7::text IS NULL THEN NULL ELSE now() END, $7)
                    ON CONFLICT (username) DO NOTHING
                    RETURNING id
                    """, name, color, username, password_hash, email,
                    balance_usd, terms_version)
            except asyncpg.UniqueViolationError:
                return "email_taken"      # the lower(email) unique index
            if author_id is None:
                return "taken"

            if row is not None:
                await conn.execute(
                    "UPDATE invites SET used_by = $1, used_at = now() "
                    "WHERE code = $2", author_id, row["code"])
            await _log(conn, "author_added",
                       {"name": name, "username": username,
                        "invite": row["code"] if row is not None else None,
                        "open": row is None,
                        # согласие уходит и в append-only лог: строку в authors
                        # можно потом стереть по требованию об удалении данных,
                        # а факт принятия условий должен пережить это
                        "terms_version": terms_version},
                       author_id)
    return author_id


async def add_invite(code, note=None):
    """Mint an invite code. False if that code already exists."""
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "INSERT INTO invites (code, note) VALUES ($1, $2) "
            "ON CONFLICT (code) DO NOTHING RETURNING code", code, note) is not None


async def list_invites():
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT i.code, i.note, i.created_at, i.used_at, a.username AS used_by "
            "FROM invites i LEFT JOIN authors a ON a.id = i.used_by "
            "ORDER BY i.created_at DESC")
        return [dict(r) for r in rows]


# ---- сколько нас: регистрации и присутствие.
#
# Служебные аккаунты (is_service) и посевные персоны (username IS NULL, войти
# нельзя) в счёт не идут: «зарегистрировано» должно отвечать на вопрос «сколько
# живых людей завело здесь учётную запись», а не «сколько строк в authors».
_REAL_USER = "a.username IS NOT NULL AND NOT a.is_service"


async def stats_public():
    """Две цифры, которые не выдают ничего личного: сколько людей завело
    аккаунт и сколько из них здесь прямо сейчас. Отдаётся открыто (см.
    GET /api/stats) — публичный счётчик присутствия и есть смысл затеи."""
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            SELECT
              (SELECT COUNT(*) FROM authors a WHERE {_REAL_USER}) AS registered,
              (SELECT COUNT(DISTINCT s.author_id)
                 FROM sessions s JOIN authors a ON a.id = s.author_id
                WHERE {_REAL_USER} AND s.last_seen > now() - $1::interval) AS online
            """, ONLINE_WINDOW)
    return {"registered": row["registered"], "online": row["online"],
            "online_window_min": int(ONLINE_WINDOW.total_seconds() // 60)}


async def stats_admin():
    """Полная сводка для страницы /stats.html — под ADMIN_TOKEN.

    Здесь можно больше, чем в публичной: имена тех, кто сейчас онлайн, свежие
    регистрации, остаток инвайтов. Один вызов вместо пяти — страница обновляется
    по таймеру, и каждый лишний round-trip умножается на частоту опроса.
    """
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        users = await conn.fetchrow(
            f"""
            SELECT
              COUNT(*)                                              AS registered,
              COUNT(*) FILTER (WHERE a.email_verified)              AS verified,
              COUNT(*) FILTER (WHERE a.created_at > now() - interval '24 hours') AS new_24h,
              COUNT(*) FILTER (WHERE a.created_at > now() - interval '7 days')   AS new_7d,
              COUNT(*) FILTER (WHERE a.dialogue_poi IS NOT NULL)    AS with_poi
            FROM authors a WHERE {_REAL_USER}
            """)
        presence = await conn.fetchrow(
            f"""
            SELECT
              COUNT(DISTINCT s.author_id) FILTER (
                WHERE s.last_seen > now() - $1::interval)             AS online,
              COUNT(DISTINCT s.author_id) FILTER (
                WHERE s.last_seen > now() - interval '24 hours')      AS active_24h,
              COUNT(DISTINCT s.author_id) FILTER (
                WHERE s.last_seen > now() - interval '7 days')        AS active_7d,
              COUNT(*)                                                AS live_sessions
            FROM sessions s JOIN authors a ON a.id = s.author_id
            WHERE {_REAL_USER} AND s.expires_at >= now()
            """, ONLINE_WINDOW)
        online_now = await conn.fetch(
            f"""
            SELECT a.id, a.username, a.name, MAX(s.last_seen) AS last_seen
            FROM sessions s JOIN authors a ON a.id = s.author_id
            WHERE {_REAL_USER} AND s.last_seen > now() - $1::interval
            GROUP BY a.id, a.username, a.name
            ORDER BY MAX(s.last_seen) DESC
            """, ONLINE_WINDOW)
        invites = await conn.fetchrow(
            """
            SELECT COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE used_at IS NOT NULL) AS used,
                   COUNT(*) FILTER (WHERE used_at IS NULL)     AS free
            FROM invites
            """)
        content = await conn.fetchrow(
            """
            SELECT COUNT(*)                                            AS nodes,
                   COUNT(*) FILTER (WHERE kind = 'argument')           AS arguments,
                   COUNT(*) FILTER (WHERE kind = 'question')           AS questions,
                   COUNT(*) FILTER (WHERE id = topic_root_id)          AS topics,
                   COUNT(*) FILTER (WHERE created_at > now() - interval '24 hours')
                                                                       AS nodes_24h
            FROM nodes WHERE deleted_at IS NULL
            """)
        dialogues = await conn.fetchrow(
            """
            SELECT COUNT(*)                                     AS started,
                   COUNT(*) FILTER (WHERE phase = 'results')    AS finished
            FROM dialogues
            """)
        # ВСЕ профили одной строкой каждый: кто, когда завёл, был ли здесь,
        # что написал и сколько денег сжёг на ИИ. Одна таблица, а не две
        # («свежие» + «расходы»): человек — это и есть его строка, и сводить
        # их глазами по логину значило бы делать работу за читателя.
        #
        # Подзапросами, а не JOIN-ами: три независимых агрегата (сессии, узлы,
        # траты) в одном GROUP BY перемножились бы друг на друга и раздули
        # суммы. На масштабах PoC это дешевле и, главное, не врёт.
        profiles = await conn.fetch(
            f"""
            SELECT a.id, a.username, a.name, a.created_at,
                   a.email, a.email_verified, a.dialogue_poi,
                   a.balance_usd            AS granted,
                   a.api_key IS NOT NULL    AS own_key,
                   (SELECT MAX(s.last_seen) FROM sessions s
                     WHERE s.author_id = a.id)                    AS last_seen,
                   (SELECT COUNT(*) FROM nodes n
                     WHERE n.author_id = a.id AND n.deleted_at IS NULL) AS nodes,
                   (SELECT COALESCE(SUM(u.cost_usd), 0) FROM usage_events u
                     WHERE u.author_id = a.id)                    AS spent,
                   (SELECT COALESCE(SUM(u.cost_usd), 0) FROM usage_events u
                     WHERE u.author_id = a.id
                       AND u.created_at > now() - interval '24 hours') AS spent_24h,
                   (SELECT COUNT(*) FROM usage_events u
                     WHERE u.author_id = a.id)                    AS calls,
                   (SELECT MAX(u.created_at) FROM usage_events u
                     WHERE u.author_id = a.id)                    AS last_call
            FROM authors a WHERE {_REAL_USER}
            ORDER BY a.created_at DESC
            """)
    rows = []
    for r in profiles:
        granted, spent_usd = float(r["granted"] or 0), float(r["spent"] or 0)
        rows.append({
            "id": r["id"], "username": r["username"], "name": r["name"],
            "created_at": r["created_at"], "email": r["email"],
            "email_verified": r["email_verified"],
            "dialogue_poi": (round(float(r["dialogue_poi"]), 1)
                             if r["dialogue_poi"] is not None else None),
            "last_seen": r["last_seen"], "nodes": r["nodes"],
            "granted_usd": round(granted, 4),
            "spent_usd": round(spent_usd, 6),
            "spent_24h_usd": round(float(r["spent_24h"] or 0), 6),
            "left_usd": round(granted - spent_usd, 4),
            "calls": r["calls"], "own_key": r["own_key"],
            "last_call": r["last_call"],
        })
    return {
        "users": dict(users),
        "presence": dict(presence),
        "online_window_min": int(ONLINE_WINDOW.total_seconds() // 60),
        "online_now": [dict(r) for r in online_now],
        "profiles": rows,
        "invites": dict(invites),
        "content": dict(content),
        "dialogues": dict(dialogues),
        "spend_total": {
            "granted_usd": round(sum(r["granted_usd"] for r in rows), 4),
            "spent_usd": round(sum(r["spent_usd"] for r in rows), 6),
            "spent_24h_usd": round(sum(r["spent_24h_usd"] for r in rows), 6),
            "calls": sum(r["calls"] for r in rows),
        },
    }


async def record_usage(author_id, rows, endpoint=None):
    """Write one usage_event per LLM call made during a request."""
    if not rows:
        return
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        await conn.executemany(
            "INSERT INTO usage_events (author_id, model, input_tokens, "
            "output_tokens, cache_read_tokens, cache_write_tokens, "
            "cost_usd, endpoint, shared_key) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,"
            # Чей ключ платил — берём здесь, а не из contextvar: middleware,
            # которая вызывает record_usage, крутится в своей задаче и
            # current_api_key, выставленный в spend_llm внутри хендлера, до неё
            # не долетает.
            " (SELECT api_key IS NULL FROM authors WHERE id = $1))",
            [(author_id, r["model"], r["input_tokens"], r["output_tokens"],
              r.get("cache_read_tokens", 0), r.get("cache_write_tokens", 0),
              r["cost_usd"], endpoint) for r in rows])


# ---------------------------------------------------------------- decisions
async def topic_material(topic_root_id):
    """
    Everything block B is rendered from, ordered in SQL so the render is
    byte-stable across calls (app/material.py explains why that matters).
    """
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        positions = await conn.fetch(
            "SELECT id, headline, composed, stance FROM positions "
            "WHERE topic_root_id = $1 ORDER BY id", topic_root_id)
        questions = await conn.fetch(
            "SELECT id, text FROM nodes WHERE topic_root_id = $1 "
            "AND kind = 'question' AND atom_group IS NULL AND deleted_at IS NULL "
            "ORDER BY id",
            topic_root_id)
        atoms = await conn.fetch(
            "SELECT id, text, atom_group FROM nodes WHERE topic_root_id = $1 "
            "AND atom_group IS NOT NULL AND deleted_at IS NULL "
            "ORDER BY atom_group, id", topic_root_id)
        dissents = await conn.fetch(
            "SELECT id, text FROM nodes WHERE topic_root_id = $1 "
            "AND dissented AND deleted_at IS NULL ORDER BY id", topic_root_id)
    return {
        "positions": [dict(r) for r in positions],
        "questions": [dict(r) for r in questions],
        "atoms": [dict(r) for r in atoms],
        "dissents": [dict(r) for r in dissents],
    }


async def create_decision(topic_root_id, question, created_by=None,
                          closes_at=None):
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "INSERT INTO decisions (topic_root_id, question, created_by, "
                "closes_at) VALUES ($1,$2,$3,$4) RETURNING *",
                topic_root_id, question, created_by, closes_at)
            await _log(conn, "decision_created",
                       {"decision_id": row["id"], "topic_root_id": topic_root_id,
                        "question": question}, created_by)
    return dict(row)


async def open_decision(decision_id, snapshot, judge_model):
    """
    Freeze the material and the judge model, then open for votes.

    Both are frozen for the same reason: everyone in a revision must be judged
    against the same debate by the same judge, or their weights are not
    comparable with each other (see the decisions table comment).
    """
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "UPDATE decisions SET status = 'open', material_snapshot = $1, "
                "judge_model = $2, opens_at = now() "
                "WHERE id = $3 AND status = 'draft' RETURNING *",
                snapshot, judge_model, decision_id)
            if row is None:
                return None
            await conn.execute(
                "INSERT INTO decision_revisions (decision_id, revision, "
                "material_snapshot) VALUES ($1, 1, $2)", decision_id, snapshot)
            await _log(conn, "decision_opened",
                       {"decision_id": decision_id, "revision": 1,
                        "judge_model": judge_model})
    return dict(row)


async def get_decision(decision_id):
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM decisions WHERE id = $1", decision_id)
    return dict(row) if row else None


async def list_decisions(status=None, viewer_id=None):
    """Список голосований для экрана участника: вопрос, тема, счётчики.

    Голоса и варианты считаем подзапросами, чтобы одно голосование давало одну
    строку. Приватность черновиков: open/closed видны всем, а draft — только
    своему автору (viewer_id). status=None ('all') = не-черновики + свои черновики.
    topic_text обрезан — списку нужен только заголовок, полный текст лишний вес.
    """
    pool = _pool_or_raise()
    if status == "draft":
        if viewer_id is None:
            return []
        where, args = "WHERE d.status = 'draft' AND d.created_by = $1", [viewer_id]
    elif status in ("open", "closed"):
        where, args = "WHERE d.status = $1", [status]
    else:  # all
        where = "WHERE (d.status <> 'draft' OR d.created_by = $1)"
        args = [viewer_id if viewer_id is not None else -1]
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT d.id, d.topic_root_id, d.question, d.status, "
            "       d.opens_at, d.closes_at, d.created_at, d.created_by, "
            "       t.title AS topic_title, left(t.text, 200) AS topic_text, "
            "       (SELECT COUNT(*) FROM decision_options o "
            "          WHERE o.decision_id = d.id) AS options, "
            "       (SELECT COUNT(DISTINCT v.author_id) FROM votes v "
            "          WHERE v.decision_id = d.id) AS voters "
            "FROM decisions d "
            "LEFT JOIN nodes t ON t.id = d.topic_root_id "
            f"{where} "
            "ORDER BY d.opens_at DESC NULLS LAST, d.created_at DESC",
            *args)
    return [dict(r) for r in rows]


async def count_recent_decisions(author_id, hours=1):
    """Сколько голосований автор создал за последние N часов — для анти-спама."""
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT COUNT(*) FROM decisions "
            "WHERE created_by = $1 AND created_at > now() - ($2 || ' hours')::interval",
            author_id, str(hours))


async def close_decision(decision_id):
    """Автор закрывает открытое голосование — приём голосов прекращается."""
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE decisions SET status = 'closed', closes_at = now() "
            "WHERE id = $1 AND status = 'open' RETURNING *", decision_id)
    return dict(row) if row else None


async def delete_decision_if_untouched(decision_id):
    """Удалить голосование, только если его никто не тронул: ни одного голоса и
    ни одного начатого разбора-диалога. Как только есть голос или подготовка
    (человек потратил на неё усилия/деньги) — это уже участие, не удаляем, только
    закрываем. Черновики (0 всего) удаляются свободно. Каскад снимает варианты и
    ревизии."""
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "DELETE FROM decisions d WHERE d.id = $1 "
            "AND NOT EXISTS (SELECT 1 FROM votes v WHERE v.decision_id = d.id) "
            "AND NOT EXISTS (SELECT 1 FROM vote_dialogues vd "
            "                  WHERE vd.decision_id = d.id) "
            "RETURNING id", decision_id)
    return row is not None


async def current_revision(decision_id):
    """The revision a voter starting now is judged against."""
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM decision_revisions WHERE decision_id = $1 "
            "ORDER BY revision DESC LIMIT 1", decision_id)
    return dict(row) if row else None


async def list_options(decision_id):
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            # имя предложившего — чтобы чужой вариант в бюллетене был
            # подписан: он стоит рядом с вариантами автора и не должен
            # выглядеть как один из них
            "SELECT o.*, p.headline, p.composed, p.stance, "
            "       a.name AS proposed_by_name "
            "FROM decision_options o "
            "LEFT JOIN positions p ON p.id = o.position_id "
            "LEFT JOIN authors a ON a.id = o.proposed_by "
            "WHERE o.decision_id = $1 ORDER BY o.sort, o.id", decision_id)
    return [dict(r) for r in rows]


async def add_option(decision_id, position_id=None, label=None,
                     origin="initial", proposed_by=None, revision=1):
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "INSERT INTO decision_options (decision_id, position_id, label, "
                "origin, proposed_by, revision) VALUES ($1,$2,$3,$4,$5,$6) "
                "RETURNING *",
                decision_id, position_id, label, origin, proposed_by, revision)
            await _log(conn, "decision_option_added",
                       {"decision_id": decision_id, "option_id": row["id"],
                        "origin": origin}, proposed_by)
    return dict(row)


async def open_revision(decision_id, snapshot, opened_by_option=None):
    """
    Start a new revision: the debate moved enough that later voters see
    different material. Earlier votes keep their own revision number and are
    never retro-fitted — the shift between revisions IS the finding.
    """
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        async with conn.transaction():
            nxt = await conn.fetchval(
                "SELECT COALESCE(MAX(revision), 0) + 1 FROM decision_revisions "
                "WHERE decision_id = $1", decision_id)
            row = await conn.fetchrow(
                "INSERT INTO decision_revisions (decision_id, revision, "
                "material_snapshot, opened_by_option) VALUES ($1,$2,$3,$4) "
                "RETURNING *", decision_id, nxt, snapshot, opened_by_option)
            await conn.execute(
                "UPDATE decisions SET material_snapshot = $1 WHERE id = $2",
                snapshot, decision_id)
            await _log(conn, "decision_revision_opened",
                       {"decision_id": decision_id, "revision": nxt,
                        "opened_by_option": opened_by_option})
    return dict(row)


async def get_vote_dialogue(decision_id, author_id):
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM vote_dialogues WHERE decision_id = $1 AND "
            "author_id = $2", decision_id, author_id)
    if row is None:
        return None
    d = dict(row)
    d["transcript"] = json.loads(d["transcript"]) if isinstance(
        d["transcript"], str) else d["transcript"]
    d["criteria"] = json.loads(d["criteria"]) if isinstance(
        d["criteria"], str) else d["criteria"]
    return d


async def start_vote_dialogue(decision_id, author_id, revision, transcript):
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "INSERT INTO vote_dialogues (decision_id, author_id, revision, "
                "transcript) VALUES ($1,$2,$3,$4) "
                "ON CONFLICT (decision_id, author_id) DO NOTHING RETURNING *",
                decision_id, author_id, revision, json.dumps(transcript))
            if row is not None:
                await _log(conn, "vote_dialogue_started",
                           {"decision_id": decision_id, "revision": revision},
                           author_id)
    return await get_vote_dialogue(decision_id, author_id)


async def save_vote_transcript(decision_id, author_id, transcript):
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE vote_dialogues SET transcript = $1 WHERE decision_id = $2 "
            "AND author_id = $3", json.dumps(transcript), decision_id, author_id)


async def finish_vote_dialogue(decision_id, author_id, score, criteria,
                               summary, weight):
    """
    Re-finalisable: the person may keep talking and finalise again, and the
    judge re-reads the whole transcript. Votes already cast keep the weight
    they were cast with (votes.weight is a snapshot) — a later re-score must
    not silently re-weight a vote that is already counted.
    """
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "UPDATE vote_dialogues SET score = $1, criteria = $2, "
                "summary = $3, weight = $4, finished_at = now() "
                "WHERE decision_id = $5 AND author_id = $6",
                score, json.dumps(criteria), summary, weight,
                decision_id, author_id)
            await _log(conn, "vote_dialogue_finished",
                       {"decision_id": decision_id, "score": score,
                        "weight": weight}, author_id)
    return await get_vote_dialogue(decision_id, author_id)


async def cast_vote(decision_id, author_id, option_ids, weight, revision,
                    vote_dialogue_id=None):
    """
    Approval-shaped: the person's whole selection is replaced atomically, so
    re-voting before the deadline is a normal act rather than a duplicate.
    """
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM votes WHERE decision_id = $1 AND author_id = $2",
                decision_id, author_id)
            for oid in option_ids:
                await conn.execute(
                    "INSERT INTO votes (decision_id, author_id, option_id, "
                    "weight, revision, vote_dialogue_id) "
                    "VALUES ($1,$2,$3,$4,$5,$6)",
                    decision_id, author_id, oid, weight, revision,
                    vote_dialogue_id)
            await _log(conn, "vote_cast",
                       {"decision_id": decision_id, "option_ids": option_ids,
                        "weight": weight, "revision": revision}, author_id)


async def tally(decision_id):
    """
    Both pictures, always: by heads and by weight.

    The gap between them is the most informative thing this produces — it shows
    whether the people who did the work disagree with the majority, and by how
    much. Reporting only one of the two hides exactly that.

    Reframes are returned but never summed into the answers: they reject the
    question rather than answer it (see the decision_options comment).
    """
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT o.id, o.label, o.origin, o.position_id, o.revision, "
            "       p.headline, "
            "       COUNT(v.author_id)                AS heads, "
            "       COALESCE(SUM(v.weight), 0)        AS weighted "
            "FROM decision_options o "
            "LEFT JOIN votes v ON v.option_id = o.id "
            "LEFT JOIN positions p ON p.id = o.position_id "
            "WHERE o.decision_id = $1 "
            "GROUP BY o.id, o.label, o.origin, o.position_id, o.revision, "
            "         p.headline "
            "ORDER BY o.sort, o.id", decision_id)
        voters = await conn.fetchrow(
            "SELECT COUNT(DISTINCT author_id) AS n, "
            "       COALESCE(AVG(weight), 0)  AS avg_weight "
            "FROM (SELECT DISTINCT author_id, weight FROM votes "
            "      WHERE decision_id = $1) s", decision_id)
        by_rev = await conn.fetch(
            "SELECT revision, COUNT(DISTINCT author_id) AS voters "
            "FROM votes WHERE decision_id = $1 GROUP BY revision "
            "ORDER BY revision", decision_id)
    opts = [dict(r) for r in rows]
    return {
        "answers": [o for o in opts if o["origin"] != "reframe"],
        "reframes": [o for o in opts if o["origin"] == "reframe"],
        "voters": voters["n"] if voters else 0,
        "avg_weight": float(voters["avg_weight"]) if voters else 0.0,
        "by_revision": [dict(r) for r in by_rev],
    }


async def account_summary(author_id):
    """Everything the cabinet shows about money: granted, spent, left."""
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
              a.balance_usd AS granted,
              a.api_key IS NOT NULL AS has_key,
              COALESCE((SELECT SUM(cost_usd) FROM usage_events u
                        WHERE u.author_id = a.id), 0) AS spent,
              COALESCE((SELECT COUNT(*) FROM usage_events u
                        WHERE u.author_id = a.id), 0) AS calls
            FROM authors a WHERE a.id = $1
            """, author_id)
    if row is None:
        return None
    granted, spent = float(row["granted"]), float(row["spent"])
    return {"granted_usd": round(granted, 4),
            "spent_usd": round(spent, 6),
            "left_usd": round(granted - spent, 4),
            "calls": row["calls"],
            "has_key": row["has_key"]}


async def activity_summary(author_id):
    """What the participant has actually contributed to the graph."""
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
              COUNT(*)                                     AS nodes,
              COUNT(*) FILTER (WHERE kind = 'argument')    AS arguments,
              COUNT(*) FILTER (WHERE kind = 'question')    AS questions,
              AVG(poi_score) FILTER (WHERE poi_score IS NOT NULL) AS avg_poi,
              MIN(created_at)                              AS first_at,
              MAX(created_at)                              AS last_at
            FROM nodes WHERE author_id = $1 AND deleted_at IS NULL
            """, author_id)
    d = dict(row) if row else {}
    if d.get("avg_poi") is not None:
        d["avg_poi"] = round(float(d["avg_poi"]), 1)
    return d


async def author_votes(author_id):
    """Публичная история голосований аккаунта: за что и как голосовал, с каким
    весом, в каком голосовании, и «почему» (резюме диалога-обоснования). Это
    материал, из которого люди сами строят транзакционную репутацию — система
    ничего не оценивает, только показывает след. Сгруппировано по голосованию
    (approval: один голос может отметить несколько опций)."""
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT v.decision_id, d.question, d.status, d.topic_root_id,
                   v.weight, v.revision, v.cast_at,
                   COALESCE(o.label, p.headline) AS option_label, o.origin,
                   vd.summary AS why, vd.score AS dialogue_score
            FROM votes v
            JOIN decisions d ON d.id = v.decision_id
            JOIN decision_options o ON o.id = v.option_id
            LEFT JOIN positions p ON p.id = o.position_id
            LEFT JOIN vote_dialogues vd ON vd.id = v.vote_dialogue_id
            WHERE v.author_id = $1
            ORDER BY v.cast_at DESC NULLS LAST, v.decision_id DESC
            """, author_id)
    out, by_dec = [], {}
    for r in rows:
        d = by_dec.get(r["decision_id"])
        if d is None:
            d = {"decision_id": r["decision_id"], "question": r["question"],
                 "status": r["status"], "topic_root_id": r["topic_root_id"],
                 "weight": r["weight"], "revision": r["revision"],
                 "cast_at": r["cast_at"].isoformat() if r["cast_at"] else None,
                 "why": r["why"], "dialogue_score": r["dialogue_score"],
                 "options": []}
            by_dec[r["decision_id"]] = d
            out.append(d)
        d["options"].append({"label": r["option_label"], "origin": r["origin"]})
    return out


async def author_activity(author_id):
    """Полная текстовая активность аккаунта: что писал и в каких обсуждениях,
    какие темы/проблемы создавал. Сгруппировано по обсуждению; внутри — вклады
    автора (тезисы, ответы, вопросы, атрибуции) с типом связи и PoI текста.
    Материал транзакционной репутации наравне с историей голосований."""
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT n.id, n.text, n.title, n.kind, n.poi_score, n.created_at,
                   n.topic_root_id, (n.id = n.topic_root_id) AS is_root,
                   r.title AS topic_title, r.text AS topic_text, r.kind AS topic_kind,
                   (SELECT e.type FROM edges e WHERE e.source_id = n.id LIMIT 1) AS rel
            FROM nodes n
            JOIN nodes r ON r.id = n.topic_root_id AND r.deleted_at IS NULL
            WHERE n.author_id = $1 AND n.atom_group IS NULL
              AND n.deleted_at IS NULL
            ORDER BY n.created_at DESC
            """, author_id)
    groups, order = {}, []
    for r in rows:
        t = r["topic_root_id"]
        g = groups.get(t)
        if g is None:
            g = {"topic_root_id": t,
                 "topic_title": r["topic_title"]
                     or (r["topic_text"][:60] if r["topic_text"] else "#" + str(t)),
                 "topic_kind": r["topic_kind"],
                 "created": False, "items": []}
            groups[t] = g
            order.append(t)
        if r["is_root"]:
            g["created"] = True     # автор создал это обсуждение (написал корень)
        g["items"].append({
            "id": r["id"], "text": r["text"], "kind": r["kind"],
            "is_root": r["is_root"], "rel": r["rel"],
            "poi_score": r["poi_score"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        })
    return [groups[t] for t in order]


async def set_balance(author_id, amount_usd):
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "UPDATE authors SET balance_usd = $1 WHERE id = $2 RETURNING id",
            amount_usd, author_id) is not None


async def set_api_key(author_id, api_key):
    """Attach this tester's own Anthropic key to their account."""
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "UPDATE authors SET api_key = $1 WHERE id = $2 RETURNING id",
            api_key, author_id) is not None


async def get_author_by_username(username):
    """SELECT * — includes password_hash, email, balance and api_key. Only for
    the login path, which checks the password and then filters. Never hand the
    result of this to a public endpoint: use public_author_by_username."""
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM authors WHERE username = $1", username)
    return dict(row) if row else None


async def public_author_by_username(username):
    """The public face of an account, addressed by its permanent name.

    Deliberately built on _AUTHOR_COLS rather than filtering SELECT *: a column
    added to authors later (another address, a payout account, anything) then
    has to be named explicitly to become public, instead of leaking the moment
    it exists. Lower() so /u/Ivan and /u/ivan are the same person.
    """
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT {_AUTHOR_COLS} FROM authors WHERE lower(username) = lower($1)",
            username)
    return dict(row) if row else None


async def create_session(token, author_id, days=30):
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO sessions (token, author_id, expires_at) "
            "VALUES ($1, $2, now() + make_interval(days => $3))",
            token, author_id, days)


async def session_author(token):
    """The author behind a live session token, or None (expired ones purged).

    Returns more than the public _AUTHOR_COLS shape: this is the person's own
    account, and the request handlers need email_verified to decide whether
    publishing is allowed. These extra columns must not be echoed into a public
    response — that is why they are added here and not to _AUTHOR_COLS, which
    goes out over the open GET /api/authors.

    Also stamps last_seen: this function runs on every authenticated request, so
    it is the one place that knows someone is still there. The write is throttled
    to once a minute per session (TOUCH_EVERY) — «онлайн» с точностью до минуты
    стоит ровно ничего, а UPDATE на каждый запрос превратил бы чтение графа в
    поток записей. Обе операции одним round-trip: CTE обновляет, SELECT читает.
    """
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM sessions WHERE expires_at < now()")
        row = await conn.fetchrow(
            f"""
            WITH touched AS (
                UPDATE sessions SET last_seen = now()
                WHERE token = $1 AND expires_at >= now()
                  AND (last_seen IS NULL OR last_seen < now() - $2::interval)
                RETURNING author_id
            )
            SELECT {', '.join('a.' + c.strip() for c in _AUTHOR_COLS.split(','))},
                   a.email, a.email_verified, a.is_service
            FROM sessions s
            JOIN authors a ON a.id = s.author_id
            WHERE s.token = $1 AND s.expires_at >= now()
            """, token, TOUCH_EVERY)
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


async def list_dialogues():
    """All dialogues with their author, newest first (dev admin viewer)."""
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT a.id AS author_id, a.username, a.name, a.dialogue_poi,
                   d.topic, d.phase, jsonb_array_length(d.turns) AS n_turns,
                   d.updated_at
            FROM dialogues d JOIN authors a ON a.id = d.author_id
            ORDER BY d.updated_at DESC
            """)
    out = []
    for row in rows:
        r = dict(row)
        if r.get("updated_at") is not None:
            r["updated_at"] = r["updated_at"].isoformat()
        out.append(r)
    return out


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
                  AND deleted_at IS NULL
                UNION
                SELECT topic_root_id FROM author_topic_poi WHERE author_id = $1
            ) t
            """, author_id)
    return [r["topic_root_id"] for r in rows]


# ---------------------------------------------------------------- per-topic PoI
async def topic_poi_of(author_id, topic_root_id):
    """
    The author's CURRENT stored topic PoI (explicit prior/dialogue score/default
    10 if no computed row yet). This is the scalar FROZEN into a reaction's
    reactor_weight at the moment it is cast — see the reactions table comment.
    """
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            """
            SELECT COALESCE(tp.poi, a.dialogue_poi, 10)
            FROM authors a
            LEFT JOIN author_topic_poi tp
                   ON tp.author_id = a.id AND tp.topic_root_id = $2
            WHERE a.id = $1
            """, author_id, topic_root_id)


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
            "WHERE author_id = $1 AND topic_root_id = $2 AND poi_score IS NOT NULL "
            "AND deleted_at IS NULL",
            author_id, topic_root_id)
        # reactions on the author's nodes in this topic, self-reactions excluded,
        # each weighted by the reactor's FROZEN weight at cast time (reactor_weight)
        # — NOT the reactor's live PoI. This is what makes the result independent
        # of when this recompute runs relative to other authors' PoI changes.
        reacts = await conn.fetch(
            """
            SELECT COALESCE(r.reactor_weight, 10) AS reactor_poi, r.stance
            FROM reactions r
            JOIN nodes n ON n.id = r.node_id AND n.deleted_at IS NULL
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
    """
    Each PARTICIPANT's current PoI in the topic — authors who contributed a node
    here or already have a per-topic PoI row. Previously this returned EVERY
    account in the DB defaulted to 10, so every topic dragged the whole user
    table (and the client filtered lurkers out itself); the filter belongs here.
    """
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
            WHERE tp.author_id IS NOT NULL
               OR EXISTS (SELECT 1 FROM nodes n
                          WHERE n.author_id = a.id AND n.topic_root_id = $1
                            AND n.deleted_at IS NULL)
            ORDER BY a.id
            """, topic_root_id)
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- reactions
async def set_reaction(author_id, node_id, stance, reactor_weight=None):
    """
    reactor_weight: the reactor's topic PoI FROZEN at cast time (computed by the
    caller via topic_poi_of). Persisted and logged so the reaction's weight in
    the accrual formula never shifts afterwards. Changing one's stance re-freezes
    at the current weight — flipping a vote is a fresh act of reacting.
    """
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO reactions (author_id, node_id, stance, reactor_weight)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (author_id, node_id) DO UPDATE
                    SET stance = EXCLUDED.stance,
                        reactor_weight = EXCLUDED.reactor_weight
                """, author_id, node_id, stance, reactor_weight)
            # the log keeps EVERY vote change; the table keeps only the latest
            await _log(conn, "reaction_set",
                       {"node_id": node_id, "stance": stance,
                        "reactor_weight": reactor_weight}, author_id)


async def get_reactions(node_id, topic_root_id):
    # poi here is the reactor's FROZEN weight (what the accrual formula actually
    # counts), so the reactor histogram shown on node-open matches the mechanic
    # rather than the reactor's drifting live PoI.
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT r.author_id, a.name, a.color, r.stance,
                   COALESCE(r.reactor_weight, 10) AS poi
            FROM reactions r
            JOIN authors a ON a.id = r.author_id
            WHERE r.node_id = $1
            ORDER BY r.author_id
            """, node_id)
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


async def mark_dissented(node_id):
    """Pin an argument as the author's own position — never auto-merge it again."""
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "UPDATE nodes SET dissented = TRUE WHERE id = $1", node_id)
            await _log(conn, "node_dissented", {"node_id": node_id})


async def delete_position(position_id):
    """Remove an empty/left-behind pool; detach any nodes still pointing at it."""
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "UPDATE nodes SET position_id = NULL WHERE position_id = $1", position_id)
            await conn.execute("DELETE FROM positions WHERE id = $1", position_id)
            await _log(conn, "position_deleted", {"position_id": position_id})


async def add_position(topic_root_id, headline, composed, stance, author_id=None):
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        async with conn.transaction():
            pid = await conn.fetchval(
                "INSERT INTO positions (topic_root_id, headline, composed, stance, "
                "author_id) VALUES ($1, $2, $3, $4, $5) RETURNING id",
                topic_root_id, headline, composed, stance, author_id)
            await _log(conn, "position_added",
                       {"position_id": pid, "topic_root_id": topic_root_id,
                        "headline": headline, "stance": stance,
                        "author_id": author_id})
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
            "SELECT * FROM nodes WHERE position_id = $1 AND kind = $2 "
            "AND deleted_at IS NULL ORDER BY id",
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
            "AND deleted_at IS NULL ORDER BY id", position_id)
    return [dict(r) for r in rows]


async def topic_argument_nodes(topic_root_id):
    """
    A topic's argument nodes (atoms excluded) — the clustering input, read
    straight from the materialized topic_root_id instead of BFS-walking the
    whole graph. Avoids loading every node in the DB on a re-cluster, and can't
    accidentally pull in another topic through a stray edge.
    """
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM nodes WHERE topic_root_id = $1 "
            "AND (kind = 'argument' OR kind IS NULL) AND atom_group IS NULL "
            "AND deleted_at IS NULL ORDER BY id", topic_root_id)
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
                FROM nodes n WHERE n.id = $1 AND n.deleted_at IS NULL
                UNION ALL
                SELECT n.id, n.text, n.kind,
                       e.target_id, e.type, down.depth + 1
                FROM down
                JOIN edges e ON e.target_id = down.id
                JOIN nodes n ON n.id = e.source_id AND n.deleted_at IS NULL
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
                   COALESCE(tp.poi, a.dialogue_poi, 10) AS poi
            FROM position_reactions pr
            JOIN authors a ON a.id = pr.author_id
            LEFT JOIN author_topic_poi tp
                   ON tp.author_id = pr.author_id AND tp.topic_root_id = $1
            WHERE pr.position_id = $2
            """, topic_root_id, position_id)
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- edges / graph
async def add_edge(source_id, target_id, edge_type, anchor_hash=None,
                   anchor_start=None, anchor_end=None, anchor_quote=None):
    """Ребро source→target. Опциональный якорь привязывает его к УЧАСТКУ текста
    цели (хеш версии + смещения + цитата); без якоря ребро относится к узлу целиком."""
    if edge_type not in EDGE_TYPES:
        raise ValueError(f"edge type must be one of {EDGE_TYPES}")
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        async with conn.transaction():
            edge_id = await conn.fetchval(
                "INSERT INTO edges (source_id, target_id, type, anchor_hash, "
                "anchor_start, anchor_end, anchor_quote) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING id",
                source_id, target_id, edge_type, anchor_hash,
                anchor_start, anchor_end, anchor_quote)
            await _log(conn, "edge_added",
                       {"edge_id": edge_id, "source_id": source_id,
                        "target_id": target_id, "edge_type": edge_type,
                        "anchor_start": anchor_start, "anchor_end": anchor_end,
                        "anchor_quote": anchor_quote, "anchor_hash": anchor_hash})
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
            SELECT n.*, a.name AS author, a.color AS author_color
            FROM nodes n
            LEFT JOIN authors a ON a.id = n.author_id
            WHERE n.deleted_at IS NULL
            ORDER BY n.id
            """)
        edge_rows = await conn.fetch(
            "SELECT e.* FROM edges e "
            "JOIN nodes s ON s.id = e.source_id AND s.deleted_at IS NULL "
            "JOIN nodes t ON t.id = e.target_id AND t.deleted_at IS NULL")
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


async def workspace_add(author_id, topic_root_id):
    """Положить тему в рабочее дерево. Идемпотентно — повторный клик не ошибка."""
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO workspace (author_id, topic_root_id) VALUES ($1, $2)
            ON CONFLICT DO NOTHING
            """, author_id, topic_root_id)


async def workspace_remove(author_id, topic_root_id):
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM workspace WHERE author_id = $1 AND topic_root_id = $2",
            author_id, topic_root_id)


async def workspace_ids(author_id):
    """Только идентификаторы — карте нужно лишь знать, что уже добавлено."""
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT topic_root_id FROM workspace WHERE author_id = $1", author_id)
    return [r["topic_root_id"] for r in rows]


async def workspace_topics(author_id):
    """Темы рабочего дерева — той же формы, что list_topics, плюс added_at.

    Свежие сверху: сортировка по id ставила бы наверх самые старые темы, а
    внизу экрана оказывалось бы то, где спор идёт прямо сейчас.
    """
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT n.id, n.text, n.title, n.poi_score, n.kind, n.author_id,
                   n.retracted_at, n.retract_note,
                   a.name AS author, a.color AS author_color,
                   a.is_service AS author_is_service,
                   (a.username IS NULL AND NOT a.is_service) AS author_is_seed,
                   w.added_at,
                   (SELECT count(*) FROM edges e2
                    JOIN nodes cn ON cn.id = e2.source_id
                    WHERE e2.target_id = n.id AND cn.deleted_at IS NULL) AS reply_count,
                   (SELECT max(c.created_at) FROM nodes c
                    WHERE c.topic_root_id = n.id AND c.deleted_at IS NULL) AS last_at
            FROM workspace w
            JOIN nodes n ON n.id = w.topic_root_id
            LEFT JOIN authors a ON a.id = n.author_id
            WHERE w.author_id = $1 AND n.deleted_at IS NULL
            ORDER BY COALESCE((SELECT max(c.created_at) FROM nodes c
                               WHERE c.topic_root_id = n.id AND c.deleted_at IS NULL),
                              w.added_at) DESC
            """, author_id)
    return [dict(r) for r in rows]


async def seed_workspace_once(author_id, limit=5):
    """Первый вход: положить в подборку несколько самых живых тем.

    Один раз за всё время аккаунта — флаг снимается сразу и в той же
    транзакции, поэтому вычищенная подборка не зарастает обратно.
    """
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        async with conn.transaction():
            seeded = await conn.fetchval(
                "SELECT workspace_seeded FROM authors WHERE id = $1 FOR UPDATE",
                author_id)
            if seeded is None or seeded:
                return []
            rows = await conn.fetch(
                """
                SELECT n.id
                FROM nodes n
                WHERE n.id = n.topic_root_id AND n.deleted_at IS NULL
                ORDER BY (SELECT count(*) FROM nodes c
                          WHERE c.topic_root_id = n.id
                            AND c.deleted_at IS NULL) DESC, n.id DESC
                LIMIT $1
                """, limit)
            ids = [r["id"] for r in rows]
            if ids:
                await conn.executemany(
                    """
                    INSERT INTO workspace (author_id, topic_root_id) VALUES ($1, $2)
                    ON CONFLICT DO NOTHING
                    """, [(author_id, i) for i in ids])
            await conn.execute(
                "UPDATE authors SET workspace_seeded = TRUE WHERE id = $1", author_id)
            return ids


async def set_topic_facets(topic_root_id, domain, sub, geo_closure, tags,
                           author_id=None, event=None):
    """Проставить теме рубрику. Идемпотентно: повторный вызов переписывает.

    Всё в одной транзакции — тема без географии или с половиной тегов из-за
    оборванной записи выглядела бы как осознанный выбор автора. Событие пишется
    здесь же, а не отдельным вызовом: лог обязан сходиться с состоянием.
    """
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        async with conn.transaction():
            if event:
                await _log(conn, event,
                           {"topic": topic_root_id, "domain": domain,
                            "sub": sub, "tags": tags}, author_id)
            await conn.execute(
                """
                INSERT INTO topic_facets (topic_root_id, domain, sub)
                VALUES ($1, $2, $3)
                ON CONFLICT (topic_root_id)
                DO UPDATE SET domain = EXCLUDED.domain, sub = EXCLUDED.sub
                """, topic_root_id, domain, sub)
            await conn.execute("DELETE FROM topic_geo WHERE topic_root_id = $1",
                               topic_root_id)
            if geo_closure:
                await conn.executemany(
                    "INSERT INTO topic_geo (topic_root_id, geo) VALUES ($1, $2)",
                    [(topic_root_id, g) for g in geo_closure])
            await conn.execute("DELETE FROM topic_tags WHERE topic_root_id = $1",
                               topic_root_id)
            if tags:
                await conn.executemany(
                    "INSERT INTO topic_tags (topic_root_id, tag) VALUES ($1, $2)",
                    [(topic_root_id, t) for t in tags])


async def map_topics():
    """Все темы с рубрикой и статистикой — то, из чего строится карта.

    Один запрос вместо N+1: агрегаты по гео и тегам собираются подзапросами,
    иначе на каждую тему уходило бы ещё два обращения.
    """
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT n.id, n.title, n.text, n.kind, n.poi_score,
                   f.domain, f.sub,
                   COALESCE((SELECT array_agg(g.geo ORDER BY g.geo)
                             FROM topic_geo g WHERE g.topic_root_id = n.id),
                            '{}') AS geo,
                   COALESCE((SELECT array_agg(t.tag ORDER BY t.tag)
                             FROM topic_tags t WHERE t.topic_root_id = n.id),
                            '{}') AS tags,
                   (SELECT count(*) FROM nodes c
                    WHERE c.topic_root_id = n.id AND c.deleted_at IS NULL) AS nodes,
                   (SELECT count(DISTINCT c.author_id) FROM nodes c
                    WHERE c.topic_root_id = n.id AND c.author_id IS NOT NULL
                      AND c.deleted_at IS NULL) AS people,
                   (SELECT avg(c.poi_score) FROM nodes c
                    WHERE c.topic_root_id = n.id AND c.poi_score IS NOT NULL
                      AND c.deleted_at IS NULL) AS avg_poi,
                   a.name AS author, a.color AS author_color
            FROM nodes n
            LEFT JOIN topic_facets f ON f.topic_root_id = n.id
            LEFT JOIN authors a ON a.id = n.author_id
            WHERE n.id = n.topic_root_id AND n.deleted_at IS NULL
            ORDER BY n.id
            """)
    out = []
    for r in rows:
        d = dict(r)
        d["geo"] = list(d["geo"] or [])
        d["tags"] = list(d["tags"] or [])
        d["avg_poi"] = round(float(d["avg_poi"]), 1) if d["avg_poi"] is not None else None
        out.append(d)
    return out


async def list_topics():
    """
    Top-level discussions = ROOT nodes: argument nodes that don't reply to
    anything (no outgoing edge). Each root is one topic / one folder in the tree.
    """
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT n.id, n.text, n.title, n.poi_score, n.kind, n.author_id,
                   n.retracted_at, n.retract_note,
                   a.name AS author, a.color AS author_color,
                   a.is_service AS author_is_service,
                   (a.username IS NULL AND NOT a.is_service) AS author_is_seed,
                   (SELECT count(*) FROM edges e2
                    JOIN nodes cn ON cn.id = e2.source_id
                    WHERE e2.target_id = n.id AND cn.deleted_at IS NULL) AS reply_count
            FROM nodes n
            LEFT JOIN authors a ON a.id = n.author_id
            WHERE n.kind IN
                  ('argument', 'question', 'proposal', 'exploration', 'problem')
              AND n.deleted_at IS NULL
              AND NOT EXISTS (SELECT 1 FROM edges e WHERE e.source_id = n.id)
            ORDER BY n.id
            """)
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- problems
async def set_problem(topic_root_id, causes=None, gap=None, scale_note=None,
                      scale_url=None, scale_excerpt=None,
                      scale_retrieved_at=None, author_id=None):
    """Проставить/переписать состояние проблемы (причины, масштаб). Идемпотентно.

    Масштаб — данные извне: ссылка живёт вместе с текстовой выдержкой, чтобы
    страница не осыпалась, когда источник исчезнет. Пишется в той же транзакции,
    что и событие: лог обязан сходиться с состоянием.
    """
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO problems (topic_root_id, causes, gap, scale_note,
                                      scale_url, scale_excerpt, scale_retrieved_at,
                                      updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, now())
                ON CONFLICT (topic_root_id) DO UPDATE SET
                    causes = EXCLUDED.causes,
                    gap = EXCLUDED.gap,
                    scale_note = EXCLUDED.scale_note,
                    scale_url = EXCLUDED.scale_url,
                    scale_excerpt = EXCLUDED.scale_excerpt,
                    scale_retrieved_at = EXCLUDED.scale_retrieved_at,
                    updated_at = now()
                """, topic_root_id, causes, gap, scale_note, scale_url,
                scale_excerpt, scale_retrieved_at)
            await _log(conn, "problem_set",
                       {"topic_root_id": topic_root_id, "causes": causes,
                        "scale_url": scale_url}, author_id)


async def get_problem(topic_root_id):
    """Состояние проблемы: авторская рамка (причины, масштаб) + сводка исходов
    из реестра. Сводка и есть «состояние»: у темы её нет, у проблемы есть."""
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM problems WHERE topic_root_id = $1", topic_root_id)
        totals = await conn.fetch(
            "SELECT outcome_kind, count(*) AS n FROM interventions "
            "WHERE topic_root_id = $1 GROUP BY outcome_kind", topic_root_id)
        scale = await conn.fetch(
            "SELECT * FROM problem_scale WHERE topic_root_id = $1 "
            "AND deleted_at IS NULL ORDER BY id", topic_root_id)
    d = dict(row) if row else {"topic_root_id": topic_root_id}
    d["scale"] = [_scale_dict(r) for r in scale]
    if d.get("scale_retrieved_at") is not None:
        d["scale_retrieved_at"] = d["scale_retrieved_at"].isoformat()
    if d.get("updated_at") is not None:
        d["updated_at"] = d["updated_at"].isoformat()
    d["outcomes"] = {r["outcome_kind"]: r["n"] for r in totals}
    d["interventions_total"] = sum(d["outcomes"].values())
    return d


def _scale_dict(row):
    d = dict(row)
    for k in ("retrieved_at", "created_at", "deleted_at"):
        if d.get(k) is not None:
            d[k] = d[k].isoformat()
    return d


async def add_scale_row(topic_root_id, region, figure, source_url=None,
                        source_excerpt=None, retrieved_at=None, author_id=None):
    """Строка масштаба: один регион — одна цифра — свой источник.

    Регистрируется как ФАКТ, наравне с записью реестра: спорят с ней в графе, а
    не правкой строки. Поэтому строку нельзя переписать, только снять и внести
    заново — иначе цифра меняется под уже написанными доводами.
    """
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                INSERT INTO problem_scale (topic_root_id, region, figure,
                    source_url, source_excerpt, retrieved_at, author_id)
                VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING *
                """, topic_root_id, region, figure, source_url, source_excerpt,
                retrieved_at, author_id)
            await _log(conn, "scale_add",
                       {"topic_root_id": topic_root_id, "region": region,
                        "figure": figure, "source_url": source_url}, author_id)
    return _scale_dict(row)


async def list_scale(topic_root_id):
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM problem_scale WHERE topic_root_id = $1 "
            "AND deleted_at IS NULL ORDER BY id", topic_root_id)
    return [_scale_dict(r) for r in rows]


async def delete_scale_row(row_id, author_id=None):
    """Мягкое снятие строки масштаба — как у узлов: строка остаётся в базе,
    из выдач исчезает, event log не рвётся."""
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "UPDATE problem_scale SET deleted_at = now() WHERE id = $1 "
                "AND deleted_at IS NULL RETURNING *", row_id)
            if row is None:
                return None
            await _log(conn, "scale_delete", {"id": row_id}, author_id)
    return _scale_dict(row)


async def suggest_problems(query, limit=5, exclude_id=None):
    """Соседние проблемы, похожие на черновик, — ПОДСКАЗКА при создании, не гейт и
    не слияние. Свободное создание остаётся: это лишь «может, вот эта уже есть?».
    С pg_trgm — триграммное сходство заголовка/текста; без него — совпадение
    значимых слов (ILIKE-фолбэк)."""
    q = " ".join((query or "").split()).strip()
    if len(q) < 3:
        return []
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        if _has_trgm:
            rows = await conn.fetch(
                """
                SELECT n.id, n.title, n.text,
                       GREATEST(similarity(coalesce(n.title,''), $1),
                                similarity(left(n.text, 240), $1)) AS sim,
                       (SELECT count(*) FROM node_topics nt
                        JOIN nodes cn ON cn.id = nt.node_id
                        WHERE nt.topic_root_id = n.id
                          AND cn.deleted_at IS NULL) AS nodes
                FROM nodes n
                WHERE n.kind = 'problem' AND n.id = n.topic_root_id
                  AND n.deleted_at IS NULL
                  AND ($2::int IS NULL OR n.id <> $2)
                ORDER BY sim DESC
                LIMIT $3
                """, q, exclude_id, limit * 3)
            out = []
            for r in rows:
                if float(r["sim"]) < 0.15:      # ниже — уже не «сосед», а шум
                    continue
                d = dict(r); d["sim"] = round(float(d["sim"]), 3); out.append(d)
            return out[:limit]
        # фолбэк без расширения: значимые слова (>3 букв), ранг по числу совпадений
        words = [w for w in q.lower().split() if len(w) > 3][:8]
        if not words:
            return []
        rows = await conn.fetch(
            """
            SELECT n.id, n.title, n.text,
                   (SELECT count(*) FROM node_topics nt
                    JOIN nodes cn ON cn.id = nt.node_id
                    WHERE nt.topic_root_id = n.id
                      AND cn.deleted_at IS NULL) AS nodes
            FROM nodes n
            WHERE n.kind = 'problem' AND n.id = n.topic_root_id
              AND n.deleted_at IS NULL
              AND ($1::int IS NULL OR n.id <> $1)
            """, exclude_id)
        scored = []
        for r in rows:
            hay = ((r["title"] or "") + " " + (r["text"] or "")).lower()
            hits = sum(1 for w in words if w in hay)
            if hits:
                d = dict(r); d["sim"] = round(hits / len(words), 3); scored.append(d)
        scored.sort(key=lambda d: d["sim"], reverse=True)
        return scored[:limit]


# ------------------------------------------------------------ interventions
async def add_intervention(topic_root_id, what, actor=None, geo=None,
                           when_text=None, outcome=None, outcome_kind="unclear",
                           conditions=None, source_url=None, source_excerpt=None,
                           source_retrieved_at=None, author_id=None):
    """Внести запись в реестр решений. Запись факта, не аргумент."""
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                INSERT INTO interventions
                    (topic_root_id, what, actor, geo, when_text, outcome,
                     outcome_kind, conditions, source_url, source_excerpt,
                     source_retrieved_at, author_id)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
                RETURNING *
                """, topic_root_id, what, actor, geo, when_text, outcome,
                outcome_kind, conditions, source_url, source_excerpt,
                source_retrieved_at, author_id)
            await _log(conn, "intervention_added",
                       {"intervention_id": row["id"],
                        "topic_root_id": topic_root_id, "geo": geo,
                        "outcome_kind": outcome_kind}, author_id)
    return _intervention_dict(row)


def _intervention_dict(row):
    d = dict(row)
    for f in ("source_retrieved_at", "created_at"):
        if d.get(f) is not None:
            d[f] = d[f].isoformat()
    return d


async def list_interventions(topic_root_id):
    """Реестр одной проблемы, старые сверху (порядок накопления читается как
    хронология попыток)."""
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT i.*, a.name AS author, a.color AS author_color
            FROM interventions i
            LEFT JOIN authors a ON a.id = i.author_id
            WHERE i.topic_root_id = $1 AND i.deleted_at IS NULL ORDER BY i.id
            """, topic_root_id)
    return [_intervention_dict(r) for r in rows]


async def get_intervention(intervention_id):
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM interventions WHERE id = $1 AND deleted_at IS NULL",
            intervention_id)
    return _intervention_dict(row) if row else None


# Правило часа для реестра — то же, что для узлов, и правки здесь тоже нет.
# Ответ на запись = атрибуция: как только по ней заявили причинную претензию
# («сработало благодаря X»), факт держит чужое рассуждение и снять его нельзя.
async def _removable_intervention(conn, intervention_id, author_id):
    row = await conn.fetchrow(
        "SELECT * FROM interventions WHERE id = $1 FOR UPDATE", intervention_id)
    if row is None or row["deleted_at"] is not None:
        raise Locked("записи реестра не существует")
    if row["author_id"] is None or row["author_id"] != author_id:
        raise Locked("снять запись может только её автор")
    if datetime.now(timezone.utc) - row["created_at"] > EDIT_WINDOW:
        raise Locked("прошёл час — запись зафиксирована, её можно отозвать")
    if await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM nodes "
            "WHERE intervention_id = $1 AND deleted_at IS NULL)", intervention_id):
        raise Locked("по записи уже заявлена атрибуция — "
                     "факт держит чужое рассуждение")
    return row


async def retract_intervention(intervention_id, author_id, note=None):
    """«Больше не ручаюсь за эту запись» — отзыв факта его автором.

    Запись остаётся в реестре вместе с атрибуциями о ней: провал, о котором
    сообщили и потом усомнились, сам по себе полезен читателю — но пометка
    обязана быть видна рядом с фактом, а не вместо него.
    """
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT author_id, deleted_at, retracted_at FROM interventions "
                "WHERE id = $1 FOR UPDATE", intervention_id)
            if row is None or row["deleted_at"] is not None:
                raise Locked("записи реестра не существует")
            if row["author_id"] != author_id:
                raise Locked("отозвать запись может только её автор")
            if row["retracted_at"] is not None:
                raise Locked("запись уже отозвана")
            updated = await conn.fetchrow(
                "UPDATE interventions SET retracted_at = now(), retract_note = $2 "
                "WHERE id = $1 RETURNING *",
                intervention_id, (note or "").strip() or None)
            await _log(conn, "intervention_retracted",
                       {"intervention_id": intervention_id, "note": note},
                       author_id)
    return _intervention_dict(updated)


async def soft_delete_intervention(intervention_id, author_id):
    """Снять свою запись реестра внутри окна. Строка остаётся, выдачи её не видят."""
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await _removable_intervention(conn, intervention_id, author_id)
            await conn.execute(
                "UPDATE interventions SET deleted_at = now() WHERE id = $1",
                intervention_id)
            await _log(conn, "intervention_deleted",
                       {"intervention_id": intervention_id,
                        "topic_root_id": row["topic_root_id"],
                        "what": row["what"]}, author_id)
    return True


async def intervention_attributions(intervention_id):
    """Атрибуции об этой записи реестра — узлы-претензии «сработало благодаря X»
    с числом ответов (плотность спора по каждой). По ним бьют обычными рёбрами.

    Порядок по времени, как и у детей узла: конкурирующие объяснения одного
    исхода — набор альтернатив, и сортировка по PoI читалась бы как ответ.
    """
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT n.id, n.text, n.poi_score, n.kind, n.created_at,
                   n.retracted_at, n.retract_note,
                   a.name AS author, a.color AS author_color,
                   (SELECT count(*) FROM edges e WHERE e.target_id = n.id) AS reply_count
            FROM nodes n
            LEFT JOIN authors a ON a.id = n.author_id
            WHERE n.intervention_id = $1 AND n.deleted_at IS NULL
            ORDER BY n.created_at, n.id
            """, intervention_id)
    out = []
    for r in rows:
        d = dict(r)
        if d.get("created_at") is not None:
            d["created_at"] = d["created_at"].isoformat()
        out.append(d)
    return out


# ------------------------------------------------------------ belonging (edges)
async def add_belonging(node_id, topic_root_id, author_id=None):
    """Поставить узел под ещё одну проблему — многодомность без копии-цитаты.

    Идемпотентно (повторное размещение не ошибка). author_id — кто ПОЛОЖИЛ связь,
    не обязательно автор узла: смысл ребра в том, что чужой довод можно принести
    под свою проблему, и видно, кто это сделал и когда. Возвращает True, если
    связь создана, False, если уже была.
    """
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        async with conn.transaction():
            created = await conn.fetchval(
                "INSERT INTO node_topics (node_id, topic_root_id, author_id) "
                "VALUES ($1, $2, $3) ON CONFLICT DO NOTHING RETURNING node_id",
                node_id, topic_root_id, author_id) is not None
            if created:
                await _log(conn, "belonging_added",
                           {"node_id": node_id, "topic_root_id": topic_root_id},
                           author_id)
    return created


async def remove_belonging(node_id, topic_root_id):
    """Снять ДОПОЛНИТЕЛЬНУЮ принадлежность. Домашнюю (== nodes.topic_root_id)
    снять нельзя: это первичная тема узла и ключ начисления PoI. Возвращает
    False, если пытались снять домашнюю или связи не было."""
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        home = await conn.fetchval(
            "SELECT topic_root_id FROM nodes WHERE id = $1", node_id)
        if home == topic_root_id:
            return False
        async with conn.transaction():
            deleted = await conn.fetchval(
                "DELETE FROM node_topics WHERE node_id = $1 AND topic_root_id = $2 "
                "RETURNING node_id", node_id, topic_root_id) is not None
            if deleted:
                await _log(conn, "belonging_removed",
                           {"node_id": node_id, "topic_root_id": topic_root_id})
    return deleted


async def node_topics_of(node_id):
    """Проблемы, под которыми стоит узел (с автором связи, временем и пометкой
    домашней). Домашняя — та, что совпадает с nodes.topic_root_id."""
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT nt.topic_root_id, nt.author_id, nt.created_at,
                   (nt.topic_root_id = n.topic_root_id) AS is_home,
                   r.title AS topic_title, r.text AS topic_text, r.kind AS topic_kind,
                   a.name AS placed_by
            FROM node_topics nt
            JOIN nodes n ON n.id = nt.node_id
            JOIN nodes r ON r.id = nt.topic_root_id AND r.deleted_at IS NULL
            LEFT JOIN authors a ON a.id = nt.author_id
            WHERE nt.node_id = $1
            ORDER BY is_home DESC, nt.created_at
            """, node_id)
    out = []
    for r in rows:
        d = dict(r)
        if d.get("created_at") is not None:
            d["created_at"] = d["created_at"].isoformat()
        out.append(d)
    return out


async def node_anchors(node_id):
    """Входящие рёбра, целящиеся в УЧАСТКИ текста этого узла — для маркеров на
    полях (маркер с числом, не подсветка всего). Каждое несёт участок, цитату,
    тип и флаг stale: текст узла разошёлся с хешем, к которому привязывались
    (сейчас текст неизменяем, поэтому stale=false, но механизм на месте)."""
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        target = await conn.fetchval("SELECT text FROM nodes WHERE id = $1", node_id)
        if target is None:
            return []
        rows = await conn.fetch(
            """
            SELECT e.id AS edge_id, e.source_id, e.type,
                   e.anchor_start, e.anchor_end, e.anchor_quote, e.anchor_hash,
                   a.name AS author, a.color AS author_color
            FROM edges e
            JOIN nodes sn ON sn.id = e.source_id AND sn.deleted_at IS NULL
            LEFT JOIN authors a ON a.id = sn.author_id
            WHERE e.target_id = $1 AND e.anchor_start IS NOT NULL
            ORDER BY e.anchor_start, e.id
            """, node_id)
    current = text_hash(target)
    out = []
    for r in rows:
        d = dict(r)
        d["stale"] = (d.pop("anchor_hash") != current)
        out.append(d)
    return out


async def topic_nodes(topic_root_id):
    """Узлы, принадлежащие проблеме ЧЕРЕЗ node_topics — включая многодомные,
    принесённые из других проблем. Отличается от scalar-фильтра `topic_root_id`:
    тот видит только «родные» узлы, этот — весь корпус, собранный под проблему."""
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT n.id, n.text, n.title, n.kind, n.poi_score,
                   n.retracted_at, n.retract_note,
                   (n.topic_root_id = $1) AS is_home,
                   a.name AS author, a.color AS author_color
            FROM node_topics nt
            JOIN nodes n ON n.id = nt.node_id
            LEFT JOIN authors a ON a.id = n.author_id
            WHERE nt.topic_root_id = $1 AND n.deleted_at IS NULL
            ORDER BY n.poi_score DESC NULLS LAST, n.id
            """, topic_root_id)
    return [dict(r) for r in rows]


# ------------------------------------------------- разговор с ИИ до публикации
async def add_companion_entry(author_id, kind, draft, result,
                              connect_to=None, root_kind=None, history=None):
    """Записать один ход разговора автора с ИИ вокруг черновика.

    Пишется на КАЖДОЕ обращение, а не только на последнее: интересен не итог, а
    путь — что ИИ сказал, что человек ответил, правил ли он текст после.

    Ошибка записи не должна ронять сам разговор, ради которого лог и ведётся,
    поэтому вызывающий оборачивает это в try/except.
    """
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "INSERT INTO companion_log "
            "(author_id, kind, connect_to, root_kind, draft, history, result) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING id",
            author_id, kind, connect_to, root_kind, draft,
            json.dumps(history or [], ensure_ascii=False),
            json.dumps(result or {}, ensure_ascii=False))


async def list_companion_log(limit=200, author_id=None):
    """Последние обращения к ИИ, новые сверху. Для наблюдения за закрытым тестом."""
    pool = _pool_or_raise()
    limit = max(1, min(1000, int(limit)))
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT c.id, c.created_at, c.kind, c.connect_to, c.root_kind,
                   c.draft, c.history, c.result,
                   c.author_id, a.username, a.name AS author
            FROM companion_log c
            LEFT JOIN authors a ON a.id = c.author_id
            WHERE ($1::int IS NULL OR c.author_id = $1)
            ORDER BY c.created_at DESC, c.id DESC
            LIMIT $2
            """, author_id, limit)
    out = []
    for r in rows:
        d = dict(r)
        for f in ("history", "result"):
            if isinstance(d[f], str):
                try:
                    d[f] = json.loads(d[f])
                except ValueError:
                    pass
        out.append(d)
    return out


async def companion_log_authors():
    """Кто вообще говорил с ИИ и сколько раз — оглавление для наблюдателя."""
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT c.author_id, a.username, a.name AS author,
                   count(*) AS entries, max(c.created_at) AS last_at
            FROM companion_log c
            LEFT JOIN authors a ON a.id = c.author_id
            GROUP BY c.author_id, a.username, a.name
            ORDER BY max(c.created_at) DESC
            """)
    return [dict(r) for r in rows]


async def topic_board(topic_root_id):
    """Доска обсуждения: ПРЕДЛОЖЕНИЯ и ВОПРОСЫ отдельными списками.

    Оба вида уже лежали в графе, но увидеть их можно было только развернув
    нужную ветку дерева. Накопитель (`interventions`) — про то, что УЖЕ
    пробовали в реальности, это факты; предложение — про то, что ещё только
    предлагают сделать, и места у него не было вовсе.

    Принадлежность берётся через `node_topics` (истина принадлежности), а не
    по скаляру `topic_root_id` — иначе довод, принесённый из другой проблемы,
    в доску не попадёт.

    Порядок — ПО ВРЕМЕНИ, а не по PoI. Предложения по одной проблеме
    взаимоисключающи, и сортировка числом читалась бы как «вот правильное»
    (то же решение, что и для детей узла: seed-problems-first-echelon).

    Атомы разбора (`atom_group`) исключены: они куски чужого текста, у них своя
    витрина внутри разбора.
    """
    pool = _pool_or_raise()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT n.id, n.text, n.kind, n.poi_score, n.created_at,
                   n.retracted_at, n.retract_note,
                   a.name AS author, a.color AS author_color,
                   a.is_service AS author_is_service,
                   (a.username IS NULL AND NOT a.is_service) AS author_is_seed,
                   (SELECT count(*) FROM edges e
                    JOIN nodes cn ON cn.id = e.source_id
                    WHERE e.target_id = n.id AND cn.deleted_at IS NULL)
                       AS reply_count,
                   (SELECT count(*) FROM reactions r
                    WHERE r.node_id = n.id AND r.stance = 'agree')
                       AS agree,
                   (SELECT count(*) FROM reactions r
                    WHERE r.node_id = n.id AND r.stance = 'disagree')
                       AS disagree
            FROM node_topics nt
            JOIN nodes n ON n.id = nt.node_id
            LEFT JOIN authors a ON a.id = n.author_id
            WHERE nt.topic_root_id = $1
              AND n.kind IN ('proposal', 'question')
              AND n.atom_group IS NULL
              AND n.deleted_at IS NULL
              AND n.id <> $1                 -- сам корень в доску не входит
            ORDER BY n.created_at, n.id
            """, topic_root_id)
    out = {"proposals": [], "questions": []}
    for r in rows:
        d = dict(r)
        key = "proposals" if d["kind"] == "proposal" else "questions"
        out[key].append(d)
    return out
