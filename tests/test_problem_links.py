"""
Связи между проблемами: «Б порождает А» (vault: drafts/problem-causal-links).

Уровень не хранится — выводится из связей; связь рождается из ответа и несёт
его как обоснование; заводить причину может только автор ответа; перед
созданием новой причины ищем, к какой существующей сойтись. Цикл не ошибка,
а структура, которую надо показать.

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


def _run(coro_fn):
    async def wrapper():
        from app import db
        try:
            await coro_fn()
        finally:
            await db.close_pool()
    asyncio.run(wrapper())


async def _fresh_db():
    from app import db
    db.DATABASE_URL = TEST_DB
    await db.close_pool()
    await db.init_pool()
    await db.init_db()
    await db.wipe(force=True)
    return db


def test_links_carry_justification_and_dispute_is_derived():
    async def body():
        db = await _fresh_db()
        uid = await db.add_user("linker", "hash", "Автор", invite_required=False)
        other = await db.add_user("critic", "hash", "Критик", invite_required=False)
        a = await db.add_node("Запрет языка", author_id=uid, kind="problem",
                              title="Запрет языка")
        b = await db.add_node("Представители не представляют", author_id=uid,
                              kind="problem", title="Представители не представляют")
        just = await db.add_node("Проблема не в законах, а в том, что…",
                                 author_id=uid, kind="argument", topic_root_id=a)
        await db.add_edge(just, a, "support")

        link = await db.add_problem_link(b, a, node_id=just, author_id=uid)
        assert link["existed"] is False
        # повтор той же пары — не дубль, а та же связь
        again = await db.add_problem_link(b, a, node_id=just, author_id=other)
        assert again["existed"] is True and again["id"] == link["id"]

        la = await db.problem_links_of(a)
        assert [c["cause_id"] for c in la["causes"]] == [b]
        assert la["effects"] == []
        c = la["causes"][0]
        assert c["node_id"] == just and c["disputed"] == 0
        assert c["problem_title"] == "Представители не представляют"

        lb = await db.problem_links_of(b)
        assert [e["effect_id"] for e in lb["effects"]] == [a]

        # спор о связи — обычными рёбрами под обоснованием, статус выводится
        rebut = await db.add_node("Нет, законы и есть проблема", author_id=other,
                                  kind="argument", topic_root_id=a)
        await db.add_edge(rebut, just, "refute")
        c = (await db.problem_links_of(a))["causes"][0]
        assert c["disputed"] == 1
    _run(body)


def test_chain_walks_up_and_shows_cycles_without_looping():
    async def body():
        db = await _fresh_db()
        uid = await db.add_user("chainer", "hash", "Автор", invite_required=False)
        a = await db.add_node("A", author_id=uid, kind="problem", title="A")
        b = await db.add_node("B", author_id=uid, kind="problem", title="B")
        c = await db.add_node("C", author_id=uid, kind="problem", title="C")
        await db.add_problem_link(b, a, author_id=uid)   # B порождает A
        await db.add_problem_link(c, b, author_id=uid)   # C порождает B
        chain = await db.problem_chain(a)
        assert [(x["id"], x["depth"], x["cycle"]) for x in chain] == \
            [(b, 1, False), (c, 2, False)]
        # замыкаем круг: A порождает C — обход не зацикливается, круг виден
        await db.add_problem_link(a, c, author_id=uid)
        chain = await db.problem_chain(a)
        assert [(x["id"], x["depth"], x["cycle"]) for x in chain] == \
            [(b, 1, False), (c, 2, False), (a, 3, True)]
    _run(body)


def test_link_hides_when_its_justification_is_removed():
    async def body():
        db = await _fresh_db()
        uid = await db.add_user("remover", "hash", "Автор", invite_required=False)
        a = await db.add_node("A", author_id=uid, kind="problem", title="A")
        b = await db.add_node("B", author_id=uid, kind="problem", title="B")
        just = await db.add_node("обоснование", author_id=uid, topic_root_id=a)
        await db.add_edge(just, a, "support")
        await db.add_problem_link(b, a, node_id=just, author_id=uid)
        pool = db._pool_or_raise()
        async with pool.acquire() as conn:
            await conn.execute("UPDATE nodes SET deleted_at = now() WHERE id = $1", just)
        assert (await db.problem_links_of(a))["causes"] == []
        assert await db.problem_chain(a) == []
    _run(body)


@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient
    from app import db, main

    async def prepare():
        db.DATABASE_URL = TEST_DB
        await db.close_pool()
        await db.init_pool()
        await db.init_db()
        await db.wipe(force=True)
        uid = await db.add_user("author1", auth.hash_password("secret1"),
                                "Автор", "#fff", email="a@example.com",
                                invite_required=False)
        oid = await db.add_user("author2", auth.hash_password("secret1"),
                                "Другой", "#fff", email="b@example.com",
                                invite_required=False)
        pool = db._pool_or_raise()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE authors SET email_verified = TRUE, balance_usd = 5 "
                "WHERE id IN ($1, $2)", uid, oid)
        effect = await db.add_node("Заборона мови", author_id=oid, kind="problem",
                                   title="Заборона використання російської мови")
        await db.set_topic_facets(effect, "politics", "law", ["Україна"], ["мова"])
        existing = await db.add_node(
            "Обрані представники не виконують волю виборців",
            author_id=oid, kind="problem",
            title="Представники не виконують волю виборців")
        qroot = await db.add_node("Що таке справедливість?", author_id=oid,
                                  kind="question", title="Справедливість")
        reply = await db.add_node(
            "Проблема не в законах, а в тому, що представники не представляють",
            author_id=uid, kind="argument", topic_root_id=effect)
        await db.add_edge(reply, effect, "support")
        foreign = await db.add_node("чужой ответ", author_id=oid,
                                    kind="argument", topic_root_id=effect)
        await db.add_edge(foreign, effect, "support")
        elsewhere = await db.add_node("ответ в вопросе", author_id=uid,
                                      kind="argument", topic_root_id=qroot)
        await db.add_edge(elsewhere, qroot, "support")
        await db.close_pool()
        return {"author": uid, "other": oid, "effect": effect,
                "existing": existing, "qroot": qroot, "reply": reply,
                "foreign": foreign, "elsewhere": elsewhere}

    ids = asyncio.run(prepare())
    main._login_calls.clear()
    main._REVIEW_CACHE.clear()
    main._llm_calls.clear()
    db.DATABASE_URL = TEST_DB
    with TestClient(main.app) as c:
        r = c.post("/api/auth/login",
                   json={"username": "author1", "password": "secret1"})
        assert r.status_code == 200, r.text
        c.ids = ids
        yield c


def _stub_review(monkeypatch, payload):
    from app import pools as pools_mod
    seen = {}

    def fake(*a, **kw):
        seen.update(kw)
        return dict(payload)
    monkeypatch.setattr(pools_mod, "review_draft", fake)
    return seen


_CAUSE = {
    "actual_type": "support", "type_note": "", "quality_note": "",
    "verdict": "new", "node_id": None, "position_id": None, "note": "",
    "split": None, "placement": "cause",
    "cause_title": "Представники не виконують волю виборців",
    "place_id": None, "place_note": "називає причину рівнем вище", "think": "",
}


def test_review_offers_cause_only_inside_a_problem(client, monkeypatch):
    """«Ты называешь причину» — только ответу в ПРОБЛЕМЕ; в вопросе или тезисе
    «уровня выше» нет. И перед созданием причины — похожие уже существующие."""
    from app import main as main_mod
    seen = _stub_review(monkeypatch, _CAUSE)
    out = client.post("/api/draft/review",
                      json={"text": "Проблема не в законах…",
                            "connect_to": client.ids["effect"]}).json()
    assert seen.get("in_problem") is True
    assert out["placement"] == "cause"
    assert out["cause_title"] == "Представники не виконують волю виборців"
    assert [m["id"] for m in out["cause_matches"]] == [client.ids["existing"]]
    assert out["place_note"]

    main_mod._REVIEW_CACHE.clear()
    out = client.post("/api/draft/review",
                      json={"text": "Проблема не в законах…",
                            "connect_to": client.ids["qroot"]}).json()
    assert seen.get("in_problem") is False
    assert out["placement"] == "here"
    assert out["cause_title"] == "" and out["cause_matches"] == []


def test_new_cause_is_one_node_and_inherits_facets(client):
    """Одна мысль — один узел: новая причина заводится корнем Б, ответа под А
    не остаётся, обоснование связи — сама постановка Б."""
    ids = client.ids
    before = client.get(f"/api/nodes/{ids['effect']}/children").json()["total"]
    r = client.post(f"/api/problems/{ids['effect']}/causes",
                    json={"title": "Представники не представляють",
                          "text": "Обрані системно ведуть політику всупереч виборцям."})
    assert r.status_code == 200, r.text
    link = r.json()
    cause = link["cause"]
    assert cause["kind"] == "problem" and cause["topic_root_id"] == cause["id"]
    assert cause["title"] == "Представники не представляють"
    assert cause["text"].startswith("Обрані системно")
    # под следствием ничего не прибавилось
    assert client.get(f"/api/nodes/{ids['effect']}/children").json()["total"] == before

    p = client.get(f"/api/problems/{ids['effect']}").json()
    assert [c["cause_id"] for c in p["links"]["causes"]] == [cause["id"]]
    assert p["links"]["causes"][0]["node_id"] == cause["id"]
    assert p["links"]["causes"][0]["node_text"].startswith("Обрані системно")
    assert [x["id"] for x in p["chain"]] == [cause["id"]]
    up = client.get(f"/api/problems/{cause['id']}").json()
    assert [e["effect_id"] for e in up["links"]["effects"]] == [ids["effect"]]
    assert up["chain"] == []                       # выше неё пока никого

    # рубрика унаследована — причина находится теми же фильтрами карты
    topics = client.get("/api/map/topics").json()
    mine = [t for t in topics if t["id"] == cause["id"]]
    assert mine and mine[0]["domain"] == "politics"


def test_cause_converges_to_existing_problem(client):
    ids = client.ids
    r = client.post(f"/api/problems/{ids['effect']}/causes",
                    json={"node_id": ids["reply"], "cause_id": ids["existing"]})
    assert r.status_code == 200, r.text
    assert r.json()["cause_id"] == ids["existing"]
    assert r.json()["existed"] is False
    # второй раз та же пара — та же связь, не дубль
    r = client.post(f"/api/problems/{ids['effect']}/causes",
                    json={"node_id": ids["reply"], "cause_id": ids["existing"]})
    assert r.status_code == 200 and r.json()["existed"] is True


def test_cause_validation(client):
    ids = client.ids
    ex = ids["existing"]
    # схождение с чужим ответом как обоснованием — нельзя
    r = client.post(f"/api/problems/{ids['effect']}/causes",
                    json={"cause_id": ex, "node_id": ids["foreign"]})
    assert r.status_code == 403
    # свой ответ, но в другом обсуждении
    r = client.post(f"/api/problems/{ids['effect']}/causes",
                    json={"cause_id": ex, "node_id": ids["elsewhere"]})
    assert r.status_code == 400
    # следствием может быть только проблема
    r = client.post(f"/api/problems/{ids['qroot']}/causes",
                    json={"cause_id": ex})
    assert r.status_code == 400
    # сама себе причиной — нет
    r = client.post(f"/api/problems/{ids['effect']}/causes",
                    json={"cause_id": ids["effect"]})
    assert r.status_code == 400
    # новой причине нужны и заголовок, и постановка
    r = client.post(f"/api/problems/{ids['effect']}/causes", json={"title": "x"})
    assert r.status_code == 400
    r = client.post(f"/api/problems/{ids['effect']}/causes", json={"text": "x"})
    assert r.status_code == 400
    # схождение без своего ответа — просто заявленная связь, обоснование = Б
    r = client.post(f"/api/problems/{ids['effect']}/causes", json={"cause_id": ex})
    assert r.status_code == 200, r.text
    assert r.json()["node_id"] == ex
