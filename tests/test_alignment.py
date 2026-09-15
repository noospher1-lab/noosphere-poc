"""
«С кем ты совпадаешь» и размежевание обсуждения (vault: decisions/2026-09-15-alignment-view).

Фракция — похожий набор согласий, считается, а не членство. Себе — с именами,
всем — без: размеры групп и доводы, по которым они расходятся. Группы меньше
трёх человек не показываются. Чистые функции без базы; с TEST_DATABASE_URL —
эндпоинт, где проверяется, что имена и id людей не утекают в общую картину.
"""

import json
import os
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from app import alignment  # noqa: E402

TEST_DB = os.environ.get("TEST_DATABASE_URL")
ITEMS = ["n:1", "n:2", "n:3", "n:4"]


def camp(pid, stance):
    return [(pid, it, stance) for it in ITEMS]


def two_camps():
    marks = []
    for pid in (1, 2, 3):
        marks += camp(pid, "agree")
    for pid in (11, 12, 13):
        marks += camp(pid, "disagree")
    # одиночка: половина за, половина против — ни к кому не близок
    marks += [(20, "n:1", "agree"), (20, "n:2", "agree"),
              (20, "n:3", "disagree"), (20, "n:4", "disagree")]
    return marks


def test_neighbours_need_enough_common_marks():
    marks = camp(1, "agree") + camp(2, "agree") + camp(3, "disagree") \
        + [(4, "n:1", "agree"), (4, "n:2", "agree")]          # всего две общих
    me = alignment.neighbours(1, marks)
    assert me["marks"] == 4
    assert [p["person_id"] for p in me["closest"]] == [2]
    assert me["closest"][0]["same"] == 4 and me["closest"][0]["share"] == 1.0
    assert [p["person_id"] for p in me["farthest"]] == [3]
    assert me["farthest"][0]["differ_items"] == ITEMS
    assert 4 not in [p["person_id"] for p in me["closest"] + me["farthest"]]


def test_two_camps_and_a_loner():
    view = alignment.summary(two_camps(), me=12)
    pub = view["public"]
    assert pub["people"] == 7
    assert pub["groups"] == [{"size": 3}, {"size": 3}]
    assert pub["unplaced"] == 1
    assert [d["item"] for d in pub["dividing"]] == ITEMS
    assert all(d["spread"] == 1.0 for d in pub["dividing"])
    assert view["my_group"] == 1                              # 12 во второй группе


def test_public_view_carries_no_people():
    pub = alignment.summary(two_camps())["public"]
    text = json.dumps(pub)
    assert "person" not in text and "members" not in text
    assert set(pub) == {"people", "groups", "unplaced", "dividing"}


def test_small_groups_are_hidden():
    marks = camp(1, "agree") + camp(2, "agree") \
        + camp(11, "disagree") + camp(12, "disagree") + camp(13, "disagree")
    pub = alignment.summary(marks)["public"]
    assert pub["groups"] == [{"size": 3}]                     # двоих не видно
    assert pub["unplaced"] == 2
    assert pub["dividing"] == []                              # не с кем сравнивать


def test_few_marks_are_left_out():
    marks = two_camps() + [(30, "n:1", "agree")]
    assert alignment.summary(marks)["public"]["people"] == 7


def test_order_of_marks_does_not_change_the_picture():
    marks = two_camps()
    shuffled = marks[:]
    random.Random(7).shuffle(shuffled)
    assert alignment.summary(marks) == alignment.summary(shuffled)


# ---------------------------------------------------------------- с базой
@pytest.mark.skipif(not TEST_DB, reason="TEST_DATABASE_URL not set")
def test_endpoint_gives_names_only_about_yourself():
    from tests.test_concessions import _client
    c = _client("align_user", False)
    try:
        root, me = c.ids["root"], c.ids["author"]

        async def seed():
            from app import db
            kids = []
            for i in range(3):
                nid = await db.add_node(f"довод {i}", author_id=me, topic_root_id=root)
                await db.add_edge(nid, root, "support")
                kids.append(nid)
            items = [root] + kids
            same, other = [me], []
            for name in ("Бук", "Вяз"):
                same.append(await db.add_user(name.lower(), "h", name, invite_required=False))
            for name in ("Граб", "Дуб", "Ель"):
                other.append(await db.add_user(name.lower(), "h", name, invite_required=False))
            for pid in same:
                for nid in items:
                    await db.set_reaction(pid, nid, "agree")
            for pid in other:
                for nid in items:
                    await db.set_reaction(pid, nid, "disagree")
        c.portal.call(seed)

        out = c.get(f"/api/topics/{root}/alignment").json()
        assert out["people"] == 6 and [g["size"] for g in out["groups"]] == [3, 3]
        assert len(out["dividing"]) == 4 and out["dividing"][0]["kind"] == "node"
        assert out["me"]["group"] is not None
        assert [p["name"] for p in out["me"]["closest"]] == ["Бук", "Вяз"]
        assert {p["name"] for p in out["me"]["farthest"]} == {"Граб", "Дуб", "Ель"}
        assert out["me"]["farthest"][0]["differ_items"][0]["label"]

        public = {k: v for k, v in out.items() if k != "me"}
        text = json.dumps(public, ensure_ascii=False)
        for name in ("Бук", "Вяз", "Граб", "Дуб", "Ель", "Вера"):
            assert name not in text

        c.cookies.clear()
        guest = c.get(f"/api/topics/{root}/alignment").json()
        assert "me" not in guest and guest["groups"] == out["groups"]

        kid = out["dividing"][1]["id"]
        assert c.get(f"/api/topics/{kid}/alignment").status_code == 404   # не корень
    finally:
        c.__exit__(None, None, None)
