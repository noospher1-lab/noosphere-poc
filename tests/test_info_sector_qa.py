"""
QA информационного сектора (studio-qa, 28.09.2026; отчёт
vault/studio/reports/2026-09-28-info-qa.md).

Дополняет tests/test_info_sector.py: границы ввода, снятие отметок и сведений,
лимит отметок, API глазами гостя и вошедшего. Тесты, помеченные xfail(strict),
— найденные баги: пока бага нет в коде, xfail превратится в XPASS и прогон
упадёт — значит, починили, и метку пора снять.

ИИ здесь не зовётся никогда: модель подменена исключением (как без ключа на
стенде), ANTHROPIC_API_KEY убран из окружения, сверка цитаты — без сети.

Чистые функции идут всегда; остальное — при TEST_DATABASE_URL (стирает базу).
"""

import asyncio
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from app import antiabuse, auth, info_ai, info_db  # noqa: E402

TEST_DB = os.environ.get("TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not TEST_DB, reason="TEST_DATABASE_URL not set")


@pytest.fixture(autouse=True)
def no_paid_model(monkeypatch):
    """Ни один тест этого файла не должен дойти до платного API."""
    def boom(*a, **k):
        raise RuntimeError("модель в QA-тестах выключена")
    monkeypatch.setattr(info_ai.poi, "complete_messages", boom)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


# ------------------------------------------------------------ без базы

# QA-1 (28.09) починен: сервер не принимает ссылку, которую браузер не разберёт
@pytest.mark.parametrize("url", ["http://", "https://", "http://exa mple.com/x", "https://[bad"])
def test_unparseable_source_url_is_rejected(url):
    with pytest.raises(info_db.InfoError):
        info_db.validate_fact({"title": "ТЕСТ", "source_url": url})


def test_long_fields_are_cut_to_known_limits():
    """Сейчас длинное молча обрезается (а не отклоняется) — фиксируем пределы,
    чтобы изменение было замечено. Что показывать человеку — вопрос в отчёте."""
    f = info_db.validate_fact({
        "title": "у" * 5000, "body": "б" * 9000, "office": "о" * 500,
        "applies_to": "к" * 500, "when_text": "к" * 500, "city": "г" * 500,
        "country": "Германия", "source_url": "https://e.eu/" + "x" * 2000,
        "source_title": "t" * 900, "source_quote": "q" * 5000})
    assert len(f["title"]) == 200 and len(f["body"]) == 4000
    assert len(f["office"]) == 200 and len(f["applies_to"]) == 200
    assert len(f["when_text"]) == 100 and len(f["city"]) == 80
    assert len(f["source_url"]) == 1000 and len(f["source_quote"]) == 2000


def test_whitespace_only_city_is_no_city():
    f = info_db.validate_fact({"title": "ТЕСТ", "city": "   ", "kind": "report",
                               "source_url": "https://e.eu"})
    assert f["city"] is None and f["country"] is None


def test_markup_is_kept_as_text_for_the_client_to_escape():
    """Сервер не чистит HTML — экранирует info.js (esc). Проверено e2e 28.09."""
    x = '<img src=x onerror="alert(1)">'
    f = info_db.validate_fact({"title": "ТЕСТ " + x, "country": "Германия", "city": x})
    assert x in f["title"] and f["city"] == x


def test_url_scheme_must_be_http():
    for url in ("ftp://example.com", "javascript:alert(1)//http://", "www.example.com",
                "//example.com"):
        with pytest.raises(info_db.InfoError):
            info_db.validate_fact({"title": "ТЕСТ", "source_url": url})


def test_empty_sector_answer_does_not_touch_model():
    r = info_ai.ask_sync("что-нибудь", [])
    assert r["ok"] is True and r["cited"] == [] and "сведений пока нет" in r["answer"]


# ------------------------------------------------------------ с базой

def _run(fn):
    async def wrapper():
        from app import db
        db.DATABASE_URL = TEST_DB
        await db.close_pool()
        await db.init_pool()
        await db.init_db()
        await db.wipe(force=True)
        antiabuse._secret = None
        try:
            await fn(db)
        finally:
            await db.close_pool()
    asyncio.run(wrapper())


@needs_db
def test_report_switch_unmark_and_note_limit():
    async def body(db):
        owner = await db.add_user("owner", "h", "Автор", invite_required=False)
        u = await db.add_user("u", "h", "U", invite_required=False)
        sid = await info_db.ensure_sector("t", "Тест")
        fid = await info_db.add_fact(sid, {"title": "ТЕСТ", "country": "Польша"}, owner)

        await info_db.set_report(fid, u, "same", "ТЕСТ: " + "х" * 900)
        f = (await info_db.country_view("t", "Польша", me=u))["national"][0]
        assert (f["same"], f["differs"], f["mine"]) == (1, 0, "same")
        assert len(f["notes"][0]["note"]) == 500

        # передумал: одна отметка человека, не две
        await info_db.set_report(fid, u, "differs", None)
        f = (await info_db.country_view("t", "Польша", me=u))["national"][0]
        assert (f["same"], f["differs"], f["mine"]) == (0, 1, "differs")
        assert f["notes"] == []                    # заметка ушла вместе с прежней отметкой

        # «mine» — только для того, кто смотрит
        f = (await info_db.country_view("t", "Польша", me=owner))["national"][0]
        assert f["mine"] is None
        f = (await info_db.country_view("t", "Польша"))["national"][0]
        assert f["mine"] is None

        await info_db.set_report(fid, u, None)
        await info_db.set_report(fid, u, None)     # снять несуществующее — не ошибка
        f = (await info_db.country_view("t", "Польша", me=u))["national"][0]
        assert (f["same"], f["differs"], f["mine"]) == (0, 0, None)

        with pytest.raises(info_db.InfoError):
            await info_db.set_report(fid, u, "maybe")
        with pytest.raises(info_db.InfoError):
            await info_db.set_report(10 ** 8, u, "same")
    _run(body)


@needs_db
def test_daily_limit_on_reports():
    async def body(db):
        owner = await db.add_user("owner", "h", "Автор", invite_required=False)
        u = await db.add_user("u", "h", "U", invite_required=False)
        sid = await info_db.ensure_sector("t", "Тест")
        ids = [await info_db.add_fact(sid, {"title": f"ТЕСТ {i}", "country": "Чехия"}, owner, limit=False)
               for i in range(info_db.REPORTS_PER_DAY + 1)]
        for fid in ids[:-1]:
            await info_db.set_report(fid, u, "same")
        with pytest.raises(info_db.InfoError):
            await info_db.set_report(ids[-1], u, "same")
        # снять свою отметку можно и после лимита
        await info_db.set_report(ids[0], u, None)
    _run(body)


@needs_db
def test_remove_rules_and_removed_fact_disappears():
    async def body(db):
        owner = await db.add_user("owner", "h", "Автор", invite_required=False)
        other = await db.add_user("other", "h", "Другой", invite_required=False)
        sid = await info_db.ensure_sector("t", "Тест")
        fid = await info_db.add_fact(sid, {"title": "ТЕСТ снимаемое уникальноеслово",
                                           "country": "Чехия"}, owner)
        keep = await info_db.add_fact(sid, {"title": "ТЕСТ остаётся", "kind": "report",
                                            "source_url": "https://e.eu"}, owner)
        with pytest.raises(info_db.InfoError):
            await info_db.remove_fact(fid, other)
        await info_db.remove_fact(fid, owner)
        with pytest.raises(info_db.InfoError):
            await info_db.remove_fact(fid, owner)  # второй раз — «сведения нет»
        with pytest.raises(info_db.InfoError):
            await info_db.set_report(fid, other, "same")
        v = await info_db.sector_view("t")
        assert v["total"] == 1 and v["countries"] == []
        # снятое не находится; без совпадений поиск пуст (с 28.09 — не «первые подряд»)
        assert await info_db.search(sid, "уникальноеслово") == []
        assert [f["id"] for f in await info_db.search(sid, "остаётся")] == [keep]
        # админ снимает чужое
        await info_db.remove_fact(keep, other, admin=True)
        assert (await info_db.sector_view("t"))["total"] == 0
    _run(body)


# ------------------------------------------------------------ API

async def _prepare_api():
    from app import db
    db.DATABASE_URL = TEST_DB
    await db.close_pool()
    await db.init_pool()
    await db.init_db()
    await db.wipe(force=True)
    antiabuse._secret = None
    ids = {}
    for name in ("qa_vera", "qa_oleg"):
        uid = await db.add_user(name, auth.hash_password("secret12"), name, "#fff",
                                email=f"{name}@example.com", invite_required=False)
        async with db._pool_or_raise().acquire() as conn:
            await conn.execute("UPDATE authors SET email_verified = TRUE, balance_usd = 5 "
                               "WHERE id = $1", uid)
        ids[name] = uid
    sid = await info_db.ensure_sector("ua-eu", "ТЕСТ сектор")
    ids["common"] = await info_db.add_fact(
        sid, {"title": "ТЕСТ Директива продлевает защиту", "kind": "report",
              "source_url": "https://e.eu", "section": "basis"}, ids["qa_oleg"])
    ids["pl"] = await info_db.add_fact(
        sid, {"title": "ТЕСТ в Польше выезд лишает статуса", "country": "Польша",
              "kind": "experience", "section": "protection"}, ids["qa_oleg"])
    await db.close_pool()
    return ids


@pytest.fixture
def api(monkeypatch):
    if not TEST_DB:
        pytest.skip("TEST_DATABASE_URL not set")
    from fastapi.testclient import TestClient
    from app import db, main
    ids = asyncio.run(_prepare_api())
    main._login_calls.clear()
    db.DATABASE_URL = TEST_DB
    # сверка цитаты без сети: «verified» — только для выдержки со словом ДОСЛОВНО
    monkeypatch.setattr(info_db, "check_quote", lambda url, q: (
        "none" if not url or not q else "verified" if "ДОСЛОВНО" in q else "mismatch"))
    c = TestClient(main.app)
    c.__enter__()
    r = c.post("/api/auth/login", json={"username": "qa_vera", "password": "secret12"})
    assert r.status_code == 200, r.text
    yield {"vera": c, "guest": _Guest(c), **ids}
    c.__exit__(None, None, None)


class _Guest:
    """Тот же клиент без куки сессии. Второй TestClient со своим lifespan
    делит с первым глобальный пул базы и ломает его («another operation is in
    progress»), поэтому гость — тот же клиент на время запроса без кук."""

    def __init__(self, c):
        self.c = c

    def __getattr__(self, method):
        def call(*a, **k):
            saved = dict(self.c.cookies)
            self.c.cookies.clear()
            try:
                return getattr(self.c, method)(*a, **k)
            finally:
                for name, value in saved.items():
                    self.c.cookies.set(name, value)
        return call


def test_api_guest_reads_but_cannot_write(api):
    g = api["guest"]
    v = g.get("/api/info/ua-eu").json()
    assert v["total"] == 2 and [f["id"] for f in v["common"]] == [api["common"]]
    c = g.get("/api/info/ua-eu/country/Польша").json()
    assert [f["id"] for f in c["national"]] == [api["pl"]]
    assert g.get("/api/info/ua-eu/country/Атлантида").status_code == 404
    assert g.get("/api/info/nope").status_code == 404
    for method, path, body in (
            ("post", f"/api/info/facts/{api['pl']}/report", {"verdict": "same"}),
            ("post", "/api/info/ua-eu/facts", {"title": "ТЕСТ"}),
            ("post", "/api/info/ua-eu/ask", {"question": "как продлить защиту"}),
            ("post", "/api/info/ua-eu/intake", {"text": "ТЕСТ: достаточно длинный текст"}),
            ("delete", f"/api/info/facts/{api['pl']}", None)):
        r = getattr(g, method)(path, **({"json": body} if body else {}))
        assert r.status_code == 401, (path, r.status_code)


def test_api_norm_without_verified_quote_goes_out_unverified(api):
    c = api["vera"]
    base = {"title": "ТЕСТ норма", "kind": "norm", "section": "basis",
            "source_url": "https://example.org/law"}
    r = c.post("/api/info/ua-eu/facts", json={**base, "source_quote": "пересказ"}).json()
    assert r["quote_status"] == "mismatch" and r["kind"] == "report"
    r = c.post("/api/info/ua-eu/facts", json={**base, "source_quote": "ДОСЛОВНО из закона"}).json()
    assert r["quote_status"] == "verified" and r["kind"] == "norm"
    r = c.post("/api/info/ua-eu/facts", json={"title": "ТЕСТ", "kind": "norm"})
    assert r.status_code == 400 and "Норма" in r.json()["detail"]
    r = c.post("/api/info/ua-eu/facts", json={"title": "ТЕСТ", "city": "Лион"})
    assert r.status_code == 400 and "выберите и страну" in r.json()["detail"]


def test_api_report_own_and_others(api):
    c = api["vera"]
    fid = c.post("/api/info/ua-eu/facts", json={"title": "ТЕСТ своё", "country": "Польша"}).json()["id"]
    r = c.post(f"/api/info/facts/{fid}/report", json={"verdict": "same"})
    assert r.status_code == 400                     # своё не подтвердить
    assert c.post(f"/api/info/facts/{api['pl']}/report",
                  json={"verdict": "same", "note": "ТЕСТ: так же"}).status_code == 200
    f = next(x for x in c.get("/api/info/ua-eu/country/Польша").json()["national"]
             if x["id"] == api["pl"])
    assert f["same"] == 1 and f["same_independent"] == 1 and f["mine"] == "same"
    # гость видит числа, но не «mine»
    f = next(x for x in api["guest"].get("/api/info/ua-eu/country/Польша").json()["national"]
             if x["id"] == api["pl"])
    assert f["same"] == 1 and f["mine"] is None
    # чужое не снять
    assert c.delete(f"/api/info/facts/{api['pl']}").status_code == 400
    assert c.delete(f"/api/info/facts/{fid}").status_code == 200
    assert c.delete(f"/api/info/facts/{fid}").status_code == 400


def test_api_ask_without_model_lists_found_facts(api):
    c = api["vera"]
    assert c.post("/api/info/ua-eu/ask", json={"question": "как"}).status_code == 400
    r = c.post("/api/info/ua-eu/ask",
               json={"question": "выезд из Польши лишает статуса?", "country": "Атлантида"}).json()
    assert r["ok"] is False and r["answer"] is None
    assert r["facts"] and r["facts"][0]["id"] == api["pl"]
    assert r["facts"][0]["country"] == "Польша"     # фронту нужна страна, чтобы открыть карточку
    r = c.post("/api/info/ua-eu/intake", json={"text": "ТЕСТ: в Лейпциге продлили без Резерв+"}).json()
    assert r["ok"] is False and "Лейпциге" in r["draft"]["body"]
