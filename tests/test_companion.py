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
    main._login_calls.clear()   # см. ниже: тот же общий счётчик, ключ — IP
    main._REVIEW_CACHE.clear()
    # то же и со счётчиком «ИИ-запросов в минуту»: он в модуле и ключом берёт
    # id автора, а после wipe id повторяется. Тесты складывались в одну
    # корзину, и стоило добавить пару вызовов — падал не тот тест, который их
    # добавил, а следующий, с 429 вместо ответа. Сбрасываем.
    main._llm_calls.clear()
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
    main._llm_calls.clear()
    main._login_calls.clear()
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
def test_own_problem_ignored_for_a_problem_root(client, monkeypatch):
    """«Заведи отдельную проблему» бессмысленно советовать проблеме — она и есть
    корень, и уже проблема. А вот корню ДРУГОГО вида это осмысленно: «тут
    заявлен вред, ему место в проблеме с накопителем попыток»."""
    _stub_review(monkeypatch, {
        "actual_type": "argument", "type_note": "", "quality_note": "",
        "verdict": "new", "node_id": None, "position_id": None, "note": "",
        "split": None, "placement": "own_problem", "place_id": None,
        "place_note": "тут заявлен вред", "think": "",
    })
    r = client.post("/api/draft/review",
                    json={"text": "Совсем другая проблема", "kind": "problem"})
    assert r.json()["placement"] == "here"

    from app import main as main_mod
    main_mod._REVIEW_CACHE.clear()
    r = client.post("/api/draft/review",
                    json={"text": "Совсем другая проблема", "kind": "question"})
    assert r.json()["placement"] == "own_problem"


@needs_db
def test_root_kind_is_compared_again(client, monkeypatch):
    """Виды корня вернулись (2026-09-09): проблема проходит ТЕСТ на вред, а
    вопрос/предложение/тезис/разбор сравниваются по форме, как ответы."""
    from app import main as main_mod

    # 1. Проблема: вердикт not_problem — предложение сменить вид, не запрет.
    _stub_review(monkeypatch, {
        "actual_type": "not_problem", "type_note": "вреда не видно",
        "quality_note": "", "verdict": "new", "node_id": None,
        "position_id": None, "note": "", "split": None, "placement": "here",
        "place_id": None, "place_note": "", "think": "",
    })
    out = client.post("/api/draft/review",
                      json={"text": "Что вообще такое справедливость?",
                            "kind": "problem"}).json()
    assert out["type_ok"] is False
    assert out["suggested_type"] == "not_problem"

    # 2. Тот же текст как ВОПРОС — рамка другая, значит и кэш другой: модель
    #    зовут заново и она классифицирует по видам.
    main_mod._REVIEW_CACHE.clear()
    _stub_review(monkeypatch, {
        "actual_type": "exploration", "type_note": "это скорее разбор",
        "quality_note": "", "verdict": "new", "node_id": None,
        "position_id": None, "note": "", "split": None, "placement": "here",
        "place_id": None, "place_note": "", "think": "",
    })
    out = client.post("/api/draft/review",
                      json={"text": "Что вообще такое справедливость?",
                            "kind": "question"}).json()
    assert out["type_ok"] is False
    assert out["suggested_type"] == "exploration"

    # 3. Вид совпал — молчим.
    main_mod._REVIEW_CACHE.clear()
    out = client.post("/api/draft/review",
                      json={"text": "Что вообще такое справедливость?",
                            "kind": "exploration"}).json()
    assert out["type_ok"] is True
    assert out["suggested_type"] is None


@needs_db
def test_root_frame_is_part_of_the_review_cache_key(client, monkeypatch):
    """У проблемы и у вопроса РАЗНЫЕ промпты. Если рамка не в ключе кэша,
    переключение вида в форме отдаёт ответ на другой вопрос."""
    from app import pools as pools_mod

    seen = []

    def fake_review(text, parent, branch, positions, neighbours=None,
                    is_root=None, root_kind=None, **kw):
        seen.append(root_kind)
        return {"actual_type": "problem", "type_note": "", "quality_note": "",
                "verdict": "new", "node_id": None, "position_id": None,
                "note": "", "split": None, "placement": "here",
                "place_id": None, "place_note": "", "think": ""}

    monkeypatch.setattr(pools_mod, "review_draft", fake_review)
    body = {"text": "Один и тот же черновик"}
    client.post("/api/draft/review", json={**body, "kind": "problem"})
    client.post("/api/draft/review", json={**body, "kind": "question"})
    assert seen == ["problem", "question"], seen
    # а вот между собой не-проблемные виды рамку не меняют — второй раз модель
    # не зовут, переключение остаётся бесплатным
    client.post("/api/draft/review", json={**body, "kind": "proposal"})
    assert seen == ["problem", "question"], seen


@needs_db
def test_every_ai_turn_is_written_down(client, monkeypatch):
    """Разговор с ИИ до публикации больше не эфемерен (2026-09-09).

    Раньше он жил в браузере автора и умирал вместе с публикацией: понять
    постфактум, где человек споткнулся, было нечем — опубликованный текст
    показывает результат и молчит о том, что ему предшествовало. На время
    закрытого теста с живыми людьми это и понадобилось.
    """
    from app import main as main_mod

    token = os.environ.get("ADMIN_TOKEN")
    if not token:
        pytest.skip("ADMIN_TOKEN не задан — админский маршрут не проверить")
    head = {"X-Admin-Token": token}

    _stub_review(monkeypatch, {
        "actual_type": "not_problem", "type_note": "вреда не видно",
        "quality_note": "", "verdict": "new", "node_id": None,
        "position_id": None, "note": "", "split": None, "placement": "here",
        "place_id": None, "place_note": "", "think": "кого это задевает?",
    })
    r = client.post("/api/draft/review",
                    json={"text": "Все спорят про политику", "kind": "problem"})
    assert r.status_code == 200, r.text

    rows = client.get("/api/dev/companion", headers=head).json()
    assert len(rows) == 1, rows
    e = rows[0]
    assert e["kind"] == "review"
    assert e["draft"] == "Все спорят про политику"
    assert e["root_kind"] == "problem"
    # пишем то, что УВИДЕЛ автор, а не сырой ответ модели
    assert e["result"]["suggested_type"] == "not_problem"
    assert e["result"]["think"] == "кого это задевает?"

    # разговор с компаньоном ложится туда же, вместе с историей реплик
    main_mod._REVIEW_CACHE.clear()
    from app import pools as pools_mod
    monkeypatch.setattr(pools_mod, "companion_reply",
                        lambda *a, **kw: {"reply": "назови, кто теряет",
                                          "suggestion": ""})
    r = client.post("/api/draft/companion", json={
        "text": "Все спорят про политику",
        "history": [{"role": "author", "text": "а почему не проблема?"}],
    })
    assert r.status_code == 200, r.text

    rows = client.get("/api/dev/companion", headers=head).json()
    assert len(rows) == 2                      # новые сверху
    assert rows[0]["kind"] == "companion"
    assert rows[0]["history"] == [{"role": "author", "text": "а почему не проблема?"}]
    assert rows[0]["result"]["reply"] == "назови, кто теряет"

    # оглавление наблюдателя видит одного человека и два обращения
    who = client.get("/api/dev/companion/authors", headers=head).json()
    assert len(who) == 1 and who[0]["entries"] == 2


@needs_db
def test_companion_log_is_admin_only(client):
    """Черновики тестеров — не публичное чтение, в отличие от корпуса."""
    assert client.get("/api/dev/companion").status_code == 403
    assert client.get("/api/dev/companion",
                      headers={"X-Admin-Token": "wrong-token"}).status_code == 403


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
             turns_left=None, **kw):
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


@needs_db
def test_short_draft_takes_language_from_authors_past_texts(client, monkeypatch):
    """«проблема в коррупции» не выдаёт язык ни буквами, ни словами. Тогда язык
    берётся из прошлых текстов автора: сначала его черновиков, без них — узлов
    (vault: decisions/2026-09-14-reply-language)."""
    from app import pools as pools_mod
    seen = []

    def fake(text, history, *a, **kw):
        seen.append(kw.get("lang"))
        return {"reply": "ок", "suggestion": None}

    monkeypatch.setattr(pools_mod, "companion_reply", fake)

    def ask(text):
        r = client.post("/api/draft/companion", json={"text": text, "history": []})
        assert r.status_code == 200, r.text
        return seen[-1]

    # черновиков ещё нет — язык из узлов автора («…остаётся высоким»)
    assert ask("проблема в коррупции") == "Russian"
    # появился явный украинский черновик — черновики важнее узлов
    assert ask("Треба зробити щось краще, бо так далі не можна") == "Ukrainian"
    assert ask("проблема в коррупции") == "Ukrainian"


@needs_db
def test_review_and_companion_get_problem_state_and_links(client, monkeypatch):
    """Сервер кладёт в разбор и компаньона карточку проблемы, реестр и связанные
    проблемы; к связанной проблеме разбор может отправить автора, хотя в
    соседях её не было (vault: decisions/2026-09-14-ai-reads-problem-state)."""
    from app import db, pools as pools_mod
    root, cause, uid = client.ids["root"], client.ids["other"], client.ids["author"]

    async def fill():
        await db.set_problem(root, causes="Город растёт быстрее дорог",
                             gap="Нет данных по пригородам", author_id=uid)
        await db.add_intervention(root, "Платные парковки в центре", geo="Рига",
                                  outcome_kind="failure", author_id=uid)
        await db.add_problem_link(cause, root, author_id=uid)   # «загрязнение» порождает «пробки»

    client.portal.call(fill)
    seen = {}

    def fake_review(*a, **kw):
        seen["review"] = kw.get("problem")
        return {"actual_type": "support", "verdict": "new", "placement": "elsewhere",
                "place_id": cause, "place_note": "это про причину"}

    def fake_companion(text, history, *a, **kw):
        seen["companion"] = kw.get("problem")
        return {"reply": "ок", "suggestion": None}

    monkeypatch.setattr(pools_mod, "review_draft", fake_review)
    monkeypatch.setattr(pools_mod, "companion_reply", fake_companion)

    r = client.post("/api/draft/review", json={
        "text": "Выхлопы машин в час пик", "connect_to": root, "edge_type": "support"})
    assert r.status_code == 200, r.text
    p = seen["review"]
    assert p["causes_text"] == "Город растёт быстрее дорог"
    assert p["gap"] == "Нет данных по пригородам"
    assert p["registry_total"] == 1 and p["registry"][0]["what"] == "Платные парковки в центре"
    assert p["outcomes"] == {"failure": 1}
    assert [c["id"] for c in p["causes"]] == [cause] and p["effects"] == []
    # отправить к связанной причине можно — её модель видела
    assert r.json()["placement"] == "elsewhere" and r.json()["place_id"] == cause

    r = client.post("/api/draft/companion", json={
        "text": "Выхлопы машин в час пик", "connect_to": root, "history": []})
    assert r.status_code == 200, r.text
    assert [c["id"] for c in seen["companion"]["causes"]] == [cause]


@needs_db
def test_review_says_said_and_knows_the_node_is_yours(client, monkeypatch):
    """Разбор указал на узел с тем же тезисом, что и черновик. Если узел написал сам
    автор, сервер отдаёт node_own — UI говорит «ты это уже писал», а не
    «уже есть возражение» (стенд 14.09, vault: decisions/2026-09-14-positions-and-topic-language)."""
    from app import db, pools as pools_mod
    root, uid = client.ids["root"], client.ids["author"]

    async def seed():
        mine = await db.add_node("Выделенные полосы для автобусов разгружают центр",
                                 author_id=uid, topic_root_id=root)
        await db.add_edge(mine, root, "support")
        return mine

    mine = client.portal.call(seed)
    seen = {}

    def fake_review(*a, **kw):
        seen["author_id"] = kw.get("author_id")
        seen["branch"] = a[2] if len(a) > 2 else kw.get("branch")
        return {"actual_type": "support", "verdict": "said", "node_id": mine,
                "note": "ты уже писал это в соседнем узле", "placement": "here"}

    monkeypatch.setattr(pools_mod, "review_draft", fake_review)
    r = client.post("/api/draft/review", json={
        "text": "Автобусные полосы разгружают центр города", "connect_to": root,
        "edge_type": "support"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["verdict"] == "said" and out["node_id"] == mine
    assert out["node_own"] is True
    # сервер сказал модели, кто автор черновика, и ветка несёт авторов узлов
    assert seen["author_id"] == uid
    assert any(n["id"] == mine and n["author_id"] == uid for n in seen["branch"])
