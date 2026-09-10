"""
Фоновые вызовы модели должны попадать в счётчик трат.

Траты собирал только middleware: он ставит корзину в contextvar и высыпает её,
когда обработчик вернул ответ. Оценка PoI стартует внутри запроса, но
заканчивается ПОСЛЕ ответа — её вызовы падали в корзину, которую уже никто не
читал. Так самый частый вызов в системе (один на каждый опубликованный текст)
не попадал ни в статистику, ни в грант аккаунта: 45 узлов с оценкой и ни одной
записи трат по /api/argument.

Проверяется именно это: публикация узла оставляет запись о ФОНОВОМ вызове.

Идут только при TEST_DATABASE_URL — тесты чистят базу.
"""

import asyncio
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from app import auth  # noqa: E402

TEST_DB = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DB, reason="TEST_DATABASE_URL not set")


@pytest.fixture
def client():
    """Приложение поверх тестовой БД с вошедшим подтверждённым автором."""
    from fastapi.testclient import TestClient
    from app import db, main

    async def prepare():
        db.DATABASE_URL = TEST_DB
        await db.close_pool()
        await db.init_pool()
        await db.init_db()
        await db.wipe(force=True)
        uid = await db.add_user("meter_user", auth.hash_password("secret1"),
                                "Автор", "#fff", email="m@example.com",
                                invite_required=False)
        pool = db._pool_or_raise()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE authors SET email_verified = TRUE, balance_usd = 5 "
                "WHERE id = $1", uid)
        root = await db.add_node("Проблема для теста", author_id=uid,
                                 kind="problem", title="Проблема")
        await db.close_pool()
        return uid, root

    uid, root = asyncio.run(prepare())
    main._REVIEW_CACHE.clear()
    main._llm_calls.clear()
    db.DATABASE_URL = TEST_DB
    with TestClient(main.app) as c:
        r = c.post("/api/auth/login",
                   json={"username": "meter_user", "password": "secret1"})
        assert r.status_code == 200, r.text
        c.ids = {"author": uid, "root": root}
        yield c


def _usage_rows(client, timeout=10.0):
    """Дождаться, пока фоновая задача допишет траты.

    Ответ на публикацию приходит РАНЬШЕ, чем заканчивается оценка, — в этом вся
    суть починки. Поэтому опрос с таймаутом, а не разовая проверка.
    """
    head = {"X-Admin-Token": os.environ.get("ADMIN_TOKEN") or ""}
    deadline = time.monotonic() + timeout
    rows = []
    while time.monotonic() < deadline:
        r = client.get("/api/dev/usage", headers=head)
        if r.status_code == 200 and r.json()["recent"]:
            rows = r.json()["recent"]
            break
        time.sleep(0.2)
    return rows


def test_background_scoring_is_billed(client, monkeypatch):
    from app import poi

    def fake_score(text, *a, **kw):
        # то же, что делает настоящий вызов: кладёт расход в корзину контекста
        sink = poi.current_usage.get()
        assert sink is not None, "у фоновой задачи нет своей корзины трат"
        sink.append({"model": "claude-sonnet-4-6", "input_tokens": 1500,
                     "cache_read_tokens": 0, "cache_write_tokens": 0,
                     "output_tokens": 300})
        return 55.0, {"clarity": 5}

    monkeypatch.setattr(poi, "score_argument", fake_score)
    # раскладка по позициям в этом тесте не нужна и звала бы модель отдельно
    from app import main as main_mod
    monkeypatch.setattr(main_mod, "_assign_position_later",
                        lambda *a, **kw: asyncio.sleep(0))

    r = client.post("/api/argument", json={
        "text": "Довод, который платформа обязана оценить.",
        "connect_to": client.ids["root"], "edge_type": "support"})
    assert r.status_code == 200, r.text
    assert r.json()["poi_score"] is None      # оценка ещё не пришла — она в фоне

    rows = _usage_rows(client)
    assert rows, "фоновая оценка не попала в счётчик трат"
    assert any(x["endpoint"] == "фон:оценка" for x in rows), rows
    row = next(x for x in rows if x["endpoint"] == "фон:оценка")
    assert row["input_tokens"] == 1500
    assert row["output_tokens"] == 300
    # 1500 вх по $3/Mtok + 300 вых по $15/Mtok = 0.0045 + 0.0045
    assert abs(float(row["cost_usd"]) - 0.009) < 1e-6, row
    assert row["author_id"] == client.ids["author"]
