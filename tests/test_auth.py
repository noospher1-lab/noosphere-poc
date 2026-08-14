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


@pytest.mark.skipif(not TEST_DB, reason="TEST_DATABASE_URL not set")
def test_invite_grants_more_than_open_door():
    """Пришедший по коду получает больший грант: его позвали адресно, и он
    почти наверняка станет писать. Открытая регистрация остаётся на базовом.
    """
    async def go():
        from app import db
        db.DATABASE_URL = TEST_DB
        await db.close_pool()
        await db.init_pool()
        await db.init_db()
        await db.wipe(force=True)

        await db.add_invite("CODE-FOR-ONE")
        invited = await db.add_user("invited", auth.hash_password("secret12"),
                                    "По коду", "#fff", invite="CODE-FOR-ONE",
                                    balance_usd=3, invite_balance_usd=5)
        walkin = await db.add_user("walkin", auth.hash_password("secret12"),
                                   "С улицы", "#fff", invite_required=False,
                                   balance_usd=3, invite_balance_usd=5)
        pool = db._pool_or_raise()
        async with pool.acquire() as conn:
            got = {r["username"]: float(r["balance_usd"]) for r in await conn.fetch(
                "SELECT username, balance_usd FROM authors WHERE id = ANY($1::int[])",
                [invited, walkin])}
        assert got["invited"] == 5.0, got
        assert got["walkin"] == 3.0, got
        await db.close_pool()
    asyncio.run(go())


@pytest.mark.skipif(not TEST_DB, reason="TEST_DATABASE_URL not set")
def test_email_change_replaces_address_and_kills_stale_links():
    """Смена адреса состоится только по ссылке из письма на новый адрес.

    И гасит прежние висящие ссылки: иначе неиспользованная ссылка с регистрации
    откатила бы смену назад, а кабинет показывал бы старый адрес как «ждём
    подтверждения».
    """
    async def go():
        from app import db
        db.DATABASE_URL = TEST_DB
        await db.close_pool()
        await db.init_pool()
        await db.init_db()
        await db.wipe(force=True)

        uid = await db.add_user("mover", auth.hash_password("secret12"), "Переезд",
                                "#fff", email="old@example.com",
                                invite_required=False)
        await db.create_email_verification(uid, "old@example.com", "hash-old")
        await db.create_email_verification(uid, "new@example.com", "hash-new")
        assert await db.pending_email_change(uid) == "new@example.com"

        assert await db.redeem_email_verification("hash-new") == uid
        pool = db._pool_or_raise()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT email, email_verified FROM authors WHERE id = $1", uid)
        assert row["email"] == "new@example.com" and row["email_verified"]
        # старая ссылка погашена и уже не откатит адрес назад
        assert await db.pending_email_change(uid) is None
        assert await db.redeem_email_verification("hash-old") == uid
        async with pool.acquire() as conn:
            assert await conn.fetchval(
                "SELECT email FROM authors WHERE id = $1", uid) == "new@example.com"
        await db.close_pool()
    asyncio.run(go())
