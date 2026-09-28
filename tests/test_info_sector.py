"""
Информационный сектор и склейка мультиаккаунтов
(vault: drafts/2026-09-28-info-sector, drafts/2026-09-28-multiaccounting).

Проверяется главное обещание: у сведения нет вердикта, есть то, на чём оно
держится, — сверенная цитата или число НЕЗАВИСИМЫХ подтверждений. Второй
аккаунт одного человека не добавляет независимого подтверждения, но и никого
не лишает голоса: общее число показывается рядом.

Чистые функции идут всегда; остальное — при TEST_DATABASE_URL (стирает базу).
"""

import asyncio
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from app import antiabuse, info_ai, info_db  # noqa: E402

TEST_DB = os.environ.get("TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not TEST_DB, reason="TEST_DATABASE_URL not set")


# ------------------------------------------------------------ без базы

def test_quote_check_is_verbatim_modulo_typography(monkeypatch):
    page = ("<html><script>var x='мусор'</script><p>Українські консульства "
            "за&nbsp;кордоном не&nbsp;зможуть надавати послуги військово­зобов’язаним "
            "чоловікам віком від 18 до 60 років.</p></html>")
    page = page.replace("</p>", " " + "дальше текст страницы. " * 20 + "</p>")
    monkeypatch.setattr(info_db, "fetch_text", lambda url: info_db.html_to_text(page))
    ok = "не зможуть надавати послуги військовозобов'язаним чоловікам"
    assert info_db.check_quote("https://x", ok) == "verified"
    # пропуск многоточием — части ищутся по порядку
    assert info_db.check_quote("https://x", "Українські консульства … від 18 до 60") == "verified"
    # пересказ — не выдержка
    assert info_db.check_quote("https://x", "консульства не обслуживают мужчин") == "mismatch"
    # части не в том порядке — тоже не выдержка
    assert info_db.check_quote("https://x", "від 18 до 60 … Українські консульства") == "mismatch"
    assert info_db.check_quote(None, ok) == "none"
    monkeypatch.setattr(info_db, "fetch_text", lambda url: None)
    assert info_db.check_quote("https://x", ok) == "unreachable"


def test_tag_spacing_before_punctuation_is_not_a_different_quote():
    # «interruption .» — след закрывающего тега на странице, слова те же
    assert info_db.normalize("without interruption .") == info_db.normalize("without interruption.")


def test_redirect_into_internal_network_is_refused():
    h = info_db._SafeRedirect()
    assert h.redirect_request(None, None, 302, "", {}, "http://127.0.0.1/admin") is None
    assert h.redirect_request(None, None, 302, "", {}, "http://169.254.169.254/") is None


def test_server_never_fetches_internal_addresses():
    for url in ("http://127.0.0.1:8000/", "http://localhost/", "http://10.0.0.5/",
                "http://169.254.169.254/latest/meta-data/", "file:///etc/passwd"):
        assert info_db.fetch_text(url) is None


def test_validation():
    with pytest.raises(info_db.InfoError):
        info_db.validate_fact({"title": "Закон", "kind": "norm"})       # норма без текста
    with pytest.raises(info_db.InfoError):
        info_db.validate_fact({"title": "X", "country": "Атлантида"})
    with pytest.raises(info_db.InfoError):
        info_db.validate_fact({"title": "X", "city": "Лейпциг"})         # город без страны
    f = info_db.validate_fact({"title": "  В   ABH  отказали ", "country": "Германия",
                               "city": "Лейпциг", "kind": "practice", "section": "protection"})
    assert f["title"] == "В ABH отказали" and f["city"] == "Лейпциг"


def test_disposable_mail():
    assert antiabuse.disposable("x@mailinator.com")
    assert antiabuse.disposable("x@YOPMAIL.com")
    assert not antiabuse.disposable("x@gmail.com")


def test_ipv6_privacy_addresses_are_one_network():
    assert antiabuse._net("2a02:1:2:3::1") == antiabuse._net("2a02:1:2:3:abcd::9")
    assert antiabuse._net("1.2.3.4") != antiabuse._net("1.2.3.5")


def test_ask_drops_invented_citations(monkeypatch):
    facts = [{"id": 7, "kind": "norm", "country": None, "city": None,
              "quote_status": "verified", "title": "Защита продлена до 04.03.2027",
              "body": None, "when_text": None, "source_quote": "until 4 March 2027"}]
    monkeypatch.setattr(info_ai.poi, "complete_messages", lambda *a, **k:
                        '{"answer": "До 4 марта 2027 [#7], а в Польше иначе [#99].", '
                        '"cited": [7, 99], "gaps": ""}')
    r = info_ai.ask_sync("до какого числа защита?", facts)
    assert r["cited"] == [7]
    assert "[#99]" not in r["answer"] and "[#7]" in r["answer"]


def test_ask_without_model_is_fail_open(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("нет ключа")
    monkeypatch.setattr(info_ai.poi, "complete_messages", boom)
    facts = [{"id": 3, "kind": "practice", "country": "Германия", "city": None,
              "quote_status": "none", "title": "t", "body": None, "when_text": None,
              "source_quote": None}]
    r = info_ai.ask_sync("вопрос", facts)
    assert r["ok"] is False and r["cited"] == [3]
    r = info_ai.intake_sync("в ABH сказали, что без Резерв+ не продлят")
    assert r["ok"] is False and "ABH" in r["draft"]["body"]


def test_intake_output_is_clamped_to_server_vocabulary(monkeypatch):
    monkeypatch.setattr(info_ai.poi, "complete_messages", lambda *a, **k:
                        '{"title": "Т", "section": "лайфхак", "kind": "norm", '
                        '"country": "ФРГ", "source_quote": null}')
    d = info_ai.intake_sync("какой-то текст про продление")["draft"]
    assert d["section"] == "other" and d["country"] is None
    assert d["kind"] == "unverified"            # норма без ссылки и выдержки не норма


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
def test_linked_accounts_count_once_but_nobody_is_erased():
    async def body(db):
        owner = await db.add_user("owner", "h", "Автор", invite_required=False)
        a = await db.add_user("anna", "h", "А", invite_required=False)
        b = await db.add_user("boris", "h", "Б", invite_required=False)
        farm1 = await db.add_user("farm1", "h", "Ф1", invite_required=False)
        farm2 = await db.add_user("farm2", "h", "Ф2", invite_required=False)
        # ферма: три регистрации подряд с одного адреса
        await antiabuse.record(farm1, "5.5.5.5", "register")
        await antiabuse.record(farm2, "5.5.5.5", "register")
        await antiabuse.record(b, "7.7.7.7", "register")
        sid = await info_db.ensure_sector("t", "Тест")
        fid = await info_db.add_fact(sid, {"title": "В ABH Лейпцига не продлевают без Резерв+",
                                           "country": "Германия", "city": "Лейпциг",
                                           "kind": "practice", "section": "protection"}, owner)
        for x in (a, b, farm1, farm2):
            await info_db.set_report(fid, x, "same")
        v = await info_db.country_view("t", "Германия")
        f = v["cities"][0]["facts"][0]
        assert f["same"] == 4                      # все отметки видны
        assert f["same_independent"] == 3          # ферма — один владелец

        # сам себя автор не подтверждает — ни этим аккаунтом, ни вторым
        with pytest.raises(info_db.InfoError):
            await info_db.set_report(fid, owner, "same")
        twin = await db.add_user("owner2", "h", "Двойник", invite_required=False)
        await antiabuse.record(owner, "9.9.9.9", "register")
        await antiabuse.record(twin, "9.9.9.9", "register")
        await info_db.set_report(fid, twin, "same")
        f = (await info_db.country_view("t", "Германия"))["cities"][0]["facts"][0]
        assert f["same"] == 5 and f["same_independent"] == 3

        # сырой адрес не хранится нигде
        pool = db._pool_or_raise()
        async with pool.acquire() as conn:
            dump = await conn.fetch("SELECT net_hash, context FROM account_signals")
        assert all("5.5.5.5" not in (r["net_hash"] + (r["context"] or "")) for r in dump)

        groups = await antiabuse.link_groups()
        assert {g["size"] for g in groups} == {2}
    _run(body)


@needs_db
def test_same_action_from_same_network_links_accounts():
    async def body(db):
        owner = await db.add_user("owner", "h", "Автор", invite_required=False)
        x = await db.add_user("x1", "h", "X", invite_required=False)
        y = await db.add_user("y1", "h", "Y", invite_required=False)
        sid = await info_db.ensure_sector("t", "Тест")
        fid = await info_db.add_fact(sid, {"title": "Сведение"}, owner)
        for u in (x, y):
            await info_db.set_report(fid, u, "same")
            await antiabuse.record(u, "8.8.4.4", "action", f"fact:{fid}")
        assert await antiabuse.independent_count([x, y]) == 1
    _run(body)


@needs_db
def test_sector_view_puts_common_law_first_and_counts_countries():
    async def body(db):
        owner = await db.add_user("owner", "h", "Автор", invite_required=False)
        sid = await info_db.ensure_sector("ua", "Украинцы в ЕС")
        await info_db.add_fact(sid, {"title": "Директива 2001/55/ЕС", "kind": "norm",
                                     "section": "basis", "source_url": "https://e.eu",
                                     "source_quote": "temporary protection"}, owner)
        await info_db.add_fact(sid, {"title": "Практика", "country": "Польша",
                                     "kind": "practice"}, owner)
        await info_db.add_fact(sid, {"title": "Город", "country": "Польша", "city": "Краков",
                                     "kind": "unverified"}, owner)
        v = await info_db.sector_view("ua")
        assert [f["title"] for f in v["common"]] == ["Директива 2001/55/ЕС"]
        pl = v["countries"][0]
        assert pl["country"] == "Польша" and pl["facts"] == 2 and pl["cities"] == ["Краков"]
        assert pl["practice"] == 1 and pl["unverified"] == 1
        found = await info_db.search(sid, "в Кракове что?", "Польша")
        assert found and found[0]["title"] == "Город"
    _run(body)


@needs_db
def test_daily_limit_on_new_facts():
    async def body(db):
        u = await db.add_user("u", "h", "U", invite_required=False)
        sid = await info_db.ensure_sector("t", "Тест")
        for i in range(info_db.FACTS_PER_DAY):
            await info_db.add_fact(sid, {"title": f"С{i}"}, u)
        with pytest.raises(info_db.InfoError):
            await info_db.add_fact(sid, {"title": "лишнее"}, u)
    _run(body)
