"""
Антиспам (app/spam.py; Alex 29.09: переключатель, по умолчанию включён, спам
невидим везде). Модель подменена — платный API не зовётся.

Чистые функции идут всегда; остальное — при TEST_DATABASE_URL (стирает базу).
"""

import asyncio
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from app import antiabuse, auth, info_db, spam  # noqa: E402

TEST_DB = os.environ.get("TEST_DATABASE_URL")


@pytest.fixture(autouse=True)
def no_paid_model(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("модель в тестах выключена")
    monkeypatch.setattr(spam.poi, "complete_messages", boom)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def test_without_model_nothing_is_marked():
    assert spam.judge_sync("купите слона https://x.biz", "обсуждение войны") is None


def test_model_verdict_is_parsed(monkeypatch):
    monkeypatch.setattr(spam.poi, "complete_messages",
                        lambda *a, **k: '{"spam": true, "reason": "реклама   канала"}')
    assert spam.judge_sync("подписывайтесь") == "реклама канала"
    monkeypatch.setattr(spam.poi, "complete_messages",
                        lambda *a, **k: '{"spam": false, "reason": ""}')
    assert spam.judge_sync("это плохая идея, и вот почему") is None


def test_prompt_protects_opinions():
    # ИИ не прячет неудобное мнение: это правило обязано быть в инструкции
    assert "NOT SPAM, never" in spam.SYSTEM and "censorship" in spam.SYSTEM


def test_hide_mode_by_method_and_cookie():
    assert spam.hide_for("GET", None) is True
    assert spam.hide_for("GET", "show") is False


# ------------------------------------------------------------ с базой

async def _prepare():
    from app import db
    db.DATABASE_URL = TEST_DB
    await db.close_pool()
    await db.init_pool()
    await db.init_db()
    await db.wipe(force=True)
    antiabuse._secret = None
    ids = {}
    for name in ("sp_vera", "sp_oleg", "root"):
        uid = await db.add_user(name, auth.hash_password("secret12"), name, "#fff",
                                email=f"{name}@example.com", invite_required=False)
        async with db._pool_or_raise().acquire() as conn:
            await conn.execute("UPDATE authors SET email_verified = TRUE WHERE id = $1", uid)
        ids[name] = uid
    root = await db.add_node("ТЕСТ обсуждение о защите", kind="problem",
                             title="ТЕСТ обсуждение", author_id=ids["sp_vera"])
    ok = await db.add_node("Нормальный довод", author_id=ids["sp_vera"], topic_root_id=root)
    await db.add_edge(ok, root, "support")
    bad = await db.add_node("Купите курс по крипте", author_id=ids["sp_oleg"], topic_root_id=root)
    await db.add_edge(bad, root, "support")
    junk = await db.add_node("ТЕСТ мусорная тема", kind="problem", title="ТЕСТ мусорная тема",
                             author_id=ids["sp_oleg"])
    fact = await info_db.add_fact(root, {"title": "ТЕСТ реклама под видом сведения",
                                         "kind": "experience", "country": "Польша",
                                         "section": "protection"}, ids["sp_oleg"])
    await spam.mark("node", bad, "реклама", "ai")
    await spam.mark("node", junk, "не о чем", "ai")
    await spam.mark("fact", fact, "реклама", "ai")
    await db.close_pool()
    return dict(ids, root=root, ok=ok, bad=bad, junk=junk, fact=fact)


@pytest.fixture
def api():
    if not TEST_DB:
        pytest.skip("TEST_DATABASE_URL not set")
    from fastapi.testclient import TestClient
    from app import db, main
    ids = asyncio.run(_prepare())
    main._login_calls.clear()
    db.DATABASE_URL = TEST_DB
    c = TestClient(main.app)
    c.__enter__()
    yield c, ids
    c.__exit__(None, None, None)


def _login(c, name):
    c.cookies.clear()
    r = c.post("/api/auth/login", json={"username": name, "password": "secret12"})
    assert r.status_code == 200, r.text


def _children(c, root):
    return [x["id"] for x in c.get(f"/api/nodes/{root}/children").json()["children"]]


def test_spam_hidden_by_default_everywhere(api):
    c, ids = api
    page = c.get(f"/api/nodes/{ids['root']}/children").json()
    assert [x["id"] for x in page["children"]] == [ids["ok"]] and page["total"] == 1
    topics = [t["id"] for t in c.get("/api/topics").json()]
    assert ids["root"] in topics and ids["junk"] not in topics
    info = c.get(f"/api/info/{ids['root']}").json()
    assert info["total"] == 0
    graph = c.get("/api/graph").json()
    assert ids["bad"] not in [n["id"] for n in graph["nodes"]]


def test_switch_off_shows_spam_with_reason(api):
    c, ids = api
    c.cookies.set(spam.COOKIE, "show")
    kids = c.get(f"/api/nodes/{ids['root']}/children").json()["children"]
    bad = next(x for x in kids if x["id"] == ids["bad"])
    assert bad["spam_reason"] == "реклама"
    assert ids["junk"] in [t["id"] for t in c.get("/api/topics").json()]
    assert c.get(f"/api/info/{ids['root']}").json()["total"] == 1


def test_author_always_sees_own(api):
    c, ids = api
    _login(c, "sp_oleg")
    assert ids["bad"] in _children(c, ids["root"])
    _login(c, "sp_vera")
    assert ids["bad"] not in _children(c, ids["root"])


def test_admin_clears_and_ai_does_not_remark(api):
    c, ids = api
    _login(c, "sp_vera")
    assert c.post(f"/api/admin/spam/node/{ids['bad']}", json={"spam": False}).status_code == 403
    _login(c, "root")
    assert len(c.get("/api/admin/spam").json()) == 3
    assert c.post(f"/api/admin/spam/node/{ids['bad']}", json={"spam": False}).json()["ok"]
    c.cookies.clear()
    assert ids["bad"] in _children(c, ids["root"])



def _with_db(fn):
    """Прямая работа с базой — в своём цикле и своём пуле, без TestClient."""
    async def run():
        from app import db
        ids = await _prepare()
        await db.init_pool()
        try:
            return await fn(ids)
        finally:
            await db.close_pool()
    return asyncio.run(run())


@pytest.mark.skipif(not TEST_DB, reason="TEST_DATABASE_URL not set")
def test_ai_does_not_remark_what_admin_cleared():
    async def body(ids):
        await spam.clear("node", ids["bad"], ids["root"])
        return await spam.mark("node", ids["bad"], "реклама", "ai")
    assert _with_db(body) is False


@pytest.mark.skipif(not TEST_DB, reason="TEST_DATABASE_URL not set")
def test_same_long_text_twice_is_a_repeat():
    text = "Абсолютно одинаковый длинный текст, вставленный два раза подряд в разные ветки"

    async def body(ids):
        from app import db
        a = await db.add_node(text, author_id=ids["sp_oleg"], topic_root_id=ids["root"])
        b = await db.add_node(text, author_id=ids["sp_oleg"], topic_root_id=ids["root"])
        await spam.check("node", a, ids["sp_oleg"], text)
        await spam.check("node", b, ids["sp_oleg"], text)
        async with db._pool_or_raise().acquire() as conn:
            return await conn.fetch("SELECT id, spam_by FROM nodes WHERE id = ANY($1::int[]) "
                                    "ORDER BY id", [a, b])
    rows = _with_db(body)
    assert rows[0]["spam_by"] is None and rows[1]["spam_by"] == "repeat"
