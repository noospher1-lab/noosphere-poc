"""
Карта мнений, фазы 3–5: API экранов A (обсуждение) и B (позиция)
(vault: decisions/2026-09-25-opinion-map).

Идут только при TEST_DATABASE_URL — стирают базу.
"""

import asyncio
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from app import auth  # noqa: E402

TEST_DB = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DB, reason="TEST_DATABASE_URL not set")


async def _prepare(n_positions=3, crowd=9):
    """Вошедший «Вера» + толпа, позиции с доводом и возражением на каждый."""
    from app import db, opinion_db
    db.DATABASE_URL = TEST_DB
    await db.close_pool()
    await db.init_pool()
    await db.init_db()
    await db.wipe(force=True)
    uid = await db.add_user("opinion_vera", auth.hash_password("secret1"), "Вера",
                            "#fff", email="vera@example.com", invite_required=False)
    async with db._pool_or_raise().acquire() as conn:
        await conn.execute("UPDATE authors SET email_verified = TRUE, balance_usd = 5 "
                           "WHERE id = $1", uid)
    others = [await db.add_author(f"o{i}") for i in range(crowd)]
    root = await db.add_node("Мелкие чиновники договариваются с поставщиками.",
                             author_id=others[0], kind="problem", title="Коррупция в закупках")
    pids, members, objs = [], [], []
    for i in range(n_positions):
        pid = await db.add_position(root, f"Позиция {i}", f"Текст {i}", "mixed")
        nid = await db.add_node(f"Довод {i}", author_id=others[0], topic_root_id=root)
        await db.add_edge(nid, root, "support")
        await db.set_node_position(nid, pid)
        oid = await db.add_node(f"Возражение {i}", author_id=others[1], topic_root_id=root,
                                poi_score=70.0)
        await db.add_edge(oid, nid, "refute")
        pids.append(pid)
        members.append(nid)
        objs.append(oid)
    await db.close_pool()
    return {"uid": uid, "others": others, "root": root, "pids": pids,
            "members": members, "objs": objs}


@pytest.fixture
def world():
    from fastapi.testclient import TestClient
    from app import db, main
    w = asyncio.run(_prepare())
    main._login_calls.clear()
    db.DATABASE_URL = TEST_DB
    c = TestClient(main.app)
    c.__enter__()
    r = c.post("/api/auth/login", json={"username": "opinion_vera", "password": "secret1"})
    assert r.status_code == 200, r.text
    w["c"] = c
    yield w
    c.__exit__(None, None, None)


def _crowd(w, moves):
    """Действия толпы — в цикле приложения (пул базы у TestClient свой)."""
    from app import opinion_db
    c = w["c"]

    async def go():
        for m in moves:
            await getattr(opinion_db, m[0])(*m[1:])
    c.portal.call(go)


def test_cold_start_then_map(world):
    c, root, pids, others = world["c"], world["root"], world["pids"], world["others"]
    r = c.get(f"/api/opinion/topic/{root}").json()
    assert r["cold_start"] is True and r["positions"] == []
    assert r["forming_positions"] == 3
    # складывающиеся позиции видны, иначе встать в них не из чего
    assert [f["id"] for f in r["forming"]] == pids and r["min_supporters"] == 3
    assert "аккаунты, а не проверенные люди" in r["caption"]
    # по трое в каждой позиции — все три выходят на карту, холодный старт кончается
    moves = []
    for i, pid in enumerate(pids):
        for u in others[3 * i:3 * i + 3]:
            moves.append(("set_stance", u, root, pid))
    _crowd(world, moves)
    r = c.get(f"/api/opinion/topic/{root}?sort=size").json()
    assert r["cold_start"] is False and len(r["positions"]) == 3
    assert all(p["in_now"] == 3 for p in r["positions"])
    assert r["positions"][0]["unchecked"] == 3


def test_position_screen_personal_block_and_buttons(world):
    c, root, pids, objs = world["c"], world["root"], world["pids"], world["objs"]
    others = world["others"]
    _crowd(world, [("set_stance", u, root, pids[0]) for u in others[:3]])
    assert c.post("/api/opinion/stance",
                  json={"topic_root_id": root, "position_id": pids[0]}).status_code == 200
    b = c.get(f"/api/opinion/position/{pids[0]}").json()
    assert b["numbers"]["in_now"] == 4
    assert b["personal"]["objection"]["id"] == objs[0]
    assert b["personal"]["objection"]["label"] == "против"
    # «не убедило» → устоял
    r = c.post("/api/opinion/exposure", json={"node_id": objs[0], "response": "not_convinced"})
    assert r.status_code == 200
    b = c.get(f"/api/opinion/position/{pids[0]}").json()
    assert b["numbers"]["stood"] == 1 and b["personal"]["objection"] is None
    # «убедило» → переход с причиной; затем «отменить» возвращает обратно
    c.post("/api/opinion/exposure", json={"node_id": objs[0], "response": "convinced"})
    r = c.post("/api/opinion/stance", json={"topic_root_id": root, "position_id": pids[1],
                                            "cause_node_id": objs[0]})
    assert r.status_code == 200 and r.json()["source"] == "person"
    b = c.get(f"/api/opinion/position/{pids[0]}?period=all").json()
    assert b["numbers"]["converted"] == 1 and b["personal"] is None
    assert b["movers"][0]["id"] == objs[0] and b["movers"][0]["direction"] == "out"
    r = c.post("/api/opinion/undo", json={"node_id": objs[0]}).json()
    assert r["moved_back"]["to"] == pids[0]
    b = c.get(f"/api/opinion/position/{pids[0]}").json()
    assert b["numbers"]["converted"] == 0 and b["numbers"]["in_now"] == 4


def test_flows_fold_small_groups(world):
    c, root, pids, objs = world["c"], world["root"], world["pids"], world["objs"]
    others = world["others"]
    moves = [("set_stance", u, root, pids[0]) for u in others[:4]]
    moves += [("set_stance", u, root, pids[1], objs[0]) for u in others[:3]]
    moves += [("set_stance", others[3], root, pids[2])]
    _crowd(world, moves)
    b = c.get(f"/api/opinion/position/{pids[0]}?period=7").json()
    out = b["movement"]["out"]
    assert [g["position_id"] for g in out["groups"]] == [pids[1]]
    assert out["groups"][0]["n"] == 3 and out["groups"][0]["cause"]["id"] == objs[0]
    assert out["rest"] == 1 and out["total"] == 4          # одиночка свёрнут в «ещё N»
    assert b["delta"] == 0                                 # пришли 4, ушли 4


def test_blind_first_answer_hides_numbers_until_own_answer(world):
    from app import main
    c, root, pids = world["c"], world["root"], world["pids"]
    main.ADMIN_TOKEN = "t"
    r = c.put(f"/api/opinion/settings/{root}", json={"blind_first_answer": True},
              headers={"X-Admin-Token": "t"})
    assert r.status_code == 200 and r.json()["blind_first_answer"] is True
    top = c.get(f"/api/opinion/topic/{root}").json()
    assert top["blind"] is True and top["cruxes"] == []
    assert c.get(f"/api/opinion/position/{pids[0]}").json()["blind"] is True
    # свой ответ в обсуждении открывает числа (в позицию он не записывает)
    r = c.post("/api/argument", json={"text": "Моё мнение: нужна открытость.",
                                      "connect_to": root, "edge_type": "support"})
    assert r.status_code == 200, r.text
    top = c.get(f"/api/opinion/topic/{root}").json()
    assert top["blind"] is False and top["my_position"] is None


def test_me_suggests_where_my_arguments_are(world):
    from app import db
    c, root, pids = world["c"], world["root"], world["pids"]

    async def mine():
        nid = await db.add_node("Мой довод", author_id=world["uid"], topic_root_id=root)
        await db.set_node_position(nid, pids[2])
    c.portal.call(mine)
    me = c.get(f"/api/opinion/me/{root}").json()
    assert me["position"] is None
    assert [s["id"] for s in me["suggest"]] == [pids[2]]


def test_question_crux_and_error_ack_on_position_screen(world):
    from app import db
    c, root, pids, members = world["c"], world["root"], world["pids"], world["members"]
    others = world["others"]
    ids = {}

    async def setup():
        q = await db.add_node("Кто заметит нарушение?", author_id=others[2],
                              kind="question", topic_root_id=root)
        await db.add_edge(q, members[0], "question")
        a = await db.add_node("Конкуренты.", author_id=others[3], topic_root_id=root)
        await db.add_edge(a, q, "support")
        ids.update(q=q, a=a)
    c.portal.call(setup)
    assert c.post("/api/opinion/join", json={"node_id": ids["q"]}).json()["joins"] == 1
    r = c.post("/api/opinion/accept", json={"question_id": ids["q"], "answer_id": ids["a"]})
    assert r.status_code == 200
    b = c.get(f"/api/opinion/position/{pids[0]}").json()
    q = next(x for x in b["cruxes"] if x["id"] == ids["q"])
    assert q["question"]["askers"] == 2 and q["question"]["status"] == "found"

    # «признаю ошибку» от автора довода — через обычный маршрут примечаний
    async def ack():
        await db.add_addendum(members[0], others[0], "цифра из одного региона", kind="error_ack")
    c.portal.call(ack)
    b = c.get(f"/api/opinion/position/{pids[0]}").json()
    assert b["position"]["members"] == 0
    assert b["errors"][0]["id"] == members[0] and b["errors"][0]["moved_after"] == 0
    bad = c.post(f"/api/nodes/{members[0]}/addendum", json={"text": "x", "kind": "boom"})
    assert bad.status_code == 400


def test_merge_and_split_admin_only(world):
    from app import main
    c, pids = world["c"], world["pids"]
    main.ADMIN_TOKEN = "t"
    assert c.post("/api/opinion/merge", json={"source_id": pids[2], "target_id": pids[1]}
                  ).status_code == 403
    r = c.post("/api/opinion/merge", json={"source_id": pids[2], "target_id": pids[1]},
               headers={"X-Admin-Token": "t"})
    assert r.status_code == 200
    r = c.post("/api/opinion/split", headers={"X-Admin-Token": "t"}, json={
        "position_id": pids[0], "parts": [{"headline": "А", "node_ids": []},
                                          {"headline": "Б", "node_ids": []}]})
    assert r.status_code == 200 and len(r.json()["parts"]) == 2
    b = c.get(f"/api/opinion/position/{pids[0]}").json()
    assert b["position"]["status"] == "split" and len(b["position"]["parts"]) == 2


def test_router_suggests_join_or_place_and_never_publishes(world):
    from app import db
    c, root, pids, members, objs = (world["c"], world["root"], world["pids"],
                                    world["members"], world["objs"])

    async def count():
        async with db._pool_or_raise().acquire() as conn:
            return await conn.fetchval("SELECT count(*) FROM nodes")
    before = c.portal.call(count)
    # дословный повтор возражения (модели нет — поиск по буквам) → «присоединиться»
    r = c.post("/api/opinion/route", json={"topic_root_id": root, "text": "  возражение 0 ",
                                           "action": "refute", "target_id": members[0]})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["embed"] is False
    assert [x["id"] for x in d["duplicates"]] == [objs[0]]
    # новое — место: цель ответа, тип по действию, позиция цели
    r = c.post("/api/opinion/route", json={"topic_root_id": root, "text": "Совсем новая мысль",
                                           "action": "qualify", "target_id": members[1]}).json()
    assert r["duplicates"] == []
    assert r["placement"]["target"]["id"] == members[1]
    assert (r["placement"]["edge_type"], r["placement"]["edge_label"]) == ("qualify", "уточнение")
    assert r["placement"]["position"]["id"] == pids[1]
    assert c.portal.call(count) == before                  # маршрутизатор ничего не создал
    bad = c.post("/api/opinion/route", json={"topic_root_id": root, "text": "x", "action": "boom"})
    assert bad.status_code == 400
