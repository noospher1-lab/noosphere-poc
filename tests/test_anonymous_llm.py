"""
Модель зовёт только вошедший, и только со своего баланса.

Правило (Alex, 2026-09-10): трат без автора не бывает. До этого дня из шести
ручек позиций пять стояли под verified_author + llm_budget, а шестая — GET со
списком — при ПЕРВОМ чтении темы запускала полную кластеризацию. Граф открыт
без входа (корпус для чтения), проверок бюджета там нет: любой человек,
открывший проблему, тратил деньги общего ключа. И записи не оставалось —
middleware при анонимном запросе выбрасывает траты, записать их не на кого.

Здесь проверяется, что дыра закрыта с обеих сторон: чтение модель не зовёт,
а заказ пересборки требует входа и бюджета.

Идёт только при TEST_DATABASE_URL — тест чистит базу.
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


@pytest.fixture
def topic():
    """Проблема с двумя доводами и БЕЗ посчитанных позиций."""
    from app import db

    async def prepare():
        db.DATABASE_URL = TEST_DB
        await db.close_pool()
        await db.init_pool()
        await db.init_db()
        await db.wipe(force=True)
        uid = await db.add_user("owner", auth.hash_password("x"), "Автор",
                                invite_required=False)
        root = await db.add_node("Проблема без позиций", author_id=uid,
                                 kind="problem", title="П")
        for t in ("Первый довод по существу.", "Второй довод, другой стороны."):
            nid = await db.add_node(t, author_id=uid, kind="argument",
                                    topic_root_id=root)
            await db.add_edge(nid, root, "support")
        await db.close_pool()
        return root

    root = asyncio.run(prepare())
    db.DATABASE_URL = TEST_DB
    return root


def _fake_cluster(calls):
    def fake(args, *a, **kw):
        # cluster_arguments возвращает СПИСОК позиций, каждая со своими members
        calls["n"] += 1
        return [{"headline": "Позиция", "composed": "Сводный текст",
                 "stance": "mixed", "members": [x["id"] for x in args]}]
    return fake


def test_guest_read_never_calls_the_model(topic, monkeypatch):
    """Гость читает — модель молчит. Это и есть починенная дыра."""
    from fastapi.testclient import TestClient
    from app import main, pools

    calls = {"n": 0}
    monkeypatch.setattr(pools, "cluster_arguments", _fake_cluster(calls))

    with TestClient(main.app) as c:
        c.cookies.clear()                       # никакого входа: чистый гость
        assert c.get("/api/auth/me").json() is None
        r = c.get(f"/api/positions/{topic}")
        assert r.status_code == 200, r.text
        assert r.json()["positions"] == []      # отдал пусто, а не собрал

    assert calls["n"] == 0, "чтение всё ещё зовёт модель"


def test_guest_cannot_order_clustering(topic, monkeypatch):
    """И заказать сборку гость тоже не может: платить нечем."""
    from fastapi.testclient import TestClient
    from app import main, pools

    calls = {"n": 0}
    monkeypatch.setattr(pools, "cluster_arguments", _fake_cluster(calls))

    with TestClient(main.app) as c:
        c.cookies.clear()
        r = c.post(f"/api/positions/{topic}/recompute")
        assert r.status_code in (401, 403), r.status_code
    assert calls["n"] == 0


def test_author_orders_clustering_and_pays(topic, monkeypatch):
    """Вошедший заказывает — модель работает, траты записаны на него."""
    from fastapi.testclient import TestClient
    from app import db, main, poi, pools

    calls = {"n": 0}
    base = _fake_cluster(calls)

    def fake_cluster(args, *a, **kw):
        # настоящий вызов кладёт расход в корзину контекста — заглушка тоже
        sink = poi.current_usage.get()
        if sink is not None:
            sink.append({"model": "claude-sonnet-4-6", "input_tokens": 900,
                         "cache_read_tokens": 0, "cache_write_tokens": 0,
                         "output_tokens": 200})
        return base(args, *a, **kw)

    monkeypatch.setattr(pools, "cluster_arguments", fake_cluster)
    # оба счётчика живут в модуле и переживают тест: лимит ИИ-вызовов ключом
    # берёт id автора (после wipe он повторяется), лимит попыток входа — IP
    # (а он у всех тестов один). Без сброса падает не тот тест, что их набрал.
    main._llm_calls.clear()
    main._login_calls.clear()

    async def verify():
        db.DATABASE_URL = TEST_DB
        await db.close_pool()
        await db.init_pool()
        pool = db._pool_or_raise()
        async with pool.acquire() as conn:
            await conn.execute("UPDATE authors SET email_verified = TRUE, "
                               "balance_usd = 5 WHERE username = 'owner'")
        await db.close_pool()

    asyncio.run(verify())
    db.DATABASE_URL = TEST_DB

    with TestClient(main.app) as c:
        assert c.post("/api/auth/login",
                      json={"username": "owner", "password": "x"}
                      ).status_code == 200
        r = c.post(f"/api/positions/{topic}/recompute")
        assert r.status_code == 200, r.text
        assert r.json()["positions"], "позиции не собрались"
        assert calls["n"] == 1

        token = os.environ.get("ADMIN_TOKEN")
        if not token:
            pytest.skip("ADMIN_TOKEN не задан — траты не проверить")
        rows = c.get("/api/dev/usage",
                     headers={"X-Admin-Token": token}).json()["recent"]

    assert rows, "заказ сборки не попал в счётчик трат"
    assert any(r["endpoint"].endswith("/recompute") for r in rows), rows
    assert all(r["author_id"] is not None for r in rows)
