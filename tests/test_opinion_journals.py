"""
Карта мнений, фаза 1: журналы, производные таблицы, хэш-цепочка
(vault: decisions/2026-09-25-opinion-map).

Инварианты ТЗ, которые здесь проверяются:
  - журналы только дописываются — UPDATE / DELETE / TRUNCATE падают;
  - current_stance всегда равна последней строке stance_log, в том числе при
    параллельных переходах одного человека;
  - все числа позиции, посчитанные инкрементально, совпадают с пересчётом из
    журналов с нуля (opinionmap.replay);
  - слияние, раскол и отмена не считаются «переубеждён»;
  - ID позиций не стираются; «признаю ошибку» выводит узел из состава;
  - цепочка событий цела и ловит подмену.

Идут только при TEST_DATABASE_URL — стирают базу.
"""

import asyncio
import os
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

TEST_DB = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DB, reason="TEST_DATABASE_URL not set")


def run(coro_fn):
    async def wrapper():
        from app import db
        db.DATABASE_URL = TEST_DB
        await db.close_pool()
        await db.init_pool()
        await db.init_db()
        await db.wipe(force=True)
        try:
            return await coro_fn()
        finally:
            await db.close_pool()
    return asyncio.run(wrapper())


async def _world(people=6, positions=3):
    """Обсуждение, позиции с одним доводом и возражением «против» на каждый."""
    from app import db
    users = [await db.add_author(f"u{i}") for i in range(people)]
    root = await db.add_node("Корень обсуждения", author_id=users[0], title="Тема")
    pids, members, objs = [], [], []
    for i in range(positions):
        pid = await db.add_position(root, f"Позиция {i}", f"Текст {i}", "mixed")
        nid = await db.add_node(f"Довод позиции {i}", author_id=users[0],
                                topic_root_id=root)
        await db.add_edge(nid, root, "support")
        await db.set_node_position(nid, pid)
        oid = await db.add_node(f"Возражение к {i}", author_id=users[1],
                                topic_root_id=root)
        await db.add_edge(oid, nid, "refute")
        pids.append(pid)
        members.append(nid)
        objs.append(oid)
    return {"users": users, "root": root, "pids": pids, "members": members,
            "objs": objs}


async def _stats(pid):
    from app import db
    async with db._pool_or_raise().acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM position_stats WHERE position_id = $1", pid)
    return dict(row) if row else {"in_now": 0, "stood": 0, "converted": 0}


async def _assert_matches_replay(root):
    """Инкрементальные счётчики == пересчёт из журналов с нуля."""
    from app import db, opinion_db
    ref = await opinion_db.recompute(root)
    async with db._pool_or_raise().acquire() as conn:
        stored = {r["position_id"]: dict(r) for r in await conn.fetch(
            "SELECT * FROM position_stats WHERE topic_root_id = $1", root)}
        cur = {(r["user_id"], r["topic_root_id"]): r["position_id"] for r in await conn.fetch(
            "SELECT * FROM current_stance WHERE topic_root_id = $1", root)}
        tops = {}
        for r in await conn.fetch("SELECT * FROM position_top ORDER BY position_id, rank"):
            tops.setdefault(r["position_id"], []).append(r["node_id"])
    assert cur == ref["current"]
    for pid, s in ref["positions"].items():
        got = stored.get(pid, {"in_now": 0, "stood": 0, "converted": 0})
        assert (got["in_now"], got["stood"], got["converted"]) == \
               (s["in_now"], s["stood"], s["converted"]), (pid, got, s)
        assert tops.get(pid, []) == s["top"], pid


# ------------------------------------------------------------------ журналы
def test_journals_refuse_update_delete_truncate():
    async def body():
        import asyncpg
        from app import db, opinion_db
        w = await _world(people=2, positions=1)
        u, p, o = w["users"][0], w["pids"][0], w["objs"][0]
        await opinion_db.set_stance(u, w["root"], p)
        await opinion_db.record_exposure(u, o, "not_convinced")
        await opinion_db.join_node(u, o)
        q = await db.add_node("Вопрос?", author_id=u, kind="question",
                              topic_root_id=w["root"])
        ans = await db.add_node("Ответ.", author_id=w["users"][1], topic_root_id=w["root"])
        await db.add_edge(ans, q, "support")
        await opinion_db.accept_answer(u, q, ans)
        async with db._pool_or_raise().acquire() as conn:
            for t in opinion_db.JOURNALS:
                # строчный триггер не срабатывает на пустой таблице — нужны строки
                assert await conn.fetchval(f"SELECT count(*) FROM {t}") > 0, t
                for sql in (f"UPDATE {t} SET id = id",
                            f"DELETE FROM {t}",
                            f"TRUNCATE {t}"):
                    with pytest.raises(asyncpg.PostgresError) as e:
                        await conn.execute(sql)
                    assert "только дописывается" in str(e.value)
    run(body)


def test_current_stance_follows_log_and_matches_replay():
    async def body():
        from app import db, opinion_db
        w = await _world()
        root, (a, b, c) = w["root"], w["pids"]
        u = w["users"]
        await opinion_db.set_stance(u[0], root, a)
        await opinion_db.set_stance(u[1], root, a)
        await opinion_db.set_stance(u[2], root, b)
        again = await opinion_db.set_stance(u[0], root, a)
        assert again["unchanged"]                         # повтор ничего не пишет
        await opinion_db.set_stance(u[0], root, b, cause_node_id=w["objs"][0])
        await opinion_db.set_stance(u[1], root, None)      # ушёл без позиции
        await opinion_db.set_stance(u[3], root, c)
        await opinion_db.set_stance(u[0], root, a)         # вернулся
        async with db._pool_or_raise().acquire() as conn:
            n_log = await conn.fetchval("SELECT count(*) FROM stance_log")
        assert n_log == 7
        sa = await _stats(a)
        assert (sa["in_now"], sa["converted"]) == (1, 1)   # u0 вернулся, u1 ушёл
        await _assert_matches_replay(root)
    run(body)


def test_parallel_moves_of_one_person_stay_consistent():
    async def body():
        from app import db, opinion_db
        w = await _world(people=3)
        root, pids = w["root"], w["pids"]
        u = w["users"][2]
        moves = [pids[i % 3] for i in range(12)] + [None, pids[1]]
        await asyncio.gather(*(opinion_db.set_stance(u, root, p) for p in moves))
        async with db._pool_or_raise().acquire() as conn:
            last = await conn.fetchrow(
                "SELECT to_position_id FROM stance_log WHERE user_id = $1 "
                "ORDER BY id DESC LIMIT 1", u)
            cur = await conn.fetchval(
                "SELECT position_id FROM current_stance WHERE user_id = $1", u)
            total = await conn.fetchval(
                "SELECT sum(in_now) FROM position_stats WHERE topic_root_id = $1", root)
        assert cur == last["to_position_id"]
        assert total == (1 if cur else 0)
        await _assert_matches_replay(root)
    run(body)


def test_exposures_stood_and_shown_does_not_override():
    async def body():
        from app import opinion_db
        w = await _world()
        root, a, o = w["root"], w["pids"][0], w["objs"][0]
        u = w["users"]
        for x in u[:3]:
            await opinion_db.set_stance(x, root, a)
        await opinion_db.record_exposure(u[0], o, "not_convinced")
        r = await opinion_db.record_exposure(u[0], o, "shown")
        assert r["recorded"] is False and r["response"] == "not_convinced"
        await opinion_db.record_exposure(u[1], o, "shown")
        await opinion_db.record_exposure(u[2], o, "partial")
        s = await _stats(a)
        assert (s["in_now"], s["stood"]) == (3, 2)
        # ответ, данный до входа в позицию, тоже засчитывается при входе
        await opinion_db.record_exposure(u[3], o, "not_convinced")
        await opinion_db.set_stance(u[3], root, a)
        assert (await _stats(a))["stood"] == 3
        await _assert_matches_replay(root)
    run(body)


def test_convinced_then_undo_is_not_converted():
    async def body():
        from app import db, opinion_db
        w = await _world()
        root, (a, b, _c) = w["root"], w["pids"]
        o = w["objs"][0]
        u = w["users"][0]
        await opinion_db.set_stance(u, root, a)
        await opinion_db.record_exposure(u, o, "convinced")
        await opinion_db.set_stance(u, root, b, cause_node_id=o)
        assert (await _stats(a))["converted"] == 1
        res = await opinion_db.undo_response(u, o)
        assert res["moved_back"]["to"] == a and res["response"] is None
        sa = await _stats(a)
        assert (sa["in_now"], sa["converted"]) == (1, 0)
        async with db._pool_or_raise().acquire() as conn:
            assert await conn.fetchval(
                "SELECT count(*) FROM cause_counts WHERE node_id = $1", o) == 0
            srcs = [r["source"] for r in await conn.fetch(
                "SELECT source FROM stance_log ORDER BY id")]
        assert srcs == ["person", "person", "undo"]
        await _assert_matches_replay(root)
    run(body)


def test_top_objections_ranked_by_departures():
    async def body():
        from app import db, opinion_db
        w = await _world(people=8, positions=2)
        root, (a, b) = w["root"], w["pids"]
        member = w["members"][0]
        extra = []
        for i in range(4):                      # ещё четыре возражения на тот же довод
            nid = await db.add_node(f"Ещё возражение {i}", author_id=w["users"][2],
                                    topic_root_id=root)
            await db.add_edge(nid, member, "undercut" if i % 2 else "refute")
            extra.append(nid)
        for x in w["users"]:
            await opinion_db.set_stance(x, root, a)
        # двое ушли из-за extra[3], один — из-за extra[2]
        await opinion_db.set_stance(w["users"][0], root, b, cause_node_id=extra[3])
        await opinion_db.set_stance(w["users"][1], root, b, cause_node_id=extra[3])
        await opinion_db.set_stance(w["users"][2], root, b, cause_node_id=extra[2])
        async with db._pool_or_raise().acquire() as conn:
            top = [r["node_id"] for r in await conn.fetch(
                "SELECT node_id FROM position_top WHERE position_id = $1 ORDER BY rank", a)]
        assert top[:2] == [extra[3], extra[2]] and len(top) == 3
        await _assert_matches_replay(root)
    run(body)


def test_status_forming_active_empty():
    async def body():
        from app import db, opinion_db
        w = await _world()
        root, a = w["root"], w["pids"][0]

        async def status():
            return (await db.get_position(a))["status"]
        assert await status() == "forming"
        for x in w["users"][:3]:
            await opinion_db.set_stance(x, root, a)
        assert await status() == "active"
        for x in w["users"][:3]:
            await opinion_db.set_stance(x, root, None)
        assert await status() == "empty"
        await opinion_db.set_stance(w["users"][4], root, a)
        assert await status() == "active"
    run(body)


def test_merge_and_split_are_not_persuasion():
    async def body():
        from app import db, opinion_db
        w = await _world(people=8)
        root, (a, b, c) = w["root"], w["pids"]
        u = w["users"]
        for x in u[:3]:
            await opinion_db.set_stance(x, root, a)
        for x in u[3:6]:
            await opinion_db.set_stance(x, root, b)
        await opinion_db.merge_positions(a, b)
        assert (await db.get_position(a))["status"] == "merged"
        sb = await _stats(b)
        assert (sb["in_now"], (await _stats(a))["converted"]) == (6, 0)
        with pytest.raises(opinion_db.OpinionError):
            await opinion_db.set_stance(u[7], root, a)     # в слитую не встают

        res = await opinion_db.split_position(b, [
            {"headline": "Часть 1", "node_ids": [w["members"][0]]},
            {"headline": "Часть 2", "node_ids": [w["members"][1]]}])
        p1, p2 = res["parts"]
        assert res["unclarified"] == 6
        assert (await _stats(b))["in_now"] == 6            # до выбора — в исходной
        await opinion_db.set_stance(u[0], root, p1)
        await opinion_db.set_stance(u[3], root, p2)
        async with db._pool_or_raise().acquire() as conn:
            srcs = [r["source"] for r in await conn.fetch(
                "SELECT source FROM stance_log WHERE user_id = ANY($1::int[]) ORDER BY id",
                [u[0], u[3]])]
            unclar = await conn.fetchval(
                "SELECT count(*) FROM current_stance WHERE unclarified")
        assert srcs.count("split") == 2 and unclar == 4
        assert (await _stats(b))["converted"] == 0
        await _assert_matches_replay(root)
    run(body)


def test_error_ack_leaves_position_but_stays_in_graph():
    async def body():
        from app import db
        w = await _world(positions=1)
        nid = w["members"][0]
        await db.add_addendum(nid, w["users"][0], "цифра из одного региона", kind="error_ack")
        async with db._pool_or_raise().acquire() as conn:
            left = await conn.fetchval(
                "SELECT count(*) FROM position_nodes WHERE node_id = $1", nid)
            ev = await conn.fetchval(
                "SELECT payload->>'reason' FROM position_events "
                "WHERE kind = 'member_remove' AND payload->>'node_id' = $1", str(nid))
        assert left == 0 and ev == "error_ack"
        assert (await db.get_node(nid))["deleted_at"] is None
    run(body)


def test_retire_keeps_position_id():
    async def body():
        from app import db
        w = await _world(positions=1)
        a = w["pids"][0]
        await db.retire_position(a)
        p = await db.get_position(a)
        assert p is not None and p["status"] == "empty"
    run(body)


def test_question_acceptance_needs_asker_and_counts_share():
    async def body():
        from app import db, opinion_db
        w = await _world(people=5, positions=1)
        root, u = w["root"], w["users"]
        q = await db.add_node("Кто заметит нарушение?", author_id=u[0],
                              kind="question", topic_root_id=root)
        await db.add_edge(q, w["members"][0], "question")
        ans = await db.add_node("Журналисты и конкуренты.", author_id=u[4],
                                topic_root_id=root)
        await db.add_edge(ans, q, "support")
        with pytest.raises(opinion_db.OpinionError):
            await opinion_db.accept_answer(u[1], q, ans)   # не спрашивал
        await opinion_db.join_node(u[1], q)
        await opinion_db.join_node(u[2], q)
        await opinion_db.join_node(u[2], q)                # повтор не задваивает
        await opinion_db.accept_answer(u[0], q, ans)
        async with db._pool_or_raise().acquire() as conn:
            st = await opinion_db.question_status(conn, q)
        assert (st["askers"], st["accepted"], st["status"]) == (3, 1, "open")
        await opinion_db.accept_answer(u[1], q, ans)
        async with db._pool_or_raise().acquire() as conn:
            st = await opinion_db.question_status(conn, q)
        assert st["status"] == "found" and st["answer_id"] == ans
        await opinion_db.accept_answer(u[1], q, ans, "withdraw")
        async with db._pool_or_raise().acquire() as conn:
            st = await opinion_db.question_status(conn, q)
        assert st["status"] == "open"
    run(body)


def test_random_history_incremental_equals_replay():
    """Случайная история: переходы, ответы, отмены, слияние — счётчики сходятся."""
    async def body():
        from app import db, opinion_db
        rnd = random.Random(20260925)
        w = await _world(people=25, positions=4)
        root, pids, objs, users = w["root"], w["pids"], w["objs"], w["users"]
        for step in range(250):
            u = rnd.choice(users)
            r = rnd.random()
            if r < 0.45:
                to = rnd.choice(pids + [None])
                cause = rnd.choice(objs + [None]) if to is not None else None
                try:
                    await opinion_db.set_stance(u, root, to, cause_node_id=cause)
                except opinion_db.OpinionError:
                    pass
            elif r < 0.85:
                await opinion_db.record_exposure(
                    u, rnd.choice(objs),
                    rnd.choice(["shown", "convinced", "partial", "not_convinced"]))
            else:
                await opinion_db.undo_response(u, rnd.choice(objs))
            if step == 150:
                await opinion_db.merge_positions(pids[3], pids[2])
                pids = pids[:3]
        await _assert_matches_replay(root)
    run(body)


# ------------------------------------------------------------------ цепочка
def test_hash_chain_verifies_and_catches_tampering():
    async def body():
        from app import chain, db, opinion_db
        w = await _world(people=3, positions=1)
        await opinion_db.set_stance(w["users"][0], w["root"], w["pids"][0])
        await db.close_pool()
        res = await chain.verify()
        assert res["checked"] > 5 and res["broken"] == []
        async with db._pool_or_raise().acquire() as conn:
            async with conn.transaction():
                await conn.execute("SET LOCAL noosphere.allow_wipe = 'on'")
                victim = await conn.fetchval(
                    "SELECT id FROM events WHERE type = 'stance_set' LIMIT 1")
                await conn.execute(
                    "UPDATE events SET payload = payload || '{\"to\": null}'::jsonb "
                    "WHERE id = $1", victim)
        await db.close_pool()
        res = await chain.verify()
        assert res["broken"] == [victim]
    run(body)
