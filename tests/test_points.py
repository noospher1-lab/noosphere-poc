"""
Баллы за участие (vault: decisions/2026-09-21-participation-points).

Балл — след работы, не оценка мысли и не вес голоса. Отсюда три вещи, которые
тест обязан держать: начисление идёт из лога событий и повтор прогона ничего
не задваивает; дневной потолок делает количество невыгодным; снятое своё
высказывание забирает баллы с собой.

Часть идёт без базы (чистые правила), часть — только при TEST_DATABASE_URL.
"""

import asyncio
import os
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from app import points  # noqa: E402

TEST_DB = os.environ.get("TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not TEST_DB, reason="TEST_DATABASE_URL not set")


# ---------------------------------------------------------------- без базы
def test_root_is_a_problem_reply_is_an_argument_question_is_cheaper():
    root = points.classify({"type": "node_added", "author_id": 1, "payload":
                            {"node_id": 7, "topic_root_id": 7}})
    reply = points.classify({"type": "node_added", "author_id": 1, "payload":
                             {"node_id": 8, "topic_root_id": 7}})
    question = points.classify({"type": "node_added", "author_id": 1, "payload":
                                {"node_id": 9, "topic_root_id": 7,
                                 "kind": "question"}})
    assert root[0] == "problem" and reply[0] == "argument"
    assert question[1] < reply[1]          # вопрос дешевле довода, как в PoI


def test_atoms_of_a_review_earn_nothing():
    """Нарезку одного текста делает ИИ — платить за куски значило бы за длину."""
    assert points.classify({"type": "node_added", "author_id": 1, "payload":
                            {"node_id": 5, "topic_root_id": 1,
                             "atom_group": "риски"}}) is None


def test_unknown_events_earn_nothing():
    assert points.classify({"type": "password_changed", "author_id": 1,
                            "payload": {}}) is None


def test_streak_counts_only_days_in_a_row():
    days = [date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 2),
            date(2026, 9, 3), date(2026, 9, 9)]
    assert points.max_streak(days) == 3
    assert points.max_streak([]) == 0


def test_closed_sections_drop_their_unearned_milestones():
    """Стенд без тренажёра и голосований не должен показывать их вехи.

    Но уже взятую веху закрытие раздела не отнимает: человек её заслужил.
    """
    stats = {"arguments": 1, "questions": 0, "problems": 0, "votes": 0,
             "concessions": 0, "problem_links": 0, "interventions": 0,
             "trainer": 1, "streak": 1, "replies_received": 0,
             "problem_replies": 0}
    view = points.milestone_view(stats, ["trainer_done", "first_argument"],
                                 hidden=("trainer", "votes"))
    keys = [m["key"] for m in view]
    assert "first_vote" not in keys          # закрыт и не взят — не показываем
    assert "trainer_done" in keys            # закрыт, но взят — остаётся
    assert "first_argument" in keys


def test_milestones_are_a_route_not_a_pile():
    stats = {"arguments": 1, "questions": 0, "problems": 0, "votes": 0,
             "concessions": 0, "problem_links": 0, "interventions": 0,
             "trainer": 0, "streak": 1, "replies_received": 0,
             "problem_replies": 0}
    got = points.earned(stats)
    assert "first_argument" in got and "trainer_done" not in got
    view = points.milestone_view(stats, got)
    assert [m["done"] for m in view if m["key"] == "first_argument"] == [True]
    # невзятые вехи остаются видимыми — это и есть подсказка, что делать дальше
    assert any(not m["done"] for m in view)


# ---------------------------------------------------------------- с базой
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
    await db.points_rebuild()      # журнал начинается пустым вместе с базой
    return db


@needs_db
def test_points_come_from_the_event_log_and_never_double():
    async def body():
        db = await _fresh_db()
        uid = await db.add_user("tester", "hash", "Тестер", invite_required=False)
        root = await db.add_node("Язык вытесняется", author_id=uid,
                                 kind="problem", title="Язык вытесняется")
        reply = await db.add_node("Не вытесняется, а меняется", author_id=uid,
                                  kind="argument", topic_root_id=root)
        await db.add_edge(reply, root, "refute")

        await db.points_sync()
        first = await db.points_total(uid)
        assert first == (points.ACTIONS["problem"][0]
                         + points.ACTIONS["argument"][0]
                         + points.MILESTONE_POINTS["first_argument"]
                         + points.MILESTONE_POINTS["first_problem"])

        # второй прогон по тому же логу не добавляет ничего
        await db.points_sync()
        assert await db.points_total(uid) == first

        # и пересборка с нуля даёт то же число — правила переигрываемы
        await db.points_rebuild()
        assert await db.points_total(uid) == first
    _run(body)


@needs_db
def test_daily_cap_makes_volume_pointless():
    async def body():
        db = await _fresh_db()
        uid = await db.add_user("spammer", "hash", "Много", invite_required=False)
        root = await db.add_node("Корень", author_id=uid, kind="problem",
                                 title="Корень")
        cap = points.ACTIONS["argument"][1]
        for i in range(cap + 3):
            nid = await db.add_node(f"довод {i}", author_id=uid,
                                    kind="argument", topic_root_id=root)
            await db.add_edge(nid, root, "support")
        await db.points_sync()

        pool = db._pool_or_raise()
        async with pool.acquire() as conn:
            paid = await conn.fetchval(
                "SELECT count(*) FROM points_ledger "
                "WHERE author_id = $1 AND kind = 'argument'", uid)
        assert paid == cap          # лишние доводы не оплачены
    _run(body)


@needs_db
def test_retracting_your_own_text_takes_the_points_with_it():
    async def body():
        db = await _fresh_db()
        uid = await db.add_user("quick", "hash", "Быстрый", invite_required=False)
        root = await db.add_node("Корень", author_id=uid, kind="problem",
                                 title="Корень")
        nid = await db.add_node("Сказал и передумал", author_id=uid,
                                kind="argument", topic_root_id=root)
        await db.add_edge(nid, root, "support")
        await db.points_sync()
        before = await db.points_total(uid)

        await db.retract_node(nid, uid, note="передумал")
        await db.points_sync()
        after = await db.points_total(uid)
        assert after == before - points.ACTIONS["argument"][0]
    _run(body)


@needs_db
def test_seeded_personas_and_the_service_account_stay_out_of_the_rating():
    async def body():
        db = await _fresh_db()
        # посевная персона: без логина, войти нельзя — значит и баллов нет
        persona = await db.add_author("Посев", color="#888")
        root = await db.add_node("Посевная проблема", author_id=persona,
                                 kind="problem", title="Посевная проблема")
        assert root
        await db.points_sync()
        assert await db.points_total(persona) == 0
        assert [t for t in await db.points_top() if t["id"] == persona] == []
    _run(body)
