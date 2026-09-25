"""
Карта мнений: таблицы, журналы и счётчики (vault: decisions/2026-09-25-opinion-map).

Три слоя, как в ТЗ:
  1. ЖУРНАЛЫ — только дописываются: stance_log, exposures, node_joins,
     answer_acceptances, position_events. UPDATE / DELETE / TRUNCATE на них
     запрещены триггером базы, а не соглашением: соглашение нарушается одной
     строкой в спешке, триггер — нет. Каждая запись дублируется событием в
     `events`, а `events` сцеплены хэшами (db._log) — так журнал проверяем.
  2. ПРОИЗВОДНЫЕ — текущее состояние, обновляется в той же транзакции, что и
     запись журнала: current_stance, exposure_state, answer_state,
     position_nodes, position_leavers, cause_counts, position_top,
     position_stats. Их можно стереть и пересобрать из журналов.
  3. СНИМКИ — position_stats_daily, для «состава во времени».

Счётчики позиции меняются на каждое событие (±1), а не полным пересчётом.
Единственное исключение — «устояли»: он зависит от того, какие возражения
сейчас главные. Когда состав главных возражений позиции меняется, «устояли»
этой позиции пересчитывается запросом по её людям — объём ограничен одной
позицией, а не темой.

Порядок блокировок: сначала замок обсуждения (все записи карты внутри одного
обсуждения идут по очереди), затем замок цепочки событий в db._log. Хуки из
db.add_edge / set_node_position / add_addendum зовутся ДО их _log — иначе
порядок перевернётся и две транзакции смогут ждать друг друга.
"""

import json
from datetime import datetime, timedelta, timezone

from . import opinionmap as om

# ключ advisory-замка обсуждения: (TOPIC_LOCK, topic_root_id)
TOPIC_LOCK = 7002

JOURNALS = ("stance_log", "exposures", "node_joins", "answer_acceptances",
            "position_events", "events")

SCHEMA = [
    # Позиция живёт в той же таблице, что и раньше: к ней привязано голосование
    # (decision_options.position_id), и ID не должны расходиться. Добавляется
    # статус (см. opinionmap.next_status) и происхождение раскола/слияния.
    "ALTER TABLE positions ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'forming'",
    "ALTER TABLE positions ADD COLUMN IF NOT EXISTS summary TEXT",
    "ALTER TABLE positions ADD COLUMN IF NOT EXISTS split_from INTEGER",
    "ALTER TABLE positions ADD COLUMN IF NOT EXISTS merged_into INTEGER",
    "ALTER TABLE positions DROP CONSTRAINT IF EXISTS positions_status_check",
    """ALTER TABLE positions ADD CONSTRAINT positions_status_check CHECK
       (status IN ('forming','active','empty','merged','split'))""",
    # Вид примечания автора: обычное или «признаю ошибку». Узел остаётся в
    # графе, но выходит из состава позиций (макет: «больше не держит позицию»).
    "ALTER TABLE node_addenda ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'note'",
    # Хэш-цепочка журнала событий. Старые события остаются без хэша: цепочка
    # начинается с первого события после миграции (генезис), задним числом
    # журнал не переписывается.
    "ALTER TABLE events ADD COLUMN IF NOT EXISTS prev_hash TEXT",
    "ALTER TABLE events ADD COLUMN IF NOT EXISTS hash TEXT",
    "CREATE INDEX IF NOT EXISTS events_hash_idx ON events (id) WHERE hash IS NOT NULL",
    # Состав позиции — какие узлы её составляют, по обсуждению. Узел может
    # одновременно лежать под несколькими проблемами (node_topics).
    """
    CREATE TABLE IF NOT EXISTS position_nodes (
        position_id   INTEGER NOT NULL REFERENCES positions(id) ON DELETE CASCADE,
        node_id       INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
        topic_root_id INTEGER NOT NULL,
        added_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (position_id, node_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS position_nodes_node ON position_nodes (node_id)",
    # ---- журналы. Внешних ключей на authors и nodes нет намеренно: каскадное
    # удаление снесло бы строки журнала (и упёрлось бы в триггер). Удаление
    # аккаунта — переатрибуция, а не стирание (decisions/data-deletion-model).
    """
    CREATE TABLE IF NOT EXISTS position_events (
        id            BIGSERIAL PRIMARY KEY,
        topic_root_id INTEGER NOT NULL,
        position_id   INTEGER NOT NULL,
        kind          TEXT NOT NULL CHECK (kind IN ('create','status','merge',
                          'split','rename','member_add','member_remove')),
        payload       JSONB NOT NULL DEFAULT '{}'::jsonb,
        author_id     INTEGER,
        created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS position_events_pos ON position_events (position_id, id)",
    "CREATE INDEX IF NOT EXISTS position_events_node ON position_events "
    "((payload->>'node_id')) WHERE kind IN ('member_add', 'member_remove')",
    "CREATE INDEX IF NOT EXISTS position_events_topic ON position_events (topic_root_id, id)",
    # source: кто перенёс. person — сам человек; merge / split — система при
    # слиянии и расколе; undo — отмена «убедило», возврат туда, где был
    # (undo_of — какой переход отменён). В «переубеждены» и в потоки идёт только
    # person, и только не отменённый.
    """
    CREATE TABLE IF NOT EXISTS stance_log (
        id               BIGSERIAL PRIMARY KEY,
        user_id          INTEGER NOT NULL,
        topic_root_id    INTEGER NOT NULL,
        from_position_id INTEGER,
        to_position_id   INTEGER,
        cause_node_id    INTEGER,
        source           TEXT NOT NULL DEFAULT 'person'
                         CHECK (source IN ('person','merge','split','undo')),
        undo_of          BIGINT,
        created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS stance_log_user ON stance_log (user_id, topic_root_id, id)",
    "CREATE INDEX IF NOT EXISTS stance_log_from ON stance_log (from_position_id, created_at)",
    "CREATE INDEX IF NOT EXISTS stance_log_to ON stance_log (to_position_id, created_at)",
    "CREATE INDEX IF NOT EXISTS stance_log_topic ON stance_log (topic_root_id, id)",
    "CREATE INDEX IF NOT EXISTS stance_log_cause ON stance_log (cause_node_id) "
    "WHERE cause_node_id IS NOT NULL",
    """
    CREATE TABLE IF NOT EXISTS exposures (
        id            BIGSERIAL PRIMARY KEY,
        user_id       INTEGER NOT NULL,
        node_id       INTEGER NOT NULL,
        topic_root_id INTEGER NOT NULL,
        position_id   INTEGER,
        response      TEXT NOT NULL CHECK (response IN
                      ('shown','convinced','partial','not_convinced','withdrawn')),
        created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS exposures_topic ON exposures (topic_root_id, id)",
    "CREATE INDEX IF NOT EXISTS exposures_node ON exposures (node_id)",
    # «Это уже сказано — присоединиться» и «у меня тот же вопрос» — одна
    # таблица: присоединение к любому узлу. Повтор ничего не пишет (уникальность).
    """
    CREATE TABLE IF NOT EXISTS node_joins (
        id         BIGSERIAL PRIMARY KEY,
        node_id    INTEGER NOT NULL,
        user_id    INTEGER NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (node_id, user_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS answer_acceptances (
        id               BIGSERIAL PRIMARY KEY,
        question_node_id INTEGER NOT NULL,
        answer_node_id   INTEGER NOT NULL,
        user_id          INTEGER NOT NULL,
        action           TEXT NOT NULL CHECK (action IN ('accept','withdraw')),
        created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS answer_acceptances_q ON answer_acceptances (question_node_id, id)",
    # ---- производные
    """
    CREATE TABLE IF NOT EXISTS current_stance (
        user_id       INTEGER NOT NULL,
        topic_root_id INTEGER NOT NULL,
        position_id   INTEGER NOT NULL REFERENCES positions(id),
        since         TIMESTAMPTZ NOT NULL,
        unclarified   BOOLEAN NOT NULL DEFAULT FALSE,
        PRIMARY KEY (user_id, topic_root_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS current_stance_pos ON current_stance (position_id)",
    """
    CREATE TABLE IF NOT EXISTS exposure_state (
        user_id     INTEGER NOT NULL,
        node_id     INTEGER NOT NULL,
        response    TEXT NOT NULL,
        position_id INTEGER,
        at          TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (user_id, node_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS exposure_state_node ON exposure_state (node_id, response)",
    """
    CREATE TABLE IF NOT EXISTS answer_state (
        question_node_id INTEGER NOT NULL,
        user_id          INTEGER NOT NULL,
        answer_node_id   INTEGER NOT NULL,
        at               TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (question_node_id, user_id)
    )
    """,
    # n — сколько неотменённых личных уходов из позиции у человека
    """
    CREATE TABLE IF NOT EXISTS position_leavers (
        position_id INTEGER NOT NULL,
        user_id     INTEGER NOT NULL,
        n           INTEGER NOT NULL,
        PRIMARY KEY (position_id, user_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cause_counts (
        position_id INTEGER NOT NULL,
        node_id     INTEGER NOT NULL,
        direction   TEXT NOT NULL CHECK (direction IN ('in','out')),
        n           INTEGER NOT NULL,
        PRIMARY KEY (position_id, node_id, direction)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS position_top (
        position_id INTEGER NOT NULL,
        node_id     INTEGER NOT NULL,
        rank        INTEGER NOT NULL,
        PRIMARY KEY (position_id, node_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS position_stats (
        position_id   INTEGER PRIMARY KEY,
        topic_root_id INTEGER NOT NULL,
        in_now        INTEGER NOT NULL DEFAULT 0,
        stood         INTEGER NOT NULL DEFAULT 0,
        converted     INTEGER NOT NULL DEFAULT 0,
        updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS position_stats_daily (
        position_id   INTEGER NOT NULL,
        topic_root_id INTEGER NOT NULL,
        day           DATE NOT NULL,
        in_now        INTEGER NOT NULL,
        stood         INTEGER NOT NULL,
        converted     INTEGER NOT NULL,
        PRIMARY KEY (position_id, day)
    )
    """,
    # Настройки карты на уровне обсуждения. Нет строки — умолчания из
    # opinionmap. Меняет пока только админ.
    """
    CREATE TABLE IF NOT EXISTS topic_settings (
        topic_root_id      INTEGER PRIMARY KEY,
        sort               TEXT NOT NULL DEFAULT 'size'
                           CHECK (sort IN ('size','movement','random')),
        blind_first_answer BOOLEAN NOT NULL DEFAULT FALSE,
        top_k              INTEGER,
        accept_share       DOUBLE PRECISION,
        min_positions      INTEGER,
        updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    # ---- «только дописывается» — триггером. Обход один: db.wipe() в тестовой
    # базе ставит SET LOCAL noosphere.allow_wipe = 'on' на свою транзакцию.
    """
    CREATE OR REPLACE FUNCTION noosphere_append_only() RETURNS trigger AS $$
    BEGIN
      IF current_setting('noosphere.allow_wipe', true) = 'on' THEN
        IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
        IF TG_OP = 'UPDATE' THEN RETURN NEW; END IF;
        RETURN NULL;
      END IF;
      RAISE EXCEPTION 'журнал % только дописывается: % запрещён',
        TG_TABLE_NAME, TG_OP USING ERRCODE = 'insufficient_privilege';
    END $$ LANGUAGE plpgsql
    """,
] + [
    stmt
    for t in JOURNALS
    for stmt in (
        f"CREATE OR REPLACE TRIGGER {t}_append_only BEFORE UPDATE OR DELETE "
        f"ON {t} FOR EACH ROW EXECUTE FUNCTION noosphere_append_only()",
        f"CREATE OR REPLACE TRIGGER {t}_no_truncate BEFORE TRUNCATE "
        f"ON {t} FOR EACH STATEMENT EXECUTE FUNCTION noosphere_append_only()",
    )
] + [
    # Состав позиций из старого скаляра nodes.position_id — только для узлов,
    # у которых состав ещё ни разу не менялся событием. Иначе каждый старт
    # возвращал бы узел туда, откуда его увели слияние, раскол или «признаю
    # ошибку» (скаляр их не знает).
    """
    INSERT INTO position_nodes (position_id, node_id, topic_root_id)
    SELECT n.position_id, n.id, p.topic_root_id
    FROM nodes n JOIN positions p ON p.id = n.position_id
    WHERE n.deleted_at IS NULL
      AND NOT EXISTS (SELECT 1 FROM position_events pe
                      WHERE pe.kind IN ('member_add', 'member_remove')
                        AND pe.payload->>'node_id' = n.id::text)
    ON CONFLICT DO NOTHING
    """,
]

# таблицы, которые db.wipe() чистит вместе с остальными
WIPE_TABLES = ("stance_log, exposures, node_joins, answer_acceptances, "
               "position_events, current_stance, exposure_state, answer_state, "
               "position_leavers, cause_counts, position_top, position_stats, "
               "position_stats_daily, topic_settings, position_nodes")


class OpinionError(Exception):
    """Отказ по правилам карты — маршрут отдаёт его как 4xx."""


def _pool():
    from . import db
    return db._pool_or_raise()


async def _log(conn, type_, payload, author_id=None):
    from . import db
    await db._log(conn, type_, payload, author_id)


async def lock_topic(conn, topic_root_id):
    await conn.execute("SELECT pg_advisory_xact_lock($1, $2)", TOPIC_LOCK, topic_root_id)


async def settings(conn, topic_root_id):
    row = await conn.fetchrow(
        "SELECT * FROM topic_settings WHERE topic_root_id = $1", topic_root_id)
    s = dict(row) if row else {}
    return {
        "sort": s.get("sort") or "size",
        "blind_first_answer": bool(s.get("blind_first_answer")),
        "top_k": s.get("top_k") or om.TOP_K,
        "accept_share": s.get("accept_share") or om.ACCEPT_SHARE,
        "min_positions": s.get("min_positions") or om.MIN_POSITIONS,
    }


async def set_settings(topic_root_id, **kw):
    allowed = {"sort", "blind_first_answer", "top_k", "accept_share", "min_positions"}
    kw = {k: v for k, v in kw.items() if k in allowed and v is not None}
    async with _pool().acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO topic_settings (topic_root_id) VALUES ($1) "
                "ON CONFLICT DO NOTHING", topic_root_id)
            for k, v in kw.items():
                await conn.execute(
                    f"UPDATE topic_settings SET {k} = $2, updated_at = now() "
                    "WHERE topic_root_id = $1", topic_root_id, v)
            await _log(conn, "topic_settings_set",
                       {"topic_root_id": topic_root_id, **kw})
            return await settings(conn, topic_root_id)


# ------------------------------------------------------------------ счётчики
async def _bump(conn, pid, in_now=0, stood=0, converted=0):
    await conn.execute(
        """
        INSERT INTO position_stats (position_id, topic_root_id, in_now, stood, converted)
        SELECT $1, topic_root_id, $2, $3, $4 FROM positions WHERE id = $1
        ON CONFLICT (position_id) DO UPDATE SET
            in_now = position_stats.in_now + EXCLUDED.in_now,
            stood = position_stats.stood + EXCLUDED.stood,
            converted = position_stats.converted + EXCLUDED.converted,
            updated_at = now()
        """, pid, in_now, stood, converted)


async def _is_stood(conn, user_id, pid):
    return await conn.fetchval(
        """
        SELECT EXISTS (
            SELECT 1 FROM exposure_state es
            JOIN position_top t ON t.node_id = es.node_id AND t.position_id = $2
            WHERE es.user_id = $1 AND es.response = ANY($3::text[]))
        """, user_id, pid, list(om.STOOD))


async def _is_leaver(conn, pid, user_id):
    return await conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM position_leavers "
        "WHERE position_id = $1 AND user_id = $2)", pid, user_id)


async def _count(conn, pid, node_id, direction, delta):
    await conn.execute(
        """
        INSERT INTO cause_counts (position_id, node_id, direction, n)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (position_id, node_id, direction)
        DO UPDATE SET n = cause_counts.n + EXCLUDED.n
        """, pid, node_id, direction, delta)
    await conn.execute(
        "DELETE FROM cause_counts WHERE position_id = $1 AND node_id = $2 "
        "AND direction = $3 AND n <= 0", pid, node_id, direction)


async def _objection_candidates(conn, pid):
    rows = await conn.fetch(
        """
        SELECT DISTINCT e.source_id AS id, n.poi_score AS poi, n.created_at
        FROM edges e
        JOIN position_nodes pn ON pn.node_id = e.target_id AND pn.position_id = $1
        JOIN nodes n ON n.id = e.source_id
        WHERE e.type = ANY($2::text[]) AND n.deleted_at IS NULL
          AND NOT EXISTS (SELECT 1 FROM position_nodes own
                          WHERE own.position_id = $1 AND own.node_id = e.source_id)
        """, pid, list(om.OBJECTION_EDGES))
    return [dict(r) for r in rows]


async def refresh_top(conn, pid):
    """Пересобрать главные возражения позиции. Если сменился их СОСТАВ —
    пересчитать «устояли» по людям этой позиции."""
    topic = await conn.fetchval("SELECT topic_root_id FROM positions WHERE id = $1", pid)
    if topic is None:
        return
    k = (await settings(conn, topic))["top_k"]
    cands = await _objection_candidates(conn, pid)
    outs = {r["node_id"]: r["n"] for r in await conn.fetch(
        "SELECT node_id, n FROM cause_counts WHERE position_id = $1 "
        "AND direction = 'out'", pid)}
    top = om.rank_objections(cands, outs, k)
    old = [r["node_id"] for r in await conn.fetch(
        "SELECT node_id FROM position_top WHERE position_id = $1 ORDER BY rank", pid)]
    if old == top:
        return
    await conn.execute("DELETE FROM position_top WHERE position_id = $1", pid)
    if top:
        await conn.executemany(
            "INSERT INTO position_top (position_id, node_id, rank) VALUES ($1, $2, $3)",
            [(pid, n, i) for i, n in enumerate(top)])
    if set(old) != set(top):
        await _bump(conn, pid)          # строка счётчиков существует
        await conn.execute(
            """
            UPDATE position_stats SET updated_at = now(), stood = (
                SELECT count(*) FROM current_stance cs
                WHERE cs.position_id = $1 AND EXISTS (
                    SELECT 1 FROM exposure_state es
                    JOIN position_top t ON t.node_id = es.node_id AND t.position_id = $1
                    WHERE es.user_id = cs.user_id AND es.response = ANY($2::text[])))
            WHERE position_id = $1
            """, pid, list(om.STOOD))


async def _position_event(conn, topic, pid, kind, payload=None, author_id=None, at=None):
    payload = payload or {}
    await conn.execute(
        "INSERT INTO position_events (topic_root_id, position_id, kind, payload, author_id, "
        "created_at) VALUES ($1, $2, $3, $4, $5, coalesce($6, now()))",
        topic, pid, kind, json.dumps(payload), author_id, at)
    await _log(conn, "position_event",
               {"topic_root_id": topic, "position_id": pid, "kind": kind, **payload},
               author_id)


async def _sync_status(conn, pid):
    row = await conn.fetchrow(
        "SELECT p.status, p.topic_root_id, coalesce(s.in_now, 0) AS in_now "
        "FROM positions p LEFT JOIN position_stats s ON s.position_id = p.id "
        "WHERE p.id = $1", pid)
    if row is None:
        return
    new = om.next_status(row["status"], row["in_now"])
    if new:
        await conn.execute("UPDATE positions SET status = $2 WHERE id = $1", pid, new)
        await _position_event(conn, row["topic_root_id"], pid, "status",
                              {"from": row["status"], "to": new})


# ------------------------------------------------------------------ переходы
async def _apply_move(conn, user_id, topic, frm, to, cause, source, undo_of=None,
                      at=None):
    """Одна строка stance_log и всё, что из неё следует, — в транзакции вызывающего.

    at — момент записи. Только для синтетики (tools/opinionmap/synth.py):
    историю за шесть недель нельзя записать «сейчас», а править журнал задним
    числом нельзя. Маршруты его не передают."""
    row = await conn.fetchrow(
        "INSERT INTO stance_log (user_id, topic_root_id, from_position_id, "
        "to_position_id, cause_node_id, source, undo_of, created_at) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7, coalesce($8, now())) "
        "RETURNING id, created_at",
        user_id, topic, frm, to, cause, source, undo_of, at)
    counted = source == om.PERSUASION
    tops = set()
    if frm is not None:
        was = await _is_stood(conn, user_id, frm)
        await _bump(conn, frm, in_now=-1, stood=-int(was))
        if counted:
            await conn.execute(
                "INSERT INTO position_leavers (position_id, user_id, n) VALUES ($1, $2, 1) "
                "ON CONFLICT (position_id, user_id) DO UPDATE SET n = position_leavers.n + 1",
                frm, user_id)
            if cause:
                await _count(conn, frm, cause, "out", 1)
                tops.add(frm)
        if await _is_leaver(conn, frm, user_id):
            await _bump(conn, frm, converted=1)
    if to is not None:
        now_stood = await _is_stood(conn, user_id, to)
        await _bump(conn, to, in_now=1, stood=int(now_stood))
        if await _is_leaver(conn, to, user_id):
            await _bump(conn, to, converted=-1)
        if counted and cause:
            await _count(conn, to, cause, "in", 1)
        await conn.execute(
            "INSERT INTO current_stance (user_id, topic_root_id, position_id, since) "
            "VALUES ($1, $2, $3, $4) ON CONFLICT (user_id, topic_root_id) DO UPDATE "
            "SET position_id = EXCLUDED.position_id, since = EXCLUDED.since, "
            "unclarified = FALSE", user_id, topic, to, row["created_at"])
    else:
        await conn.execute(
            "DELETE FROM current_stance WHERE user_id = $1 AND topic_root_id = $2",
            user_id, topic)
    if undo_of is not None:
        # отменённый переход был A→B «убедило»; сейчас человек вернулся B→A
        orig = await conn.fetchrow("SELECT * FROM stance_log WHERE id = $1", undo_of)
        a, b = orig["from_position_id"], orig["to_position_id"]
        if a is not None:
            await conn.execute(
                "UPDATE position_leavers SET n = n - 1 WHERE position_id = $1 AND user_id = $2",
                a, user_id)
            await conn.execute(
                "DELETE FROM position_leavers WHERE position_id = $1 AND user_id = $2 AND n <= 0",
                a, user_id)
        if orig["cause_node_id"]:
            if a is not None:
                await _count(conn, a, orig["cause_node_id"], "out", -1)
                tops.add(a)
            if b is not None:
                await _count(conn, b, orig["cause_node_id"], "in", -1)
    for pid in sorted(tops):
        await refresh_top(conn, pid)
    for pid in (frm, to):
        if pid is not None:
            await _sync_status(conn, pid)
    await _log(conn, "stance_set",
               {"stance_id": row["id"], "topic_root_id": topic, "from": frm, "to": to,
                "cause_node_id": cause, "source": source, "undo_of": undo_of},
               user_id)
    return {"stance_id": row["id"], "from": frm, "to": to, "cause_node_id": cause,
            "source": source, "at": row["created_at"].isoformat()}


async def set_stance(user_id, topic, position_id, cause_node_id=None, at=None):
    """Человек сам выбирает позицию в обсуждении (или уходит без позиции)."""
    async with _pool().acquire() as conn:
        async with conn.transaction():
            await lock_topic(conn, topic)
            cur = await conn.fetchrow(
                "SELECT position_id, unclarified FROM current_stance "
                "WHERE user_id = $1 AND topic_root_id = $2", user_id, topic)
            frm = cur["position_id"] if cur else None
            if position_id is not None:
                pos = await conn.fetchrow(
                    "SELECT topic_root_id, status, split_from FROM positions WHERE id = $1",
                    position_id)
                if pos is None or pos["topic_root_id"] != topic:
                    raise OpinionError("такой позиции в этом обсуждении нет")
                if pos["status"] not in om.OPEN_STATUSES:
                    raise OpinionError("позиция слита или расколота — выберите её часть")
            if frm == position_id:
                return {"unchanged": True, "position_id": frm}
            if cause_node_id is not None and not await conn.fetchval(
                    "SELECT EXISTS (SELECT 1 FROM nodes WHERE id = $1 AND deleted_at IS NULL)",
                    cause_node_id):
                raise OpinionError("узла-причины нет")
            source = om.PERSUASION
            if cur and cur["unclarified"] and position_id is not None:
                split_parent = await conn.fetchval(
                    "SELECT split_from FROM positions WHERE id = $1", position_id)
                if split_parent == frm:
                    source = "split"    # уточнил позицию после раскола — не «переубедили»
            return await _apply_move(conn, user_id, topic, frm, position_id,
                                     cause_node_id, source, at=at)


# ------------------------------------------------------------- возражения
async def _node_topic(conn, node_id):
    row = await conn.fetchrow(
        "SELECT topic_root_id, author_id, kind, deleted_at FROM nodes WHERE id = $1", node_id)
    if row is None or row["deleted_at"] is not None:
        raise OpinionError("узла нет")
    return row


async def record_exposure(user_id, node_id, response, at=None):
    if response not in om.RESPONSES or response == "withdrawn":
        raise OpinionError("неизвестный ответ")
    async with _pool().acquire() as conn:
        async with conn.transaction():
            node = await _node_topic(conn, node_id)
            topic = node["topic_root_id"]
            await lock_topic(conn, topic)
            return await _expose(conn, user_id, node_id, topic, response, at)


async def _expose(conn, user_id, node_id, topic, response, at=None):
    state = await conn.fetchrow(
        "SELECT response FROM exposure_state WHERE user_id = $1 AND node_id = $2",
        user_id, node_id)
    if response == "shown" and state is not None:
        return {"recorded": False, "response": state["response"]}
    if response == "withdrawn" and state is None:
        return {"recorded": False, "response": None}
    pid = await conn.fetchval(
        "SELECT position_id FROM current_stance WHERE user_id = $1 AND topic_root_id = $2",
        user_id, topic)
    affected = pid is not None and await conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM position_top WHERE position_id = $1 AND node_id = $2)",
        pid, node_id)
    before = await _is_stood(conn, user_id, pid) if affected else False
    at = await conn.fetchval(
        "INSERT INTO exposures (user_id, node_id, topic_root_id, position_id, response, "
        "created_at) VALUES ($1, $2, $3, $4, $5, coalesce($6, now())) RETURNING created_at",
        user_id, node_id, topic, pid, response, at)
    if response == "withdrawn":
        await conn.execute(
            "DELETE FROM exposure_state WHERE user_id = $1 AND node_id = $2", user_id, node_id)
    else:
        await conn.execute(
            "INSERT INTO exposure_state (user_id, node_id, response, position_id, at) "
            "VALUES ($1, $2, $3, $4, $5) ON CONFLICT (user_id, node_id) DO UPDATE SET "
            "response = EXCLUDED.response, position_id = EXCLUDED.position_id, at = EXCLUDED.at",
            user_id, node_id, response, pid, at)
    if affected:
        after = await _is_stood(conn, user_id, pid)
        if after != before:
            await _bump(conn, pid, stood=1 if after else -1)
    await _log(conn, "exposure",
               {"node_id": node_id, "topic_root_id": topic, "position_id": pid,
                "response": response}, user_id)
    return {"recorded": True, "response": None if response == "withdrawn" else response}


async def undo_response(user_id, node_id, at=None):
    """«Отменить»: снять ответ на возражение. Если после «убедило» человек уже
    перешёл, назвав этот узел причиной, — вернуть его обратно (source=undo)."""
    async with _pool().acquire() as conn:
        async with conn.transaction():
            node = await _node_topic(conn, node_id)
            topic = node["topic_root_id"]
            await lock_topic(conn, topic)
            moved = None
            last = await conn.fetchrow(
                "SELECT * FROM stance_log WHERE user_id = $1 AND topic_root_id = $2 "
                "ORDER BY id DESC LIMIT 1", user_id, topic)
            back_open = last is not None and last["from_position_id"] is not None and \
                await conn.fetchval("SELECT status = ANY($2::text[]) FROM positions "
                                    "WHERE id = $1", last["from_position_id"],
                                    list(om.OPEN_STATUSES))
            if (last and last["source"] == om.PERSUASION
                    and last["cause_node_id"] == node_id
                    and (last["from_position_id"] is None or back_open)):
                moved = await _apply_move(conn, user_id, topic, last["to_position_id"],
                                          last["from_position_id"], None, "undo",
                                          undo_of=last["id"], at=at)
            res = await _expose(conn, user_id, node_id, topic, "withdrawn", at)
            res["moved_back"] = moved
            return res


# --------------------------------------------------- присоединения и ответы
async def join_node(user_id, node_id, at=None):
    async with _pool().acquire() as conn:
        async with conn.transaction():
            node = await _node_topic(conn, node_id)
            if node["author_id"] == user_id:
                raise OpinionError("к своему узлу присоединяться незачем")
            jid = await conn.fetchval(
                "INSERT INTO node_joins (node_id, user_id, created_at) "
                "VALUES ($1, $2, coalesce($3, now())) "
                "ON CONFLICT (node_id, user_id) DO NOTHING RETURNING id", node_id, user_id, at)
            if jid is not None:
                await _log(conn, "node_joined", {"node_id": node_id}, user_id)
            n = await conn.fetchval(
                "SELECT count(*) FROM node_joins WHERE node_id = $1", node_id)
    return {"joined": True, "new": jid is not None, "joins": n}


async def _askers(conn, question_id):
    author = await conn.fetchval("SELECT author_id FROM nodes WHERE id = $1", question_id)
    rows = await conn.fetch("SELECT user_id FROM node_joins WHERE node_id = $1", question_id)
    return ({author} if author else set()) | {r["user_id"] for r in rows}


async def accept_answer(user_id, question_id, answer_id, action="accept", at=None):
    if action not in ("accept", "withdraw"):
        raise OpinionError("action: accept | withdraw")
    async with _pool().acquire() as conn:
        async with conn.transaction():
            q = await _node_topic(conn, question_id)
            if q["kind"] != "question":
                raise OpinionError("принять ответ можно только на вопрос")
            if user_id not in await _askers(conn, question_id):
                raise OpinionError("принимает ответ тот, кто спрашивал: "
                                   "сначала «у меня тот же вопрос»")
            if not await conn.fetchval(
                    "SELECT EXISTS (SELECT 1 FROM edges WHERE source_id = $1 AND target_id = $2)",
                    answer_id, question_id):
                raise OpinionError("это не ответ на этот вопрос")
            cur = await conn.fetchval(
                "SELECT answer_node_id FROM answer_state WHERE question_node_id = $1 "
                "AND user_id = $2", question_id, user_id)
            if action == "withdraw" and cur != answer_id:
                return {"accepted": cur}
            if action == "accept" and cur == answer_id:
                return {"accepted": cur}
            at = await conn.fetchval(
                "INSERT INTO answer_acceptances (question_node_id, answer_node_id, user_id, "
                "action, created_at) VALUES ($1, $2, $3, $4, coalesce($5, now())) "
                "RETURNING created_at", question_id, answer_id, user_id, action, at)
            if action == "accept":
                await conn.execute(
                    "INSERT INTO answer_state (question_node_id, user_id, answer_node_id, at) "
                    "VALUES ($1, $2, $3, $4) ON CONFLICT (question_node_id, user_id) "
                    "DO UPDATE SET answer_node_id = EXCLUDED.answer_node_id, at = EXCLUDED.at",
                    question_id, user_id, answer_id, at)
            else:
                await conn.execute(
                    "DELETE FROM answer_state WHERE question_node_id = $1 AND user_id = $2",
                    question_id, user_id)
            await _log(conn, "answer_" + action,
                       {"question_id": question_id, "answer_id": answer_id}, user_id)
    return {"accepted": answer_id if action == "accept" else None}


async def question_status(conn, question_id, share=om.ACCEPT_SHARE):
    askers = await _askers(conn, question_id)
    rows = await conn.fetch(
        "SELECT user_id, answer_node_id FROM answer_state WHERE question_node_id = $1",
        question_id)
    opened = await conn.fetchval("SELECT created_at FROM nodes WHERE id = $1", question_id)
    return om.question_status(askers, {r["user_id"]: r["answer_node_id"] for r in rows},
                              share, opened)


# ------------------------------------------------------ состав позиций (хуки)
async def on_node_position(conn, node_id, old_pid, new_pid, reason="assign"):
    """Хук db.set_node_position: переносит узел в составе и пересобирает
    главные возражения затронутых позиций. Зовётся ДО db._log."""
    pids = [p for p in (old_pid, new_pid) if p is not None]
    if not pids:
        return
    topics = sorted({r["topic_root_id"] for r in await conn.fetch(
        "SELECT topic_root_id FROM positions WHERE id = ANY($1::int[])", pids)})
    for t in topics:
        await lock_topic(conn, t)
    if old_pid is not None and old_pid != new_pid:
        await _remove_member(conn, old_pid, node_id, reason)
    if new_pid is not None:
        await _add_member(conn, new_pid, node_id, reason)
    # у узлов позиции могли быть возражения, у возражений — цели в позиции
    for pid in sorted(set(pids)):
        await refresh_top(conn, pid)


async def _add_member(conn, pid, node_id, reason):
    topic = await conn.fetchval("SELECT topic_root_id FROM positions WHERE id = $1", pid)
    added = await conn.fetchval(
        "INSERT INTO position_nodes (position_id, node_id, topic_root_id) "
        "VALUES ($1, $2, $3) ON CONFLICT DO NOTHING RETURNING node_id", pid, node_id, topic)
    if added is not None:
        await _position_event(conn, topic, pid, "member_add",
                              {"node_id": node_id, "reason": reason})


async def _remove_member(conn, pid, node_id, reason):
    topic = await conn.fetchval("SELECT topic_root_id FROM positions WHERE id = $1", pid)
    gone = await conn.fetchval(
        "DELETE FROM position_nodes WHERE position_id = $1 AND node_id = $2 "
        "RETURNING node_id", pid, node_id)
    if gone is not None:
        await _position_event(conn, topic, pid, "member_remove",
                              {"node_id": node_id, "reason": reason})


async def on_edge(conn, target_id, edge_type):
    """Хук db.add_edge: новое возражение на узел позиции может войти в главные."""
    if edge_type not in om.OBJECTION_EDGES:
        return
    rows = await conn.fetch(
        "SELECT position_id, topic_root_id FROM position_nodes WHERE node_id = $1 "
        "ORDER BY topic_root_id, position_id", target_id)
    for t in sorted({r["topic_root_id"] for r in rows}):
        await lock_topic(conn, t)
    for r in rows:
        await refresh_top(conn, r["position_id"])


async def on_error_ack(conn, node_id):
    """Хук «признаю ошибку»: узел выходит из состава всех позиций, в графе остаётся."""
    rows = await conn.fetch(
        "SELECT position_id, topic_root_id FROM position_nodes WHERE node_id = $1", node_id)
    for t in sorted({r["topic_root_id"] for r in rows}):
        await lock_topic(conn, t)
    for r in rows:
        await _remove_member(conn, r["position_id"], node_id, "error_ack")
        await refresh_top(conn, r["position_id"])
    return [r["position_id"] for r in rows]


async def create_position(conn, topic, headline, composed, author_id=None,
                          split_from=None, status="forming"):
    pid = await conn.fetchval(
        "INSERT INTO positions (topic_root_id, headline, composed, stance, author_id, "
        "status, split_from) VALUES ($1, $2, $3, 'mixed', $4, $5, $6) RETURNING id",
        topic, headline, composed, author_id, status, split_from)
    await _position_event(conn, topic, pid, "create",
                          {"headline": headline, "split_from": split_from}, author_id)
    await _bump(conn, pid)
    return pid


# ------------------------------------------------------- слияние и раскол
async def merge_positions(src, dst, author_id=None, at=None):
    """Слить src в dst: люди и узлы переходят, src получает статус merged.
    Переходы людей пишутся с source=merge — в «переубеждены» они не идут."""
    async with _pool().acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                "SELECT id, topic_root_id, status FROM positions WHERE id = ANY($1::int[])",
                [src, dst])
            by = {r["id"]: r for r in rows}
            if src == dst or len(by) != 2 or by[src]["topic_root_id"] != by[dst]["topic_root_id"]:
                raise OpinionError("сливать можно две разные позиции одного обсуждения")
            if by[src]["status"] not in om.OPEN_STATUSES or by[dst]["status"] not in om.OPEN_STATUSES:
                raise OpinionError("слитую или расколотую позицию не сливают")
            topic = by[src]["topic_root_id"]
            await lock_topic(conn, topic)
            people = await conn.fetch(
                "SELECT user_id FROM current_stance WHERE position_id = $1 ORDER BY user_id", src)
            for p in people:
                await _apply_move(conn, p["user_id"], topic, src, dst, None, "merge", at=at)
            for m in await conn.fetch(
                    "SELECT node_id FROM position_nodes WHERE position_id = $1 ORDER BY node_id", src):
                await _remove_member(conn, src, m["node_id"], "merge")
                await _add_member(conn, dst, m["node_id"], "merge")
                await conn.execute("UPDATE nodes SET position_id = $2 WHERE id = $1",
                                   m["node_id"], dst)
            await conn.execute(
                "UPDATE positions SET status = 'merged', merged_into = $2 WHERE id = $1", src, dst)
            await _position_event(conn, topic, src, "merge", {"into": dst, "people": len(people)},
                                  author_id, at=at)
            await refresh_top(conn, dst)
            await refresh_top(conn, src)
            await _sync_status(conn, dst)
    return {"merged": src, "into": dst, "people": len(people)}


async def split_position(pid, parts, author_id=None, at=None):
    """Расколоть позицию на части [{headline, composed, node_ids}].

    Люди НЕ распределяются: у каждого сторонника пометка «не уточнил», он
    видит выбор между частями и до выбора считается в исходной позиции.
    """
    if len(parts) < 2:
        raise OpinionError("раскол — это хотя бы две части")
    async with _pool().acquire() as conn:
        async with conn.transaction():
            pos = await conn.fetchrow("SELECT * FROM positions WHERE id = $1", pid)
            if pos is None or pos["status"] not in om.OPEN_STATUSES:
                raise OpinionError("раскалывать можно только живую позицию")
            topic = pos["topic_root_id"]
            await lock_topic(conn, topic)
            members = {r["node_id"] for r in await conn.fetch(
                "SELECT node_id FROM position_nodes WHERE position_id = $1", pid)}
            new_ids = []
            for part in parts:
                npid = await create_position(conn, topic, part["headline"],
                                             part.get("composed") or part["headline"],
                                             author_id, split_from=pid)
                for nid in part.get("node_ids", []):
                    if nid in members:
                        await _remove_member(conn, pid, nid, "split")
                        await _add_member(conn, npid, nid, "split")
                        await conn.execute("UPDATE nodes SET position_id = $2 WHERE id = $1",
                                           nid, npid)
                new_ids.append(npid)
            await conn.execute("UPDATE positions SET status = 'split' WHERE id = $1", pid)
            n = await conn.fetchval(
                "SELECT count(*) FROM current_stance WHERE position_id = $1", pid)
            await conn.execute(
                "UPDATE current_stance SET unclarified = TRUE WHERE position_id = $1", pid)
            await _position_event(conn, topic, pid, "split", {"parts": new_ids}, author_id, at=at)
            for x in [pid] + new_ids:
                await refresh_top(conn, x)
    return {"split": pid, "parts": new_ids, "unclarified": n}


# ------------------------------------------------------------ эталон и снимки
async def journals(conn, topic):
    """Всё, что нужно opinionmap.replay, — из журналов и текущей раскладки."""
    stance = [dict(r) for r in await conn.fetch(
        "SELECT * FROM stance_log WHERE topic_root_id = $1 ORDER BY id", topic)]
    expo = [dict(r) for r in await conn.fetch(
        "SELECT id, user_id, node_id, response, created_at FROM exposures "
        "WHERE topic_root_id = $1 ORDER BY id", topic)]
    members, objections = {}, {}
    pids = [r["id"] for r in await conn.fetch(
        "SELECT id FROM positions WHERE topic_root_id = $1", topic)]
    meta = {}
    for pid in pids:
        members[pid] = {r["node_id"] for r in await conn.fetch(
            "SELECT node_id FROM position_nodes WHERE position_id = $1", pid)}
        cands = await _objection_candidates(conn, pid)
        objections[pid] = {c["id"] for c in cands}
        for c in cands:
            meta[c["id"]] = {"poi": c["poi"], "created_at": c["created_at"]}
    events = [dict(r) | {"payload": json.loads(r["payload"]) if isinstance(r["payload"], str)
                         else r["payload"]}
              for r in await conn.fetch(
                  "SELECT kind, position_id, payload, created_at FROM position_events "
                  "WHERE topic_root_id = $1 AND kind = 'split' ORDER BY id", topic)]
    k = (await settings(conn, topic))["top_k"]
    return stance, expo, members, objections, meta, events, k


async def recompute(topic):
    """Полный пересчёт из журналов (эталон) — для сверки и отладки."""
    async with _pool().acquire() as conn:
        stance, expo, members, objections, meta, events, k = await journals(conn, topic)
    return om.replay(stance, expo, members, objections, meta, events, k=k)


async def ensure_daily(topic, today=None):
    """Дописать недостающие дневные снимки до вчера включительно.

    Сегодняшний день снимком не пишется — его числа ещё меняются; экран берёт
    их из position_stats.
    """
    today = today or datetime.now(timezone.utc).date()
    async with _pool().acquire() as conn:
        last = await conn.fetchval(
            "SELECT max(day) FROM position_stats_daily WHERE topic_root_id = $1", topic)
        first_ts = await conn.fetchval(
            "SELECT min(created_at) FROM stance_log WHERE topic_root_id = $1", topic)
        if first_ts is None:
            return 0
        start = (last + timedelta(days=1)) if last else first_ts.astimezone(timezone.utc).date()
        end = today - timedelta(days=1)
        if start > end:
            return 0
        stance, expo, members, objections, meta, events, k = await journals(conn, topic)
    series = om.daily_series(stance, expo, members, objections, meta, events,
                             first=start, last=end, k=k)
    rows = [(pid, topic, d, s["in_now"], s["stood"], s["converted"])
            for d, per in series.items() for pid, s in per.items()]
    async with _pool().acquire() as conn:
        async with conn.transaction():
            await conn.executemany(
                "INSERT INTO position_stats_daily (position_id, topic_root_id, day, "
                "in_now, stood, converted) VALUES ($1, $2, $3, $4, $5, $6) "
                "ON CONFLICT (position_id, day) DO NOTHING", rows)
    return len(series)


# ------------------------------------------------------- чтение для экранов
CAPTION = "Числа — это аккаунты, а не проверенные люди: верификации пока нет."
# метка вида узла на карте — по его ребру (как в макете)
KIND_LABEL = {"refute": "против", "undercut": "подрыв", "qualify": "уточн.",
              "support": "за", "question": "вопрос", "restate": "пересказ",
              "proposal": "предложение"}
PERIODS = {"7": 7, "30": 30, "all": None}


def period_since(period, now=None):
    if period not in PERIODS:
        raise OpinionError("period: 7 | 30 | all")
    days = PERIODS[period]
    if days is None:
        return None
    return (now or datetime.now(timezone.utc)) - timedelta(days=days)


async def _node_cards(conn, ids):
    """{id: {id, text, kind, label, poi, retracted}} — для любого числа на
    экране нужен текст узла в одно касание."""
    ids = [i for i in set(ids) if i is not None]
    if not ids:
        return {}
    rows = await conn.fetch(
        """
        SELECT n.id, n.text, n.title, n.kind, n.poi_score, n.retracted_at,
               (SELECT e.type FROM edges e WHERE e.source_id = n.id ORDER BY e.id LIMIT 1) AS rel
        FROM nodes n WHERE n.id = ANY($1::int[])
        """, ids)
    out = {}
    for r in rows:
        rel = "question" if r["kind"] == "question" else r["rel"]
        out[r["id"]] = {"id": r["id"], "text": r["title"] or r["text"], "kind": r["kind"],
                        "rel": rel, "label": KIND_LABEL.get(rel, ""),
                        "poi": round(r["poi_score"]) if r["poi_score"] is not None else None,
                        "retracted": r["retracted_at"] is not None}
    return out


async def _titles(conn, topic):
    return {r["id"]: {"id": r["id"], "title": r["headline"], "status": r["status"]}
            for r in await conn.fetch(
                "SELECT id, headline, status FROM positions WHERE topic_root_id = $1", topic)}


async def _rows_for(conn, pids, since=None):
    return [dict(r) for r in await conn.fetch(
        """
        SELECT * FROM stance_log
        WHERE (from_position_id = ANY($1::int[]) OR to_position_id = ANY($1::int[]))
          AND ($2::timestamptz IS NULL OR created_at >= $2
               OR source = 'undo')
        ORDER BY id
        """, pids, since)]


def _net(rows, pid, since):
    n = 0
    for r in rows:
        if since is not None and r["created_at"] < since:
            continue
        n += (r["to_position_id"] == pid) - (r["from_position_id"] == pid)
    return n


def _flow_view(fl, titles, cards):
    def side(x):
        groups = []
        for g in x["groups"]:
            other = g["position_id"]
            groups.append({
                "position_id": other,
                "title": titles[other]["title"] if other in titles else None,
                "n": g["n"],
                "cause": cards.get(g["cause_node_id"])})
        return {"groups": groups, "rest": x["rest"], "total": x["total"]}
    return {"in": side(fl["in"]), "out": side(fl["out"])}


async def _crux_nodes(conn, topic, pid=None):
    """Вопросы и подрывы — внутри обсуждения или направленные на узлы позиции."""
    if pid is None:
        rows = await conn.fetch(
            """
            SELECT n.id FROM nodes n WHERE n.topic_root_id = $1 AND n.deleted_at IS NULL
              AND (n.kind = 'question' OR EXISTS (SELECT 1 FROM edges e
                   WHERE e.source_id = n.id AND e.type = 'undercut'))
            """, topic)
    else:
        rows = await conn.fetch(
            """
            SELECT DISTINCT n.id FROM edges e
            JOIN position_nodes pn ON pn.node_id = e.target_id AND pn.position_id = $1
            JOIN nodes n ON n.id = e.source_id
            WHERE n.deleted_at IS NULL AND (n.kind = 'question' OR e.type = 'undercut')
            """, pid)
    return [r["id"] for r in rows]


async def _cruxes(conn, topic, pid=None, limit=6, share=om.ACCEPT_SHARE, titles=None):
    ids = await _crux_nodes(conn, topic, pid)
    if not ids:
        return []
    moves = {r["cause_node_id"]: r for r in await conn.fetch(
        """
        SELECT cause_node_id, count(*) AS n FROM stance_log
        WHERE cause_node_id = ANY($1::int[]) AND source = 'person'
          AND id NOT IN (SELECT undo_of FROM stance_log WHERE undo_of IS NOT NULL)
        GROUP BY cause_node_id
        """, ids)}
    resp = {}
    for r in await conn.fetch(
            "SELECT node_id, response, count(*) AS n FROM exposure_state "
            "WHERE node_id = ANY($1::int[]) GROUP BY node_id, response", ids):
        resp.setdefault(r["node_id"], {})[r["response"]] = r["n"]
    scored = sorted(ids, key=lambda i: (-(moves[i]["n"] if i in moves else 0)
                                        - resp.get(i, {}).get("not_convinced", 0), i))
    cards = await _node_cards(conn, scored[:limit])
    out = []
    for i in scored[:limit]:
        c = dict(cards[i])
        c["moves"] = moves[i]["n"] if i in moves else 0
        c["not_convinced"] = resp.get(i, {}).get("not_convinced", 0)
        c["convinced"] = resp.get(i, {}).get("convinced", 0)
        c["partial"] = resp.get(i, {}).get("partial", 0)
        if c["kind"] == "question":
            c["question"] = await question_status(conn, i, share)
            c["question"]["joins"] = await conn.fetchval(
                "SELECT count(*) FROM node_joins WHERE node_id = $1", i)
        if pid is not None:
            dest = await conn.fetchrow(
                """
                SELECT to_position_id, count(*) AS n FROM stance_log
                WHERE from_position_id = $1 AND cause_node_id = $2 AND source = 'person'
                GROUP BY to_position_id ORDER BY n DESC LIMIT 1
                """, pid, i)
            if dest and dest["n"] >= om.MIN_FLOW:
                c["led_to"] = {"position_id": dest["to_position_id"], "n": dest["n"],
                               "title": (titles or {}).get(dest["to_position_id"], {}).get("title")}
        out.append(c)
    return out


def _numbers(s):
    in_now, stood, conv = s.get("in_now", 0), s.get("stood", 0), s.get("converted", 0)
    return {"in_now": in_now, "stood": stood, "unchecked": in_now - stood,
            "converted": conv, "ever": in_now + conv}


async def _may_see_numbers(conn, topic, viewer_id, cfg):
    """blind_first_answer: без позиции и без своего ответа в обсуждении чисел не видно."""
    if not cfg["blind_first_answer"]:
        return True
    if viewer_id is None:
        return False
    return await conn.fetchval(
        """
        SELECT EXISTS (SELECT 1 FROM current_stance WHERE user_id = $1 AND topic_root_id = $2)
            OR EXISTS (SELECT 1 FROM nodes WHERE author_id = $1 AND topic_root_id = $2
                       AND id <> topic_root_id AND deleted_at IS NULL)
        """, viewer_id, topic)


async def topic_view(topic, period="30", sort=None, viewer_id=None, seed=None):
    since = period_since(period)
    async with _pool().acquire() as conn:
        root = await conn.fetchrow(
            "SELECT id, title, text, kind FROM nodes WHERE id = $1 AND id = topic_root_id "
            "AND deleted_at IS NULL", topic)
        if root is None:
            raise OpinionError("обсуждения нет")
        cfg = await settings(conn, topic)
        sort = sort or cfg["sort"]
        titles = await _titles(conn, topic)
        stats = {r["position_id"]: dict(r) for r in await conn.fetch(
            "SELECT * FROM position_stats WHERE topic_root_id = $1", topic)}
        visible = [pid for pid, t in titles.items()
                   if t["status"] == "active"
                   or (t["status"] == "split" and stats.get(pid, {}).get("in_now", 0) > 0)]
        active = sum(1 for t in titles.values() if t["status"] == "active")
        cold = active < cfg["min_positions"]
        forming = sum(1 for t in titles.values() if t["status"] == "forming")
        see = await _may_see_numbers(conn, topic, viewer_id, cfg)
        mine = None
        if viewer_id is not None:
            mine = await conn.fetchval(
                "SELECT position_id FROM current_stance WHERE user_id = $1 AND topic_root_id = $2",
                viewer_id, topic)
        rows = await _rows_for(conn, visible, since) if visible and see else []
        items, cause_ids = [], []
        for pid in visible:
            fl = om.flows(rows, pid, since, limit=3)
            for side in ("in", "out"):
                cause_ids += [g["cause_node_id"] for g in fl[side]["groups"]]
            items.append((pid, fl))
        cards = await _node_cards(conn, cause_ids)
        positions = []
        for pid, fl in items:
            p = {"id": pid, "title": titles[pid]["title"], "status": titles[pid]["status"],
                 "mine": pid == mine}
            if see:
                p.update(_numbers(stats.get(pid, {})))
                p["delta"] = _net(rows, pid, since)
                p["unclarified"] = await conn.fetchval(
                    "SELECT count(*) FROM current_stance WHERE position_id = $1 AND unclarified",
                    pid) if titles[pid]["status"] == "split" else 0
                p["flows"] = _flow_view(fl, titles, cards)
            positions.append(p)
        if sort == "random":
            import random as _r
            _r.Random(seed).shuffle(positions)
        elif sort == "movement" and see:
            positions.sort(key=lambda p: (-abs(p.get("delta", 0)), p["id"]))
        elif see:
            positions.sort(key=lambda p: (-p.get("in_now", 0), p["id"]))
        cruxes = await _cruxes(conn, topic, None, limit=8, share=cfg["accept_share"],
                               titles=titles) if see else []
    return {"topic": {"id": topic, "title": root["title"] or root["text"][:80],
                      "kind": root["kind"]},
            "period": period, "sort": sort, "cold_start": cold,
            "active_positions": active, "forming_positions": forming,
            "min_positions": cfg["min_positions"], "blind": not see,
            "my_position": mine, "positions": positions, "cruxes": cruxes,
            "caption": CAPTION}


async def position_view(pid, period="30", at=None, viewer_id=None):
    since = period_since(period)
    async with _pool().acquire() as conn:
        pos = await conn.fetchrow("SELECT * FROM positions WHERE id = $1", pid)
        if pos is None:
            raise OpinionError("позиции нет")
        topic = pos["topic_root_id"]
        cfg = await settings(conn, topic)
        see = await _may_see_numbers(conn, topic, viewer_id, cfg)
        titles = await _titles(conn, topic)
        root = await conn.fetchrow("SELECT id, title, text FROM nodes WHERE id = $1", topic)
        n_members = await conn.fetchval(
            "SELECT count(*) FROM position_nodes WHERE position_id = $1", pid)
        parts = [titles[r["id"]] for r in await conn.fetch(
            "SELECT id FROM positions WHERE split_from = $1 ORDER BY id", pid)]
        head = {"id": pid, "title": pos["headline"], "summary": pos["summary"] or pos["composed"],
                "status": pos["status"], "created_at": pos["created_at"].isoformat(),
                "members": n_members, "parts": parts,
                "merged_into": titles.get(pos["merged_into"]),
                "split_from": titles.get(pos["split_from"]),
                "topic": {"id": topic, "title": root["title"] or root["text"][:80]}}
        if not see:
            return {"position": head, "blind": True, "caption": CAPTION}

        stats = dict(await conn.fetchrow(
            "SELECT in_now, stood, converted FROM position_stats WHERE position_id = $1", pid)
            or {})
        numbers = _numbers(stats)
        daily = [dict(r) for r in await conn.fetch(
            "SELECT day, in_now, stood, converted FROM position_stats_daily "
            "WHERE position_id = $1 ORDER BY day", pid)]
        today = datetime.now(timezone.utc).date()
        series = [{"day": r["day"].isoformat(), **_numbers(r)} for r in daily]
        series.append({"day": today.isoformat(), **numbers})
        at_numbers = None
        if at:
            pick = [x for x in series if x["day"] <= at]
            at_numbers = pick[-1] if pick else _numbers({})

        # движение за период
        rows = await _rows_for(conn, [pid], since)
        fl = om.flows(rows, pid, since, limit=4)
        delta = _net(rows, pid, since)
        movers = await conn.fetch(
            "SELECT node_id, direction, n FROM cause_counts WHERE position_id = $1 "
            "ORDER BY n DESC, node_id LIMIT 6", pid)
        top = [r["node_id"] for r in await conn.fetch(
            "SELECT node_id FROM position_top WHERE position_id = $1 ORDER BY rank", pid)]
        errors = await conn.fetch(
            """
            SELECT DISTINCT ON (ad.node_id) ad.node_id, ad.text, ad.created_at
            FROM position_events pe
            JOIN node_addenda ad ON ad.node_id = (pe.payload->>'node_id')::int
                                AND ad.kind = 'error_ack'
            WHERE pe.position_id = $1 AND pe.kind = 'member_remove'
              AND pe.payload->>'reason' = 'error_ack'
            ORDER BY ad.node_id, ad.created_at
            """, pid)
        cards = await _node_cards(
            conn, [g["cause_node_id"] for s in ("in", "out") for g in fl[s]["groups"]]
            + [r["node_id"] for r in movers] + top + [r["node_id"] for r in errors])

        top_view = []
        for rank, nid in enumerate(top):
            out_n = await conn.fetchval(
                "SELECT coalesce(sum(n), 0) FROM cause_counts WHERE position_id = $1 "
                "AND node_id = $2 AND direction = 'out'", pid, nid)
            stayed = await conn.fetchval(
                "SELECT count(*) FROM exposure_state es JOIN current_stance cs "
                "ON cs.user_id = es.user_id AND cs.position_id = $1 "
                "WHERE es.node_id = $2 AND es.response = ANY($3::text[])",
                pid, nid, list(om.STOOD))
            top_view.append({**cards[nid], "rank": rank, "led_away": out_n, "stayed": stayed})

        personal = None
        if viewer_id is not None:
            me = await conn.fetchrow(
                "SELECT position_id, unclarified, since FROM current_stance "
                "WHERE user_id = $1 AND topic_root_id = $2", viewer_id, topic)
            if me and me["position_id"] == pid:
                answered = {r["node_id"]: r["response"] for r in await conn.fetch(
                    "SELECT node_id, response FROM exposure_state WHERE user_id = $1 "
                    "AND node_id = ANY($2::int[])", viewer_id, top)}
                unseen = next((t for t in top_view
                               if answered.get(t["id"]) in (None, "shown")), None)
                personal = {"in_position": True, "unclarified": me["unclarified"],
                            "since": me["since"].isoformat(), "objection": unseen,
                            "answers": answered,
                            "choices": parts if me["unclarified"] else []}

        errs = []
        for r in errors:
            n = await conn.fetchval(
                "SELECT count(*) FROM stance_log WHERE cause_node_id = $1 AND created_at > $2 "
                "AND source = 'person'", r["node_id"], r["created_at"])
            errs.append({**cards[r["node_id"]], "note": r["text"],
                         "at": r["created_at"].isoformat(), "moved_after": n})

        return {
            "position": head, "blind": False, "period": period,
            "numbers": numbers, "at": at, "at_numbers": at_numbers, "delta": delta,
            "top_objections": top_view, "personal": personal,
            "movement": _flow_view(fl, titles, cards),
            "series": series,
            "cruxes": await _cruxes(conn, topic, pid, limit=5,
                                    share=cfg["accept_share"], titles=titles),
            "movers": [{**cards[r["node_id"]], "direction": r["direction"], "n": r["n"]}
                       for r in movers],
            "errors": errs,
            "caption": CAPTION,
        }


async def my_view(topic, viewer_id):
    """Что человек видит о себе: где стоит, что надо уточнить, своя история и
    «похоже, вы здесь» — позиции, в которых лежат его доводы, если сам он ещё
    нигде не стоит (система только предлагает, решает человек)."""
    async with _pool().acquire() as conn:
        titles = await _titles(conn, topic)
        me = await conn.fetchrow(
            "SELECT position_id, unclarified, since FROM current_stance "
            "WHERE user_id = $1 AND topic_root_id = $2", viewer_id, topic)
        history = [{"from": titles.get(r["from_position_id"]),
                    "to": titles.get(r["to_position_id"]),
                    "cause_node_id": r["cause_node_id"], "source": r["source"],
                    "at": r["created_at"].isoformat()}
                   for r in await conn.fetch(
                       "SELECT * FROM stance_log WHERE user_id = $1 AND topic_root_id = $2 "
                       "ORDER BY id DESC LIMIT 50", viewer_id, topic)]
        suggest = []
        if me is None:
            suggest = [titles[r["position_id"]] | {"my_nodes": r["n"]} for r in await conn.fetch(
                """
                SELECT pn.position_id, count(*) AS n FROM position_nodes pn
                JOIN nodes n ON n.id = pn.node_id
                JOIN positions p ON p.id = pn.position_id
                WHERE n.author_id = $1 AND pn.topic_root_id = $2
                  AND p.status = ANY($3::text[])
                GROUP BY pn.position_id ORDER BY n DESC LIMIT 3
                """, viewer_id, topic, list(om.OPEN_STATUSES))]
        parts = []
        if me and me["unclarified"]:
            parts = [titles[r["id"]] for r in await conn.fetch(
                "SELECT id FROM positions WHERE split_from = $1 ORDER BY id", me["position_id"])]
        open_positions = [t for t in titles.values() if t["status"] in om.OPEN_STATUSES]
    return {"position": titles.get(me["position_id"]) if me else None,
            "unclarified": bool(me and me["unclarified"]), "choices": parts,
            "suggest": suggest, "history": history,
            "open_positions": sorted(open_positions, key=lambda t: t["id"])}
