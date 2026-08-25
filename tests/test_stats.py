"""
Тесты счётчиков: сколько зарегистрировано, сколько онлайн, сколько потрачено.

Как и остальные тесты слоя БД, идут только при TEST_DATABASE_URL (они вытирают
базу):
    TEST_DATABASE_URL=postgresql://noosphere:noosphere@localhost/noosphere_test \
        python -m pytest tests/test_stats.py -q
"""

import asyncio
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

TEST_DB = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DB, reason="TEST_DATABASE_URL not set")


def _run(coro_fn):
    """Каждый тест живёт в своём event loop, поэтому пул обязан закрыться внутри
    того же цикла, в котором открылся: пережив asyncio.run, он потянет за собой
    закрытый loop и уронит следующий тест — что и случилось 2026-08-25, когда
    один упавший ассерт увёл за собой соседний тест с «Event loop is closed».
    Закрытие в finally, а не в конце тела теста: падение — как раз тот случай,
    когда пул закрыть некому."""
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


async def _register(db, username, name="Кто-то"):
    """Аккаунт, каким его делает регистрация (инвайты выключены)."""
    return await db.add_user(username, "hash", name, invite_required=False)


async def _seen(db, author_id, minutes_ago):
    """Живая сессия с отметкой активности «столько-то минут назад»."""
    token = f"t{author_id}-{minutes_ago}"
    pool = db._pool_or_raise()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO sessions (token, author_id, expires_at, last_seen) "
            "VALUES ($1, $2, now() + interval '30 days', "
            "        now() - ($3 || ' minutes')::interval)",
            token, author_id, str(minutes_ago))
    return token


def test_registered_counts_people_not_rows():
    """Посевные персоны (без логина) и служебный аккаунт — не участники."""
    async def go():
        db = await _fresh_db()
        await db.add_author("Посевная персона", "#fff")     # username IS NULL
        await _register(db, "alice")
        bot = await _register(db, "bot")
        pool = db._pool_or_raise()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE authors SET is_service = TRUE WHERE id = $1", bot)

        s = await db.stats_public()
        assert s["registered"] == 1
        assert s["online"] == 0
        await db.close_pool()
    _run(go)


def test_online_is_activity_inside_the_window():
    async def go():
        db = await _fresh_db()
        fresh = await _register(db, "fresh")
        stale = await _register(db, "stale")
        await _register(db, "never")
        await _seen(db, fresh, 2)
        await _seen(db, stale, 90)          # был здесь, но давно

        s = await db.stats_public()
        assert s["registered"] == 3
        assert s["online"] == 1             # только fresh
        assert s["online_window_min"] == 15

        adm = await db.stats_admin()
        assert adm["presence"]["online"] == 1
        assert adm["presence"]["active_24h"] == 2
        assert [o["username"] for o in adm["online_now"]] == ["fresh"]
        await db.close_pool()
    _run(go)


def test_two_devices_are_one_person_online():
    """Телефон и ноутбук — это один онлайн, а не два."""
    async def go():
        db = await _fresh_db()
        aid = await _register(db, "twodevices")
        await _seen(db, aid, 1)
        await _seen(db, aid, 3)

        s = await db.stats_public()
        assert s["online"] == 1
        adm = await db.stats_admin()
        assert adm["presence"]["live_sessions"] == 2      # сессий всё-таки две
        await db.close_pool()
    _run(go)


def test_session_author_stamps_last_seen():
    """Отметка ставится самим фактом запроса — без неё «онлайн» всегда ноль."""
    async def go():
        db = await _fresh_db()
        aid = await _register(db, "walker")
        token = "walker-session"
        await db.create_session(token, aid)

        assert (await db.stats_public())["online"] == 0   # ещё ни одного запроса
        assert (await db.session_author(token))["username"] == "walker"
        assert (await db.stats_public())["online"] == 1
        await db.close_pool()
    _run(go)


def test_profile_row_carries_money_and_activity():
    async def go():
        db = await _fresh_db()
        aid = await _register(db, "spender", name="Транжира")
        await db.set_balance(aid, 5)
        await db.add_node("довод", poi_score=70, author_id=aid)
        await db.record_usage(aid, [{
            "model": "claude-opus-5", "input_tokens": 1000,
            "output_tokens": 500, "cost_usd": 0.25,
        }], endpoint="/api/dialogue")

        adm = await db.stats_admin()
        row = next(p for p in adm["profiles"] if p["username"] == "spender")
        assert row["name"] == "Транжира"
        assert row["granted_usd"] == 5
        assert row["spent_usd"] == 0.25
        assert row["left_usd"] == 4.75
        assert row["calls"] == 1
        assert row["nodes"] == 1
        assert row["last_call"] is not None

        assert adm["spend_total"]["spent_usd"] == 0.25
        assert adm["spend_total"]["granted_usd"] == 5
        await db.close_pool()
    _run(go)


def test_profiles_lists_everyone_including_the_idle():
    """Список — это ВСЕ профили, а не только те, кто что-то делал."""
    async def go():
        db = await _fresh_db()
        for i in range(5):
            await _register(db, f"user{i}")

        adm = await db.stats_admin()
        assert len(adm["profiles"]) == 5
        idle = adm["profiles"][0]
        assert idle["spent_usd"] == 0 and idle["calls"] == 0
        assert idle["last_seen"] is None
        assert adm["users"]["registered"] == 5
        await db.close_pool()
    _run(go)
