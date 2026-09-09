"""
Доска обсуждения: «решения на столе» (предложения) и «открытые вопросы».

Оба вида узлов жили в графе и раньше, но увидеть их можно было только развернув
нужную ветку дерева. Здесь проверяется сама выборка: что доска берёт
принадлежность через `node_topics` (а не по скаляру `topic_root_id`), что она
не тащит в списки атомы и сам корень, и что «открытость» вопроса считается по
ответам, а не по флагу.

Как и остальные тесты слоя БД, идут только при TEST_DATABASE_URL — они вытирают
базу.
"""

import asyncio
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

TEST_DB = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DB, reason="TEST_DATABASE_URL not set")


def _run(coro_fn):
    """Пул закрывается в том же цикле, в котором открылся (см. test_stats)."""
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


def test_board_splits_proposals_and_open_questions():
    async def body():
        db = await _fresh_db()
        uid = await db.add_user("boarder", "hash", "Автор", invite_required=False)
        root = await db.add_node("Проблема с состоянием", author_id=uid,
                                 kind="problem", title="Проблема")

        prop = await db.add_node("Давайте сделаем X", author_id=uid,
                                 kind="proposal", topic_root_id=root)
        await db.add_edge(prop, root, "proposal")
        q_open = await db.add_node("А кто это проверяет?", author_id=uid,
                                   kind="question", topic_root_id=root)
        await db.add_edge(q_open, root, "question")
        q_answered = await db.add_node("А есть ли случаи наоборот?", author_id=uid,
                                       kind="question", topic_root_id=root)
        await db.add_edge(q_answered, root, "question")
        ans = await db.add_node("Да, вот такой", author_id=uid,
                                kind="argument", topic_root_id=root)
        await db.add_edge(ans, q_answered, "support")
        # обычный довод в доску не входит — она про предложения и вопросы
        plain = await db.add_node("Просто довод", author_id=uid,
                                  kind="argument", topic_root_id=root)
        await db.add_edge(plain, root, "support")

        board = await db.topic_board(root)
        assert [p["id"] for p in board["proposals"]] == [prop]
        assert sorted(q["id"] for q in board["questions"]) == sorted([q_open, q_answered])

        by_id = {q["id"]: q for q in board["questions"]}
        assert by_id[q_open]["reply_count"] == 0        # открытый
        assert by_id[q_answered]["reply_count"] == 1    # на него ответили

        # счётчики реакций едут вместе со строкой — иначе список предложений
        # пришлось бы добивать запросом на каждую строку
        await db.set_reaction(uid, prop, "agree")
        board = await db.topic_board(root)
        assert board["proposals"][0]["agree"] == 1
        assert board["proposals"][0]["disagree"] == 0

    _run(body)


def test_board_follows_belonging_edges_not_the_scalar():
    """Довод, принесённый из другой проблемы, обязан появиться в доске той, куда
    его принесли: истина принадлежности — `node_topics`, а не скаляр."""
    async def body():
        db = await _fresh_db()
        uid = await db.add_user("boarder2", "hash", "Автор", invite_required=False)
        home = await db.add_node("Домашняя проблема", author_id=uid, kind="problem",
                                 title="Дом")
        away = await db.add_node("Другая проблема", author_id=uid, kind="problem",
                                 title="Туда")
        prop = await db.add_node("Предложение, написанное дома", author_id=uid,
                                 kind="proposal", topic_root_id=home)
        await db.add_edge(prop, home, "proposal")

        assert [p["id"] for p in (await db.topic_board(away))["proposals"]] == []
        await db.add_belonging(prop, away, author_id=uid)
        assert [p["id"] for p in (await db.topic_board(away))["proposals"]] == [prop]
        # и не пропало из домашней
        assert [p["id"] for p in (await db.topic_board(home))["proposals"]] == [prop]

    _run(body)


def test_board_route_refuses_a_non_root():
    """Доска собирается для КОРНЯ: у ответа своей доски нет."""
    from fastapi.testclient import TestClient

    async def prepare():
        db = await _fresh_db()
        uid = await db.add_user("boarder3", "hash", "Автор", invite_required=False)
        root = await db.add_node("Корень", author_id=uid, kind="problem", title="К")
        child = await db.add_node("Ответ", author_id=uid, kind="proposal",
                                  topic_root_id=root)
        await db.add_edge(child, root, "proposal")
        return root, child

    root = child = None

    async def wrapper():
        nonlocal root, child
        from app import db
        try:
            root, child = await prepare()
        finally:
            await db.close_pool()
    asyncio.run(wrapper())

    from app import db, main
    db.DATABASE_URL = TEST_DB
    with TestClient(main.app) as c:
        assert c.get(f"/api/topics/{root}/board").status_code == 200
        assert c.get(f"/api/topics/{child}/board").status_code == 400
        assert c.get("/api/topics/999999/board").status_code == 404
