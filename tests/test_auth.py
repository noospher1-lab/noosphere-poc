"""
Accounts: pure password-hashing tests (always run) and a session flow test
against Postgres (gated on TEST_DATABASE_URL like the rest).
"""

import asyncio
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from app import auth  # noqa: E402

TEST_DB = os.environ.get("TEST_DATABASE_URL")


def test_password_hash_roundtrip():
    stored = auth.hash_password("correct horse")
    assert auth.verify_password("correct horse", stored)
    assert not auth.verify_password("wrong horse", stored)
    assert not auth.verify_password("correct horse", "garbage")
    # same password, different salt -> different hash
    assert stored != auth.hash_password("correct horse")


@pytest.mark.skipif(not TEST_DB, reason="TEST_DATABASE_URL not set")
def test_user_and_session_flow():
    async def go():
        from app import db
        db.DATABASE_URL = TEST_DB
        await db.close_pool()
        await db.init_pool()
        await db.init_db()
        await db.wipe(force=True)

        # invite_required=False: регистрация открыта с 2026-08, и без этого флага
        # add_user отсекает по отсутствующему коду раньше, чем доходит до проверки
        # имени — тест ловил бы "invite" вместо интересующего его результата
        uid = await db.add_user("alex_test", auth.hash_password("secret1"), "Алекс",
                                "#fff", invite_required=False)
        assert uid is not None
        # duplicate username is rejected, not overwritten — add_user reports the
        # reason as a tag rather than a bare None (see its docstring)
        assert await db.add_user("alex_test", auth.hash_password("x" * 6), "Другой",
                                 invite_required=False) == "taken"

        a = await db.get_author_by_username("alex_test")
        assert auth.verify_password("secret1", a["password_hash"])

        token = auth.new_token()
        await db.create_session(token, uid)
        me = await db.session_author(token)
        assert me["id"] == uid
        assert "password_hash" not in me          # never leaves the DB layer

        await db.delete_session(token)
        assert await db.session_author(token) is None

        # public author reads must not expose the hash either
        assert all("password_hash" not in x for x in await db.list_authors())
        await db.close_pool()
    asyncio.run(go())


@pytest.mark.skipif(not TEST_DB, reason="TEST_DATABASE_URL not set")
def test_terms_consent_is_recorded():
    """Согласие фиксируется, а не подразумевается.

    Регистрация открыта незнакомым людям, и через год должно быть видно не
    только «согласился», но и с какой версией текста (vault:
    data-deletion-model).
    """
    async def go():
        from app import db
        db.DATABASE_URL = TEST_DB
        await db.close_pool()
        await db.init_pool()
        await db.init_db()
        await db.wipe(force=True)

        uid = await db.add_user("terms_user", auth.hash_password("secret12"),
                                "Согласный", "#fff", invite_required=False,
                                terms_version="2026-07-21")
        pool = db._pool_or_raise()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT terms_version, terms_accepted_at FROM authors WHERE id = $1",
                uid)
        assert row["terms_version"] == "2026-07-21"
        assert row["terms_accepted_at"] is not None

        # без версии — время согласия не проставляется само собой
        other = await db.add_user("no_terms", auth.hash_password("secret12"),
                                  "Без версии", "#fff", invite_required=False)
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT terms_accepted_at FROM authors WHERE id = $1", other)
        assert row["terms_accepted_at"] is None
        await db.close_pool()
    asyncio.run(go())
