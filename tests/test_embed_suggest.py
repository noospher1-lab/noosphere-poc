"""
Поиск похожих проблем по смыслу (app/embed.py + db.suggest_problems).

Модель здесь не грузится: векторы подставляются руками. Проверяется обвязка —
что смысловые совпадения приходят первыми и ловят то, чего триграммы не видят
(русский запрос ↔ украинская проблема), что порог отсекает шум, что без
вектора запроса поиск остаётся лексическим, и что разбор черновика получает
кандидатов схождения через тот же путь.

Идут только при TEST_DATABASE_URL — стирают базу.
"""

import asyncio
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from app import auth, embed as embed_mod  # noqa: E402

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


# «векторы» из трёх измерений: язык, мова, представительство
UK_LANG = [1.0, 0.1, 0.0]
UK_REPR = [0.0, 0.1, 1.0]
RU_LANG_QUERY = [0.95, 0.2, 0.05]        # русская постановка о запрете языка
RU_OTHER_QUERY = [0.0, 1.0, 0.0]         # ни о чём из этого (косинус ≈ 0,1 к обеим)


def test_semantic_hits_come_first_and_cross_languages():
    async def body():
        db = await _fresh_db()
        uid = await db.add_user("emb", "hash", "Автор", invite_required=False)
        uk = await db.add_node("Уряд обмежує російську мову в школах.", author_id=uid,
                               kind="problem", title="Заборона російської мови")
        rep = await db.add_node("Обрані ведуть політику всупереч виборцям.",
                                author_id=uid, kind="problem",
                                title="Представники не виконують волю виборців")
        await db.upsert_embedding(uk, embed_mod.MODEL, UK_LANG, "h1")
        await db.upsert_embedding(rep, embed_mod.MODEL, UK_REPR, "h2")

        # триграммы русский запрос не ловят — а вектор ловит
        lex = await db.suggest_problems("Запрет русского языка в Украине")
        assert [d["id"] for d in lex] == []
        sem = await db.suggest_problems("Запрет русского языка в Украине",
                                        qvec=RU_LANG_QUERY)
        assert [d["id"] for d in sem] == [uk]
        assert sem[0]["semantic"] is True and sem[0]["sim"] > embed_mod.SIM_FLOOR

        # порог: посторонний запрос не тянет ничего
        assert await db.suggest_problems("Рост цен на жильё", qvec=RU_OTHER_QUERY) == []

        # исключение своей же проблемы
        assert await db.suggest_problems("Запрет русского языка", qvec=RU_LANG_QUERY,
                                         exclude_id=uk) == []

        # чужая модель — векторы не сравниваются
        await db.upsert_embedding(uk, "other-model", UK_LANG, "h1")
        assert await db.suggest_problems("Запрет русского языка", qvec=RU_LANG_QUERY) == []
    _run(body)


def test_backfill_finds_missing_and_stale_vectors():
    async def body():
        db = await _fresh_db()
        uid = await db.add_user("emb2", "hash", "Автор", invite_required=False)
        a = await db.add_node("текст а", author_id=uid, kind="problem", title="А")
        b = await db.add_node("текст б", author_id=uid, kind="problem", title="Б")
        q = await db.add_node("вопрос", author_id=uid, kind="question", title="В")
        hf = lambda t, x: embed_mod.text_hash(embed_mod.problem_text(t, x))
        todo = await db.problems_needing_embedding(embed_mod.MODEL, hf)
        assert [p["id"] for p in todo] == [a, b]          # вопрос не проблема
        await db.upsert_embedding(a, embed_mod.MODEL, [1, 0, 0], hf("А", "текст а"))
        await db.upsert_embedding(b, embed_mod.MODEL, [0, 1, 0], "устаревший")
        todo = await db.problems_needing_embedding(embed_mod.MODEL, hf)
        assert [p["id"] for p in todo] == [b]             # у б текст «изменился»
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
        pool = db._pool_or_raise()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE authors SET email_verified = TRUE, balance_usd = 5 WHERE id = $1", uid)
        effect = await db.add_node("Уряд обмежує російську мову.", author_id=uid,
                                   kind="problem", title="Заборона російської мови")
        rep = await db.add_node("Обрані ведуть політику всупереч виборцям.",
                                author_id=uid, kind="problem",
                                title="Представники не виконують волю виборців")
        await db.upsert_embedding(effect, embed_mod.MODEL, UK_LANG, "h1")
        await db.upsert_embedding(rep, embed_mod.MODEL, UK_REPR, "h2")
        await db.close_pool()
        return {"effect": effect, "rep": rep}

    ids = asyncio.run(prepare())
    main._login_calls.clear()
    main._REVIEW_CACHE.clear()
    main._llm_calls.clear()
    db.DATABASE_URL = TEST_DB
    with TestClient(main.app) as c:
        r = c.post("/api/auth/login", json={"username": "author1", "password": "secret1"})
        assert r.status_code == 200, r.text
        c.ids = ids
        yield c


def test_cause_matches_converge_across_languages(client, monkeypatch):
    """Русский заголовок причины находит украинскую проблему — через вектор,
    который триграммам недоступен."""
    from app import main as main_mod, pools as pools_mod

    async def fake_qvec(text):
        return [0.0, 0.1, 0.95] if "Депутаты" in text else None
    monkeypatch.setattr(embed_mod, "query_vector", fake_qvec)
    monkeypatch.setattr(pools_mod, "review_draft", lambda *a, **kw: {
        "actual_type": "support", "type_note": "", "quality_note": "",
        "verdict": "new", "node_id": None, "position_id": None, "note": "",
        "split": None, "placement": "cause",
        "cause_title": "Депутаты не выполняют волю избирателей",
        "place_id": None, "place_note": "", "think": ""})
    out = client.post("/api/draft/review",
                      json={"text": "Дело не в законе, а в депутатах",
                            "connect_to": client.ids["effect"]}).json()
    assert out["placement"] == "cause"
    assert [m["id"] for m in out["cause_matches"]] == [client.ids["rep"]]

    # подсказка дублей при создании — тот же путь
    async def fake_qvec2(text):
        return RU_LANG_QUERY
    monkeypatch.setattr(embed_mod, "query_vector", fake_qvec2)
    hits = client.get("/api/problems/suggest?title=Запрет русского языка").json()
    assert [h["id"] for h in hits] == [client.ids["effect"]]
