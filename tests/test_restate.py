"""
Вид ответа «пересказ» и разведённые блоки запроса разбора (vault: decisions/2026-09-15-restate).

Замер на QT30 (15.09): перефраз в 53 случаях из 100 уходил в «за» — у повтора не
было своего вида; в 2 из 300 ответов в вид связи попадало имя схемы. Здесь: новый
вид есть в запросе, в базе и в сервере; «пересказ» не усиливает и не ослабляет
довод в споре; справочники схем и ценностей — отдельным блоком под заголовком.
"""

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from app import db, dialectic, pools  # noqa: E402

TEST_DB = os.environ.get("TEST_DATABASE_URL")


def _capture(monkeypatch, answer):
    seen = {}

    def fake(system, user, **kw):
        seen["user"] = user
        return json.dumps(answer)

    monkeypatch.setattr(pools.poi, "complete", fake)
    return seen


def test_restate_is_an_edge_type():
    assert "restate" in db.EDGE_TYPES


def test_review_offers_restate_and_fences_reference_lists(monkeypatch):
    seen = _capture(monkeypatch, {"actual_type": "restate", "verdict": "new"})
    pools.review_draft("Если я правильно понял, вы говорите, что норма нужна только в госсфере.",
                       {"id": 1, "text": "Норма нужна в госсфере, а не у прилавка"}, [], [])
    user = seen["user"]
    assert "restate (пересказ:" in user
    assert "support|refute|qualify|restate|question|proposal|exploration" in user
    assert "ONLY one of these, never a scheme, value or placement name" in user
    fence = user.index("REFERENCE LISTS — used ONLY to fill the SCHEME and VALUE fields")
    assert fence < user.index("REASONING SCHEMES") < user.index("VALUES.")


def test_restate_neither_supports_nor_attacks():
    rows = [{"id": 1, "parent_id": None, "rel": None, "poi_score": 70, "retracted": False},
            {"id": 2, "parent_id": 1, "rel": "restate", "poi_score": 90, "retracted": False}]
    v = dialectic.verdict(rows, 1)
    assert v["verdict"] == "untested" and v["strength"] == pytest.approx(0.7)


@pytest.mark.skipif(not TEST_DB, reason="TEST_DATABASE_URL not set")
def test_restate_reply_is_published_and_suggested(monkeypatch):
    from app import main
    from app import pools as pools_mod
    from tests.test_concessions import _client

    pub = _client("restate_bot", True)
    try:
        root = pub.ids["root"]
        r = pub.post("/api/argument", json={
            "text": "Если я правильно понял: каждый говорит на своём, и никто не обязан переходить.",
            "connect_to": root, "edge_type": "restate"})
        assert r.status_code == 200, r.text
        kids = {k["id"]: k for k in pub.get(f"/api/nodes/{root}/children").json()["children"]}
        assert kids[r.json()["id"]]["rel"] == "restate"
    finally:
        pub.__exit__(None, None, None)

    c = _client("restate_user", False)
    try:
        base = {"type_note": "это пересказ", "quality_note": "", "verdict": "new",
                "node_id": None, "position_id": None, "note": "", "split": None,
                "placement": "here", "place_id": None, "place_note": "", "think": ""}
        monkeypatch.setattr(pools_mod, "review_draft",
                            lambda *a, **kw: {**base, "actual_type": "restate"})
        out = c.post("/api/draft/review", json={
            "text": "То есть вы говорите, что…", "connect_to": c.ids["root"],
            "edge_type": "support"}).json()
        assert out["type_ok"] is False and out["suggested_type"] == "restate"

        main._REVIEW_CACHE.clear()
        monkeypatch.setattr(pools_mod, "review_draft",
                            lambda *a, **kw: {**base, "actual_type": "cause"})   # утечка имени
        out = c.post("/api/draft/review", json={
            "text": "То есть вы говорите, что…", "connect_to": c.ids["root"],
            "edge_type": "support"}).json()
        assert out["type_ok"] is True and out["suggested_type"] is None
    finally:
        c.__exit__(None, None, None)
