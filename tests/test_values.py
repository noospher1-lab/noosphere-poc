"""
Ценности у доводов (vault: decisions/2026-09-15-values).

Выбор Alex 15.09: пункт из общего живого списка + формулировка своими словами;
ставит разбор, автор видит и может сменить (и после публикации — это метка);
в «Размежевании» видно, на какие ценности опираются группы. Модель подменяется.
"""

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from app import alignment, pools, values  # noqa: E402

TEST_DB = os.environ.get("TEST_DATABASE_URL")


def test_list_v1_is_ten_values_and_other():
    assert values.VERSION == 1
    assert len(values.VALUES) == 11 and "other" in values.VALUES
    assert values.clean("Identity") == "identity"
    assert values.clean("none") is None and values.clean("astrology") is None
    assert values.name("selfrule") == "самоуправление"
    assert values.clean_phrase("  право   говорить " + "x" * 300).startswith("право говорить x")
    assert len(values.clean_phrase("x" * 500)) == values.PHRASE_MAX


def _capture(monkeypatch, answer):
    seen = {}

    def fake(system, user, **kw):
        seen["user"] = user
        return json.dumps(answer)

    monkeypatch.setattr(pools.poi, "complete", fake)
    return seen


def test_reply_review_asks_for_value_and_phrase(monkeypatch):
    seen = _capture(monkeypatch, {"actual_type": "refute", "verdict": "new"})
    pools.review_draft("Это моя страна, язык — часть того, кто мы.",
                       {"id": 1, "text": "Язык — просто инструмент"}, [], [])
    user = seen["user"]
    assert "VALUES." in user and "- identity: language, culture" in user
    assert "VALUE: which underlying VALUE" in user
    assert '"value": "liberty|security|' in user and '"value_phrase"' in user


def test_problem_statement_gets_no_value(monkeypatch):
    seen = _capture(monkeypatch, {"actual_type": "problem", "verdict": "new"})
    pools.review_draft("Пытки в полиции остаются безнаказанными", None, [], [],
                       root_kind="problem")
    assert "VALUES." not in seen["user"] and '"value"' not in seen["user"]


def test_groups_show_the_values_they_lean_on():
    items = ["n:1", "n:2", "n:3", "n:4"]
    marks = [(p, it, "agree") for p in (1, 2, 3) for it in items] \
        + [(p, it, "disagree") for p in (11, 12, 13) for it in items]
    item_values = {"n:1": "identity", "n:2": "identity", "n:3": "security", "n:4": "liberty"}
    pub = alignment.summary(marks, item_values=item_values)["public"]
    assert pub["groups"][0]["values"] == [
        {"id": "identity", "count": 2}, {"id": "liberty", "count": 1},
        {"id": "security", "count": 1}]
    assert pub["groups"][1]["values"] == []          # ни с одним доводом не согласны
    # без ценностей форма групп прежняя
    assert alignment.summary(marks)["public"]["groups"] == [{"size": 3}, {"size": 3}]


# ---------------------------------------------------------------- с базой
@pytest.mark.skipif(not TEST_DB, reason="TEST_DATABASE_URL not set")
def test_publish_show_and_change_value():
    from tests.test_concessions import _client
    c = _client("values_bot", True)
    try:
        root = c.ids["root"]
        assert len(c.get("/api/values").json()["values"]) == 11

        r = c.post("/api/argument", json={
            "text": "Это моя страна, и язык — часть того, кто мы.",
            "connect_to": root, "edge_type": "refute",
            "value": {"id": "identity", "phrase": "  право говорить  на родном языке "}})
        assert r.status_code == 200, r.text
        nid = r.json()["id"]
        node = c.get(f"/api/nodes/{nid}").json()
        assert node["value"] == "identity" and node["value_name"] == "идентичность и традиция"
        assert node["value_phrase"] == "право говорить на родном языке"
        assert node["value_version"] == 1

        bad = c.post("/api/argument", json={"text": "x", "connect_to": root,
                                            "edge_type": "refute", "value": {"id": "astrology"}})
        assert bad.status_code == 400

        # автор меняет метку: формулировка ИИ была про прежнюю ценность — снимается
        ch = c.put(f"/api/nodes/{nid}/value", json={"id": "security"}).json()
        assert ch["value"] == "security" and ch["value_phrase"] is None
        assert c.put(f"/api/nodes/{nid}/value", json={"id": None}).json()["value"] is None
        assert c.get(f"/api/nodes/{nid}").json()["value"] is None

        async def foreign():
            from app import db
            other = await db.add_user("stranger", "h", "Чужой", invite_required=False)
            return await db.add_node("чужой довод", author_id=other, topic_root_id=root)
        fid = c.portal.call(foreign)
        assert c.put(f"/api/nodes/{fid}/value", json={"id": "care"}).status_code == 403

        graph = {n["id"]: n for n in c.get(f"/api/graph/topic/{root}").json()["nodes"]}
        assert graph[nid]["value"] is None
    finally:
        c.__exit__(None, None, None)


@pytest.mark.skipif(not TEST_DB, reason="TEST_DATABASE_URL not set")
def test_review_keeps_only_listed_values(monkeypatch):
    from app import main
    from app import pools as pools_mod
    from tests.test_concessions import _client
    c = _client("values_user", False)
    try:
        base = {"actual_type": "refute", "type_note": "", "quality_note": "",
                "verdict": "new", "node_id": None, "position_id": None, "note": "",
                "split": None, "placement": "here", "place_id": None,
                "place_note": "", "think": ""}
        monkeypatch.setattr(pools_mod, "review_draft", lambda *a, **kw: {
            **base, "value": "identity", "value_phrase": "право на родной язык"})
        out = c.post("/api/draft/review", json={
            "text": "Это моя страна", "connect_to": c.ids["root"], "edge_type": "refute"}).json()
        assert out["value"] == "identity" and out["value_name"] == "идентичность и традиция"
        assert out["value_phrase"] == "право на родной язык"

        main._REVIEW_CACHE.clear()
        monkeypatch.setattr(pools_mod, "review_draft", lambda *a, **kw: {
            **base, "value": "astrology", "value_phrase": "звёзды"})
        out = c.post("/api/draft/review", json={
            "text": "Это моя страна", "connect_to": c.ids["root"], "edge_type": "refute"}).json()
        assert out["value"] is None and out["value_phrase"] == ""
    finally:
        c.__exit__(None, None, None)
