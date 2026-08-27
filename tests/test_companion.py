"""
ИИ-компаньон до публикации (vault: decisions/edit-delete-window,
decisions/ai-navigator-draft-review).

Проверяется всё, кроме самой модели: LLM подменяется заглушкой. Интересует
серверная обвязка — что ответ модели не пролезает в граф без проверки, что
разговор ограничен по длине и что обрыв компаньона не роняет публикацию.
"""

import asyncio
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from app import auth  # noqa: E402

TEST_DB = os.environ.get("TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not TEST_DB, reason="TEST_DATABASE_URL not set")


@pytest.fixture
def client(monkeypatch):
    """Приложение поверх тестовой БД, с вошедшим подтверждённым автором."""
    from fastapi.testclient import TestClient
    from app import db, main

    async def prepare():
        db.DATABASE_URL = TEST_DB
        await db.close_pool()
        await db.init_pool()
        await db.init_db()
        await db.wipe(force=True)
        uid = await db.add_user("companion_user", auth.hash_password("secret1"),
                                "Автор", "#fff", email="c@example.com",
                                invite_required=False)
        pool = db._pool_or_raise()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE authors SET email_verified = TRUE, balance_usd = 5 "
                "WHERE id = $1", uid)
        root = await db.add_node("Пробки в городе", author_id=uid, kind="problem",
                                 title="Пробки в городе")
        other = await db.add_node(
            "Загрязнение воздуха в городах остаётся высоким",
            author_id=uid, kind="problem", title="Загрязнение воздуха")
        await db.close_pool()
        return uid, root, other

    uid, root, other = asyncio.run(prepare())
    # кеш разбора живёт в модуле и переживает тест: без сброса следующий тест с
    # тем же черновиком получил бы чужой заготовленный ответ
    main._REVIEW_CACHE.clear()
    # бюджет списывается через тот же пул, что и остальное приложение —
    # TestClient поднимет его заново в своём цикле на старте
    db.DATABASE_URL = TEST_DB
    with TestClient(main.app) as c:
        r = c.post("/api/auth/login",
                   json={"username": "companion_user", "password": "secret1"})
        assert r.status_code == 200, r.text
        c.ids = {"author": uid, "root": root, "other": other}
        yield c


@pytest.fixture
def service_client(monkeypatch):
    """То же приложение, но вошедший автор помечен служебным."""
    from fastapi.testclient import TestClient
    from app import db, main

    async def prepare():
        db.DATABASE_URL = TEST_DB
        await db.close_pool()
        await db.init_pool()
        await db.init_db()
        await db.wipe(force=True)
        uid = await db.add_user("platform_bot", auth.hash_password("secret1"),
                                "NOOSPHERE AI BOT", "#66b0ea",
                                email="bot@example.com", invite_required=False)
        pool = db._pool_or_raise()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE authors SET email_verified = TRUE, balance_usd = 0, "
                "is_service = TRUE WHERE id = $1", uid)
        await db.close_pool()
        return uid

    uid = asyncio.run(prepare())
    main._REVIEW_CACHE.clear()
    db.DATABASE_URL = TEST_DB
    with TestClient(main.app) as c:
        assert c.post("/api/auth/login",
                      json={"username": "platform_bot",
                            "password": "secret1"}).status_code == 200
        c.author_id = uid
        yield c


def _stub_review(monkeypatch, payload):
    from app import pools as pools_mod
    monkeypatch.setattr(pools_mod, "review_draft",
                        lambda *a, **kw: dict(payload))


@needs_db
def test_placement_elsewhere_only_for_offered_problems(client, monkeypatch):
    """LLM может назвать любой id — принимаем только тот, что сами предложили.

    Иначе модель (или текст, который ею манипулирует) уводила бы автора к
    произвольному узлу графа.
    """
    _stub_review(monkeypatch, {
        "actual_type": "argument", "type_note": "", "quality_note": "",
        "verdict": "new", "node_id": None, "position_id": None, "note": "",
        "split": None, "placement": "elsewhere",
        "place_id": 999999,                       # такого узла не предлагали
        "place_note": "это про другое", "think": "",
    })
    r = client.post("/api/draft/review",
                    json={"text": "Расширение дорог не снижает пробки", "kind": "argument"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["placement"] == "here"             # подложный id отброшен
    assert out["place_id"] is None


@needs_db
def test_own_problem_ignored_for_a_root(client, monkeypatch):
    """«Заведи отдельную проблему» бессмысленно советовать корню — он и есть корень."""
    _stub_review(monkeypatch, {
        "actual_type": "argument", "type_note": "", "quality_note": "",
        "verdict": "new", "node_id": None, "position_id": None, "note": "",
        "split": None, "placement": "own_problem", "place_id": None,
        "place_note": "тянет на свою тему", "think": "",
    })
    r = client.post("/api/draft/review",
                    json={"text": "Совсем другая проблема", "kind": "argument"})
    assert r.json()["placement"] == "here"


@needs_db
def test_think_question_reaches_the_author(client, monkeypatch):
    _stub_review(monkeypatch, {
        "actual_type": "argument", "type_note": "", "quality_note": "",
        "verdict": "new", "node_id": None, "position_id": None, "note": "",
        "split": None, "placement": "here", "place_id": None, "place_note": "",
        "think": "через какой механизм спрос догоняет пропускную способность?",
    })
    r = client.post("/api/draft/review",
                    json={"text": "Расширение дорог не снижает пробки", "kind": "argument"})
    assert "механизм" in r.json()["think"]


@needs_db
def test_companion_turn_returns_reply_and_suggestion(client, monkeypatch):
    from app import pools as pools_mod
    seen = {}

    def fake(text, history, parent=None, branch=None, neighbours=None,
             turns_left=None):
        seen["text"] = text
        seen["history"] = history
        seen["turns_left"] = turns_left
        return {"reply": "а что с городами, где полосы убрали?",
                "suggestion": "Расширение дорог не снижает пробки: свободная "
                              "полоса немедленно заполняется отложенными поездками."}

    monkeypatch.setattr(pools_mod, "companion_reply", fake)
    r = client.post("/api/draft/companion", json={
        "text": "Расширение дорог не снижает пробки",
        "history": [{"role": "companion", "text": "через какой механизм?"},
                    {"role": "author", "text": "через индуцированный спрос"}],
    })
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["reply"].startswith("а что")
    assert "отложенными поездками" in out["suggestion"]
    # компаньон читает ЖИВОЙ черновик и весь разговор, а не только последний ход
    assert seen["text"] == "Расширение дорог не снижает пробки"
    assert len(seen["history"]) == 2
    # и знает, сколько ходов осталось — чтобы успеть отдать формулировку до
    # того, как разговор упрётся в потолок (в истории один ход автора + этот)
    from app.main import COMPANION_MAX_TURNS
    assert seen["turns_left"] == COMPANION_MAX_TURNS - 2
    assert out["turns_left"] == COMPANION_MAX_TURNS - 2


@needs_db
def test_companion_refuses_empty_draft_and_endless_talk(client, monkeypatch):
    from app import pools as pools_mod
    monkeypatch.setattr(pools_mod, "companion_reply",
                        lambda *a, **kw: {"reply": "ок", "suggestion": None})

    assert client.post("/api/draft/companion", json={"text": "   "}).status_code == 400

    from app.main import COMPANION_MAX_TURNS

    # ровно на потолке разговор ещё живой, и остаток честно равен нулю
    at_cap = [{"role": "author", "text": "ещё"}
              for _ in range(COMPANION_MAX_TURNS - 1)]
    r = client.post("/api/draft/companion",
                    json={"text": "черновик", "history": at_cap})
    assert r.status_code == 200, r.text
    assert r.json()["turns_left"] == 0

    # а следующий ход — отказ, и он называет потолок вместо глухого «нельзя»
    over = [{"role": "author", "text": "ещё"}
            for _ in range(COMPANION_MAX_TURNS)]
    r = client.post("/api/draft/companion",
                    json={"text": "черновик", "history": over})
    assert r.status_code == 400
    assert str(COMPANION_MAX_TURNS) in r.json()["detail"]


@needs_db
def test_dead_companion_says_so_but_review_stays_silent(client, monkeypatch):
    """Разные роли — разное поведение при поломке.

    Разбор перед публикацией fail-open: он не должен мешать публиковать. А
    оборванный РАЗГОВОР надо назвать вслух, иначе молчание компаньона читается
    как «замечаний нет».
    """
    from app import pools as pools_mod

    def boom(*a, **kw):
        raise RuntimeError("модель недоступна")

    monkeypatch.setattr(pools_mod, "review_draft", boom)
    monkeypatch.setattr(pools_mod, "companion_reply", boom)

    r = client.post("/api/draft/review",
                    json={"text": "любой довод", "kind": "argument"})
    assert r.status_code == 200
    assert r.json()["verdict"] == "new" and r.json()["quality_note"] == ""

    r = client.post("/api/draft/companion", json={"text": "любой довод"})
    assert r.status_code == 502
    assert "компаньон" in r.json()["detail"]


# ------------------------------------------------- служебный аккаунт
@needs_db
def test_service_account_gets_no_ai_and_no_poi(service_client, monkeypatch):
    """NOOSPHERE AI BOT — голос платформы, а не участник.

    Он публикует служебные тексты, но ИИ ему не положен: ни компаньон, ни
    оценка собственных текстов. Иначе машина, знающая рубрику изнутри, стояла
    бы в одном рейтинге с людьми и выше них.
    """
    from app import main, pools as pools_mod

    scored = []
    monkeypatch.setattr(main, "_score_later",
                        lambda *a, **kw: scored.append(a))
    monkeypatch.setattr(main, "_spawn", lambda coro: None)
    monkeypatch.setattr(pools_mod, "companion_reply",
                        lambda *a, **kw: {"reply": "ок", "suggestion": None})

    # ИИ закрыт отдельным кодом: дело не в деньгах, которые можно доложить
    r = service_client.post("/api/draft/companion", json={"text": "черновик"})
    assert r.status_code == 403, r.text
    assert "служебный" in r.json()["detail"]

    r = service_client.post("/api/draft/review",
                            json={"text": "черновик", "kind": "argument"})
    assert r.status_code == 403, r.text

    # но публиковать он может — и текст остаётся без оценки
    r = service_client.post("/api/argument", json={
        "text": "Служебное объявление платформы", "kind": "question",
        "title": "Объявление", "domain": "tech", "sub": "Платформы и модерация"})
    assert r.status_code == 200, r.text
    assert r.json()["scoring"] == "off"
    assert r.json()["poi_score"] is None
    assert scored == [], "служебный текст не должен уходить на оценку"

    # и виден остальным как служебный — UI по этому признаку не пишет
    # «оценивается…» о тексте, который не будет оценён никогда
    node = service_client.get("/api/nodes/%s" % r.json()["id"]).json()
    assert node["author_is_service"] is True
