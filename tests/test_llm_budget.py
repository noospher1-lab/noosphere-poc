"""
Общий кран бюджета: счётчик трат по ключу инстанса.

Гейтятся на TEST_DATABASE_URL так же, как остальные DB-тесты (они чистят базу):
    createdb noosphere_test
    TEST_DATABASE_URL=postgresql://noosphere:noosphere@localhost/noosphere_test \
        python -m pytest tests/ -q
"""

import asyncio
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

TEST_DB = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DB, reason="TEST_DATABASE_URL not set")


def _run(coro):
    return asyncio.run(coro)


async def _fresh_db():
    from app import db
    db.DATABASE_URL = TEST_DB
    await db.close_pool()
    await db.init_pool()
    await db.init_db()
    await db.wipe(force=True)
    return db


def _usage(cost):
    return {"model": "claude-test", "input_tokens": 10, "output_tokens": 5,
            "cost_usd": cost}


def test_shared_key_spend_ignores_own_key_accounts():
    async def go():
        db = await _fresh_db()
        assert await db.shared_key_spend() == 0.0

        shared = await db.add_author("На общем ключе", "#d8af6e")
        own = await db.add_author("Со своим ключом", "#5aa9e6")
        await db.set_api_key(own, "sk-ant-personal")

        await db.record_usage(shared, [_usage(0.25)], "/api/x")
        # Своим ключом платит сам аккаунт — в общий кран это не попадает,
        # иначе один щедрый тестер закрыл бы ИИ всему инстансу.
        await db.record_usage(own, [_usage(9.0)], "/api/x")

        assert await db.shared_key_spend() == pytest.approx(0.25)
        await db.close_pool()
    _run(go())


def test_own_key_later_does_not_erase_past_shared_spend():
    """Ключ, выданный задним числом, не должен вычитать уже потраченное.

    Ради этого shared_key пишется в момент траты, а не выводится джойном по
    authors: джойн ослаблял бы кран ровно там, где ошибаться нельзя.
    """
    async def go():
        db = await _fresh_db()
        aid = await db.add_author("Сначала на общем", "#b98cff")
        await db.record_usage(aid, [_usage(0.40)], "/api/x")

        await db.set_api_key(aid, "sk-ant-later")
        await db.record_usage(aid, [_usage(5.0)], "/api/x")

        # Прошлые $0.40 остаются на общем ключе, новые $5 — уже нет.
        assert await db.shared_key_spend() == pytest.approx(0.40)
        await db.close_pool()
    _run(go())


def test_spend_accumulates_across_calls():
    async def go():
        db = await _fresh_db()
        a = await db.add_author("Тестер", "#57d98a")
        await db.record_usage(a, [_usage(0.1), _usage(0.2)], "/api/x")
        await db.record_usage(a, [_usage(0.05)], "/api/y")
        assert await db.shared_key_spend() == pytest.approx(0.35)
        await db.close_pool()
    _run(go())
