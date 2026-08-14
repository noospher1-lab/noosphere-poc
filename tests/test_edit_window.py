"""
Правило часа (vault: decisions/edit-delete-window).

Опубликованный текст неизменен — правки нет вообще. Автору доступны три вещи:
снять высказывание (час, и только пока никто не ответил), отозвать его
(«больше не настаиваю» — всегда) и приписать примечание (всегда).

Как и остальные тесты против Postgres, гейтятся на TEST_DATABASE_URL.
"""

import asyncio
import os
import sys
from datetime import timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from app import auth  # noqa: E402

TEST_DB = os.environ.get("TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not TEST_DB, reason="TEST_DATABASE_URL not set")


async def _fresh_db():
    from app import db
    db.DATABASE_URL = TEST_DB
    await db.close_pool()
    await db.init_pool()
    await db.init_db()
    await db.wipe(force=True)
    return db


async def _author(db, username):
    # invite_required=False: регистрация открыта с 2026-08, а с кодом add_user
    # вернула бы строку-тег вместо id
    uid = await db.add_user(username, auth.hash_password("secret1"), username,
                            "#fff", invite_required=False)
    assert isinstance(uid, int), uid
    return uid


async def _age_node(db, node_id, minutes):
    """Состарить узел — иначе окно пришлось бы ждать по-настоящему."""
    pool = db._pool_or_raise()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE nodes SET created_at = now() - ($2 || ' minutes')::interval "
            "WHERE id = $1", node_id, str(minutes))


def _run(coro_fn):
    """Каждый тест живёт в своём event loop, поэтому пул обязан закрыться внутри
    того же цикла, в котором открылся: пережив asyncio.run, он потянет за собой
    закрытый loop и уронит следующий тест."""
    async def wrapper():
        from app import db
        try:
            await coro_fn()
        finally:
            await db.close_pool()
    asyncio.run(wrapper())


# --------------------------------------------------------------- снятие
@needs_db
def test_author_removes_own_node_inside_window():
    async def go():
        db = await _fresh_db()
        uid = await _author(db, "remover")
        root = await db.add_node("Тема о дорогах", author_id=uid, kind="problem",
                                 title="Дороги")
        nid = await db.add_node("Довод внутри темы", author_id=uid,
                                topic_root_id=root)

        state = await db.node_removability(nid, uid)
        assert state["can_remove"] is True, state
        assert state["removable_until"] is not None

        assert await db.soft_delete_node(nid, uid) is True
        # снятого узла для системы нет
        assert await db.get_node(nid) is None
        assert await db.get_node_full(nid) is None
        assert nid not in [n["id"] for n in await db.topic_nodes(root)]
        assert nid not in [n["id"] for n in (await db.get_graph())["nodes"]]
        kids = await db.get_children(root)
        assert kids["total"] == 0 and kids["children"] == []
    _run(go)


@needs_db
def test_stranger_cannot_remove():
    async def go():
        db = await _fresh_db()
        mine = await _author(db, "owner")
        other = await _author(db, "stranger")
        root = await db.add_node("Тема", author_id=mine, kind="problem", title="Т")
        nid = await db.add_node("Мой довод", author_id=mine, topic_root_id=root)

        with pytest.raises(db.Locked, match="только его автор"):
            await db.soft_delete_node(nid, other)
        assert await db.get_node(nid) is not None
    _run(go)


@needs_db
def test_window_closes_after_an_hour():
    async def go():
        db = await _fresh_db()
        uid = await _author(db, "latecomer")
        root = await db.add_node("Тема", author_id=uid, kind="problem", title="Т")
        nid = await db.add_node("Довод", author_id=uid, topic_root_id=root)
        await _age_node(db, nid, 61)

        state = await db.node_removability(nid, uid)
        assert state["can_remove"] is False
        assert "час" in state["reason"]
        with pytest.raises(db.Locked, match="прошёл час"):
            await db.soft_delete_node(nid, uid)
    _run(go)


@needs_db
def test_any_reply_closes_the_window():
    async def go():
        db = await _fresh_db()
        mine = await _author(db, "author1")
        root = await db.add_node("Тема", author_id=mine, kind="problem", title="Т")
        nid = await db.add_node("Довод", author_id=mine, topic_root_id=root)
        reply = await db.add_node("Возражение", author_id=mine, topic_root_id=root)
        await db.add_edge(reply, nid, "refute")

        # даже собственный ответ автора: продолжив ветку, он сам сделал текст опорой
        with pytest.raises(db.Locked, match="уже ответили"):
            await db.soft_delete_node(nid, mine)
    _run(go)


@needs_db
def test_foreign_reaction_closes_the_window():
    async def go():
        db = await _fresh_db()
        mine = await _author(db, "author2")
        other = await _author(db, "reactor")
        root = await db.add_node("Тема", author_id=mine, kind="problem", title="Т")
        nid = await db.add_node("Довод", author_id=mine, topic_root_id=root)

        await db.set_reaction(mine, nid, "agree", 10)      # своя не в счёт
        assert (await db.node_removability(nid, mine))["can_remove"] is True

        await db.set_reaction(other, nid, "agree", 10)
        with pytest.raises(db.Locked, match="отреагировали"):
            await db.soft_delete_node(nid, mine)
    _run(go)


@needs_db
def test_foreign_belonging_closes_the_window():
    async def go():
        db = await _fresh_db()
        mine = await _author(db, "author3")
        other = await _author(db, "curator")
        root = await db.add_node("Тема", author_id=mine, kind="problem", title="Т")
        other_root = await db.add_node("Чужая проблема", author_id=other,
                                       kind="problem", title="Ч")
        nid = await db.add_node("Довод", author_id=mine, topic_root_id=root)

        await db.add_belonging(nid, other_root, author_id=other)
        with pytest.raises(db.Locked, match="под другую проблему"):
            await db.soft_delete_node(nid, mine)
    _run(go)


@needs_db
def test_root_locked_once_the_topic_has_content():
    async def go():
        db = await _fresh_db()
        mine = await _author(db, "starter")
        other = await _author(db, "joiner")
        root = await db.add_node("Пустая тема", author_id=mine, kind="problem",
                                 title="П")
        assert (await db.node_removability(root, mine))["can_remove"] is True

        await db.add_node("Чужой довод", author_id=other, topic_root_id=root)
        with pytest.raises(db.Locked, match="уже есть доводы"):
            await db.soft_delete_node(root, mine)
    _run(go)


@needs_db
def test_removing_root_clears_it_from_workspaces():
    async def go():
        db = await _fresh_db()
        mine = await _author(db, "starter2")
        other = await _author(db, "watcher")
        root = await db.add_node("Тема на снос", author_id=mine, kind="problem",
                                 title="С")
        await db.workspace_add(other, root)
        assert root in [t["id"] for t in await db.workspace_topics(other)]

        await db.soft_delete_node(root, mine)
        assert await db.workspace_topics(other) == []
        assert root not in [t["id"] for t in await db.map_topics()]
        assert root not in [t["id"] for t in await db.list_topics()]
    _run(go)


# --------------------------------------------------------------- отзыв
@needs_db
def test_retract_works_when_removal_no_longer_does():
    async def go():
        db = await _fresh_db()
        mine = await _author(db, "retractor")
        other = await _author(db, "opponent")
        root = await db.add_node("Тема", author_id=mine, kind="problem", title="Т")
        nid = await db.add_node("Поспешный довод", author_id=mine, topic_root_id=root)
        reply = await db.add_node("Возражение", author_id=other, topic_root_id=root)
        await db.add_edge(reply, nid, "refute")
        await _age_node(db, nid, 200)                 # и час прошёл, и ответили

        with pytest.raises(db.Locked):
            await db.soft_delete_node(nid, mine)

        node = await db.retract_node(nid, mine, "передумал: механизм не работает")
        assert node["retracted_at"] is not None
        assert node["retract_note"] == "передумал: механизм не работает"

        # узел остаётся на месте вместе с ответом на него — унести чужое возражение
        # с собой нельзя
        full = await db.get_node_full(nid)
        assert full is not None and full["retracted_at"] is not None
        assert reply in [c["id"] for c in (await db.get_children(nid))["children"]]
    _run(go)


@needs_db
def test_retract_is_author_only_and_once():
    async def go():
        db = await _fresh_db()
        mine = await _author(db, "retractor2")
        other = await _author(db, "nosy")
        root = await db.add_node("Тема", author_id=mine, kind="problem", title="Т")
        nid = await db.add_node("Довод", author_id=mine, topic_root_id=root)

        with pytest.raises(db.Locked, match="только его автор"):
            await db.retract_node(nid, other)
        await db.retract_node(nid, mine)
        with pytest.raises(db.Locked, match="уже отозвано"):
            await db.retract_node(nid, mine)
    _run(go)


# --------------------------------------------------------- примечания
@needs_db
def test_addendum_available_after_the_window():
    async def go():
        db = await _fresh_db()
        mine = await _author(db, "annotator")
        other = await _author(db, "outsider")
        root = await db.add_node("Тема", author_id=mine, kind="problem", title="Т")
        nid = await db.add_node("Довод с опечаткой", author_id=mine,
                                topic_root_id=root)
        await _age_node(db, nid, 500)

        with pytest.raises(db.Locked, match="только автор"):
            await db.add_addendum(nid, other, "чужая приписка")

        first = await db.add_addendum(nid, mine, "уточняю: имелось в виду другое")
        assert first["text"].startswith("уточняю")
        await db.add_addendum(nid, mine, "и ещё одно")

        addenda = await db.node_addenda(nid)
        assert [a["text"] for a in addenda] == [
            "уточняю: имелось в виду другое", "и ещё одно"]
        # исходный текст при этом не тронут
        assert (await db.get_node(nid))["text"] == "Довод с опечаткой"
        assert (await db.get_node_full(nid))["text"] == "Довод с опечаткой"
    _run(go)


# ------------------------------------------------------------- реестр
@needs_db
def test_intervention_removal_and_retraction():
    async def go():
        db = await _fresh_db()
        mine = await _author(db, "registrar")
        other = await _author(db, "claimant")
        root = await db.add_node("Проблема", author_id=mine, kind="problem",
                                 title="П")
        iv = await db.add_intervention(root, what="Раздали субсидии",
                                       outcome_kind="failure", author_id=mine)
        ivid = iv["id"]

        # пока по записи не заявлена атрибуция — её можно снять
        assert (await db.list_interventions(root))[0]["id"] == ivid
        await db.soft_delete_intervention(ivid, mine)
        assert await db.get_intervention(ivid) is None
        assert await db.list_interventions(root) == []

        # запись с атрибуцией снять уже нельзя, но можно отозвать
        iv2 = await db.add_intervention(root, what="Ввели квоты",
                                        outcome_kind="partial", author_id=mine)
        await db.add_node("Сработало благодаря контролю", author_id=other,
                          kind="attribution", topic_root_id=root,
                          intervention_id=iv2["id"])
        with pytest.raises(db.Locked, match="атрибуция"):
            await db.soft_delete_intervention(iv2["id"], mine)
        out = await db.retract_intervention(iv2["id"], mine, "источник не подтвердился")
        assert out["retracted_at"] is not None
    _run(go)


# ------------------------------------------------------- правки не существует
def test_no_edit_path_exists():
    """Защита от регресса: правка опубликованного текста не должна вернуться
    ни в каком виде. Формулировку доводят до отправки, а не после неё."""
    from app import db
    assert not hasattr(db, "edit_node")
    assert not hasattr(db, "edit_intervention")
