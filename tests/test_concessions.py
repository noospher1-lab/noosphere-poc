"""
Уступка — второе действие ответа (vault: decisions/2026-09-15-concession-act).

Живой случай (стенд 14.09): Вера (#43) и Оксана (#44) признали «третий
вариант» Alex и обе возразили остальному. Ребро держит одно отношение — в
графе осталось голое «против», признание потерялось. По Inference Anchoring
Theory ребро (связь утверждений) и уступка (действие реплики) — разные слои.

Идут без базы: сборка запроса разбора и поиск цитаты. С TEST_DATABASE_URL —
публикация, чтение дерева и панели, проверка разбора и восстановимость из лога.
"""

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from app import auth, pools  # noqa: E402

TEST_DB = os.environ.get("TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not TEST_DB, reason="TEST_DATABASE_URL not set")

PARENT_TEXT = ("Третий вариант: каждый говорит на своём языке, и никто не "
               "обязан переходить.\nОтказ обслужить по языку запрещён.")


# ---------------------------------------------------------------- без базы
def test_span_finds_quote_even_with_collapsed_whitespace():
    from app import main
    exact = main._concession_span(PARENT_TEXT, "Отказ обслужить по языку запрещён.")
    assert exact["anchor_quote"] == "Отказ обслужить по языку запрещён."
    assert PARENT_TEXT[exact["anchor_start"]:exact["anchor_end"]] == exact["anchor_quote"]

    # модель схлопнула перенос строки — участок всё равно указывает в текст
    soft = main._concession_span(PARENT_TEXT, "не обязан переходить. Отказ обслужить")
    assert soft is not None
    assert PARENT_TEXT[soft["anchor_start"]:soft["anchor_end"]] == soft["anchor_quote"]
    assert "\n" in soft["anchor_quote"]


def test_span_rejects_what_the_parent_never_said():
    from app import main
    assert main._concession_span(PARENT_TEXT, "язык не важен") is None


def test_span_without_quote_is_an_unspecified_concession():
    from app import main
    assert main._concession_span(PARENT_TEXT, "  ") == {
        "anchor_hash": None, "anchor_start": None,
        "anchor_end": None, "anchor_quote": None}


def _capture(monkeypatch):
    seen = {}

    def fake_complete(system, user, **kw):
        seen["user"] = user
        return json.dumps({"actual_type": "refute", "verdict": "new"})

    monkeypatch.setattr(pools.poi, "complete", fake_complete)
    return seen


def test_review_listing_marks_concessions(monkeypatch):
    seen = _capture(monkeypatch)
    branch = [
        {"id": 42, "depth": 0, "kind": "argument", "rel": None, "parent_id": None,
         "author_id": 11, "text": PARENT_TEXT},
        {"id": 43, "depth": 1, "kind": "argument", "rel": "refute", "parent_id": 42,
         "author_id": 20, "text": "Годится, но скрытый отказ он не ловит.",
         "concedes": True, "concede_quote": "Третий вариант"},
        {"id": 44, "depth": 1, "kind": "argument", "rel": "refute", "parent_id": 42,
         "author_id": 21, "text": "Это моя страна.", "concedes": False},
    ]
    pools.review_draft("текст", {"id": 42, "text": PARENT_TEXT}, branch, [])
    user = seen["user"]
    assert "[43] (argument, refute -> 42, CONCEDES «Третий вариант»)" in user
    assert "[44] (argument, refute -> 42)" in user
    assert "CONCEDES marks a reply that grants the quoted part" in user


def test_reply_review_asks_for_a_verbatim_concession(monkeypatch):
    seen = _capture(monkeypatch)
    pools.review_draft("текст", {"id": 42, "text": PARENT_TEXT}, [], [])
    assert "4b. CONCESSION" in seen["user"]
    assert '"concedes": "verbatim quote' in seen["user"]


def test_root_review_has_no_concession(monkeypatch):
    seen = _capture(monkeypatch)
    pools.review_draft("текст", None, [], [], root_kind="argument")
    assert "CONCESSION" not in seen["user"]
    assert '"concedes"' not in seen["user"]


# ---------------------------------------------------------------- с базой
async def _prepare(username, service):
    from app import db
    db.DATABASE_URL = TEST_DB
    await db.close_pool()
    await db.init_pool()
    await db.init_db()
    await db.wipe(force=True)
    uid = await db.add_user(username, auth.hash_password("secret1"), "Вера",
                            "#fff", email=f"{username}@example.com",
                            invite_required=False)
    pool = db._pool_or_raise()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE authors SET email_verified = TRUE, balance_usd = 5, "
            "is_service = $2 WHERE id = $1", uid, service)
    root = await db.add_node(PARENT_TEXT, author_id=uid, kind="argument",
                             title="Третий вариант")
    await db.close_pool()
    return uid, root


def _client(username, service):
    from fastapi.testclient import TestClient
    from app import db, main

    uid, root = asyncio.run(_prepare(username, service))
    main._REVIEW_CACHE.clear()
    main._llm_calls.clear()
    main._login_calls.clear()
    db.DATABASE_URL = TEST_DB
    c = TestClient(main.app)
    c.__enter__()
    r = c.post("/api/auth/login", json={"username": username, "password": "secret1"})
    assert r.status_code == 200, r.text
    c.ids = {"author": uid, "root": root}
    return c


@pytest.fixture
def publisher():
    """Служебный автор: публикует без фоновой оценки — модель не нужна."""
    c = _client("concede_bot", True)
    yield c
    c.__exit__(None, None, None)


@pytest.fixture
def reviewer():
    c = _client("concede_user", False)
    yield c
    c.__exit__(None, None, None)


@needs_db
def test_reply_carries_concession_into_tree_panel_and_log(publisher):
    root = publisher.ids["root"]
    r = publisher.post("/api/argument", json={
        "text": "Третий вариант годится, но скрытый отказ он не ловит.",
        "connect_to": root, "edge_type": "refute",
        "concedes": {"quote": "Третий вариант: каждый говорит на своём языке"}})
    assert r.status_code == 200, r.text
    reply = r.json()["id"]

    # обычный ответ — без метки
    plain = publisher.post("/api/argument", json={
        "text": "Это моя страна.", "connect_to": root, "edge_type": "refute"})
    assert plain.status_code == 200, plain.text

    kids = {k["id"]: k for k in publisher.get(f"/api/nodes/{root}/children").json()["children"]}
    assert kids[reply]["rel"] == "refute"                       # связь не тронута
    assert kids[reply]["concedes"] is True
    assert kids[reply]["concede_quote"] == "Третий вариант: каждый говорит на своём языке"
    assert kids[plain.json()["id"]]["concedes"] is False

    panel = publisher.get(f"/api/nodes/{root}").json()
    assert [(c["source_id"], c["rel"]) for c in panel["conceded_by"]] == [(reply, "refute")]

    # в цикле самого приложения: пул базы у TestClient свой, второй не поднять
    async def check_log():
        from app import db, replay
        sub = await db.topic_subtree(root)
        mark = {n["id"]: n["concedes"] for n in sub}
        assert mark[reply] is True and mark[root] is False
        problems, replayed = await replay.verify()
        assert not [p for p in problems if p.startswith("concessions")], problems
        assert (reply, root, "Третий вариант: каждый говорит на своём языке") \
            in replayed["concessions"]
    publisher.portal.call(check_log)


@needs_db
def test_concession_is_refused_where_it_means_nothing(publisher):
    root = publisher.ids["root"]
    # «за» и так согласие
    r = publisher.post("/api/argument", json={
        "text": "Да.", "connect_to": root, "edge_type": "support",
        "concedes": {"quote": "Третий вариант"}})
    assert r.status_code == 400
    # признать можно только сказанное
    r = publisher.post("/api/argument", json={
        "text": "Нет.", "connect_to": root, "edge_type": "refute",
        "concedes": {"quote": "язык вообще не важен"}})
    assert r.status_code == 400
    # и только в ответе
    r = publisher.post("/api/argument", json={
        "text": "Новая тема", "title": "Новая", "kind": "argument",
        "concedes": {"quote": "Третий вариант"}})
    assert r.status_code == 400
    # отказ не оставил узлов
    assert publisher.get(f"/api/nodes/{root}/children").json()["total"] == 0


@needs_db
def test_review_keeps_only_quotes_the_parent_actually_contains(reviewer, monkeypatch):
    from app import main
    from app import pools as pools_mod
    root = reviewer.ids["root"]
    base = {"actual_type": "refute", "type_note": "", "quality_note": "",
            "verdict": "new", "node_id": None, "position_id": None, "note": "",
            "split": None, "placement": "here", "place_id": None,
            "place_note": "", "think": ""}

    monkeypatch.setattr(pools_mod, "review_draft",
                        lambda *a, **kw: {**base, "concedes": "Отказ обслужить по языку запрещён."})
    out = reviewer.post("/api/draft/review", json={
        "text": "Запрет хорош, но скрытый отказ он не ловит.",
        "connect_to": root, "edge_type": "refute"}).json()
    assert out["concedes"] == "Отказ обслужить по языку запрещён."

    main._REVIEW_CACHE.clear()
    monkeypatch.setattr(pools_mod, "review_draft",
                        lambda *a, **kw: {**base, "concedes": "этого там нет"})
    out = reviewer.post("/api/draft/review", json={
        "text": "Запрет хорош, но скрытый отказ он не ловит.",
        "connect_to": root, "edge_type": "refute"}).json()
    assert out["concedes"] == ""

    # «за» уступкой не бывает, даже если модель её назвала
    main._REVIEW_CACHE.clear()
    monkeypatch.setattr(pools_mod, "review_draft",
                        lambda *a, **kw: {**base, "actual_type": "support",
                                          "concedes": "Третий вариант"})
    out = reviewer.post("/api/draft/review", json={
        "text": "Согласна.", "connect_to": root, "edge_type": "support"}).json()
    assert out["concedes"] == ""
