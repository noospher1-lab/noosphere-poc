"""
Сила довода в споре (vault: decisions/2026-09-15-dialectic-strength).

Главный случай, ради которого это сделано: у связи проблем «оспорено · 2»
стояло и тогда, когда оба возражения уже отбиты. Отвеченное возражение должно
бить слабее неотвеченного, поддержка — уравновешивать, отозванный ответ — не
считаться вовсе. Счёт без модели; с TEST_DATABASE_URL — ещё и чтение из базы.
"""

import asyncio
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from app import dialectic  # noqa: E402

TEST_DB = os.environ.get("TEST_DATABASE_URL")


def n(id_, parent=None, rel=None, poi=None, retracted=False):
    return {"id": id_, "parent_id": parent, "rel": rel, "poi_score": poi,
            "retracted": retracted}


def test_nobody_argued_means_untested():
    v = dialectic.verdict([n(1, poi=70)], 1)
    assert v["verdict"] == "untested"
    assert v["strength"] == v["base"] == 0.7


def test_unanswered_objection_weakens_and_is_listed():
    v = dialectic.verdict([n(1, poi=70), n(2, 1, "refute", 60)], 1)
    assert v["strength"] < v["base"]
    assert v["verdict"] in ("weakened", "shaken")
    assert v["unanswered"] == [2]


def test_answered_objection_hits_much_softer():
    open_ = dialectic.verdict([n(1, poi=70), n(2, 1, "refute", 60)], 1)
    answered = dialectic.verdict(
        [n(1, poi=70), n(2, 1, "refute", 60), n(3, 2, "refute", 80)], 1)
    assert answered["strength"] > open_["strength"]
    assert answered["verdict"] == "holds"
    assert answered["unanswered"] == []


def test_support_balances_an_objection():
    v = dialectic.verdict(
        [n(1, poi=70), n(2, 1, "refute", 60), n(3, 1, "support", 60)], 1)
    assert v["strength"] == pytest.approx(0.7)
    assert v["verdict"] == "holds"


def test_neutral_replies_and_retracted_ones_do_not_count():
    v = dialectic.verdict(
        [n(1, poi=70), n(2, 1, "qualify", 90), n(3, 1, "question", 90),
         n(4, 1, "refute", 90, retracted=True)], 1)
    assert v["verdict"] == "untested"
    assert v["strength"] == pytest.approx(0.7)


def test_any_reply_by_someone_else_answers_an_objection():
    # на стенде на возражения отвечают уточнением — это тоже ответ
    q = dialectic.verdict([n(1, poi=70) | {"author_id": 1},
                           n(2, 1, "refute", 60) | {"author_id": 2},
                           n(3, 2, "qualify", 60) | {"author_id": 1}], 1)
    assert q["unanswered"] == []
    # а сила — только из «за» и «против»: уточнение возражение не ослабило
    plain = dialectic.verdict([n(1, poi=70), n(2, 1, "refute", 60)], 1)
    assert q["strength"] == plain["strength"]


def test_own_follow_up_is_not_an_answer():
    v = dialectic.verdict([n(1, poi=70) | {"author_id": 1},
                           n(2, 1, "refute", 60) | {"author_id": 2},
                           n(3, 2, "qualify", 60) | {"author_id": 2},
                           n(4, 2, "question", 60, retracted=True) | {"author_id": 1}], 1)
    assert v["unanswered"] == [2]


def test_undercut_is_an_attack():
    v = dialectic.verdict([n(1, poi=70), n(2, 1, "undercut", 60)], 1)
    assert v["attacks"] == 1 and v["unanswered"] == [2]


def test_many_weak_objections_add_up():
    one = dialectic.verdict([n(1, poi=70), n(2, 1, "refute", 20)], 1)
    five = dialectic.verdict(
        [n(1, poi=70)] + [n(i, 1, "refute", 20) for i in range(2, 7)], 1)
    assert five["strength"] < one["strength"]


def test_unscored_nodes_start_neutral():
    assert dialectic.base_score({"poi_score": None}) == 0.5


def test_deep_chain_is_counted_bottom_up():
    rows = [n(1, poi=70)] + [n(i, i - 1, "refute", 60) for i in range(2, 45)]
    v = dialectic.verdict(rows, 1)          # глубокая цепочка не роняет обход
    assert 0.0 <= v["strength"] <= 1.0


def test_unknown_node_gives_none():
    assert dialectic.verdict([n(1)], 99) is None


# ---------------------------------------------------------------- с базой
@pytest.mark.skipif(not TEST_DB, reason="TEST_DATABASE_URL not set")
def test_link_status_follows_the_justification_not_the_raw_count():
    async def body():
        from app import db
        db.DATABASE_URL = TEST_DB
        await db.close_pool()
        await db.init_pool()
        try:
            await db.init_db()
            await db.wipe(force=True)
            uid = await db.add_user("dx", "hash", "Автор", invite_required=False)
            a = await db.add_node("Запрет языка", author_id=uid, kind="problem",
                                  title="Запрет языка")
            b = await db.add_node("Представители не представляют", author_id=uid,
                                  kind="problem", title="Представители")
            just = await db.add_node("Проблема в представителях", author_id=uid,
                                     topic_root_id=a)
            await db.add_edge(just, a, "support")
            await db.add_problem_link(b, a, node_id=just, author_id=uid)

            critic = await db.add_user("dx2", "hash", "Критик", invite_required=False)
            obj = await db.add_node("Нет, дело в законе", author_id=critic, topic_root_id=a)
            await db.add_edge(obj, just, "refute")
            links = await db.problem_links_of(a)
            dx = links["causes"][0]["dialectic"]
            assert dx["unanswered"] == [obj] and dx["verdict"] != "holds"
            assert links["causes"][0]["disputed"] == 1

            ans = await db.add_node("Закон принят этими же представителями",
                                    author_id=uid, topic_root_id=a)
            await db.add_edge(ans, obj, "refute")
            links = await db.problem_links_of(a)
            dx = links["causes"][0]["dialectic"]
            assert dx["unanswered"] == [] and dx["verdict"] == "holds"
            assert links["causes"][0]["disputed"] == 1      # сырой счёт остался для ИИ

            rows = await db.dialectic_rows(just)
            assert {r["id"] for r in rows} == {just, obj, ans}
        finally:
            await db.close_pool()
    asyncio.run(body())
