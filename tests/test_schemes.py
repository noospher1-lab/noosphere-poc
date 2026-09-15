"""
Схемы рассуждения Уолтона (vault: decisions/2026-09-15-argument-schemes).

Разбор называет схему, и его вопрос автору — самый важный неотвеченный
проверочный вопрос этой схемы; компаньон знает те же списки. Сервер принимает
только схему из каталога. Модель подменяется заглушкой — проверяется сборка
запроса и чистка ответа.
"""

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from app import pools, schemes  # noqa: E402

TEST_DB = os.environ.get("TEST_DATABASE_URL")


def _capture(monkeypatch, answer=None):
    seen = {}

    def fake_complete(system, user, **kw):
        seen["user"] = user
        return json.dumps(answer or {"actual_type": "refute", "verdict": "new"})

    monkeypatch.setattr(pools.poi, "complete", fake_complete)
    return seen


def test_catalog_is_small_and_every_scheme_has_questions():
    assert 6 <= len(schemes.SCHEMES) <= 8
    for sid, s in schemes.SCHEMES.items():
        assert s["name"] and s["what"] and len(s["questions"]) >= 2, sid
    assert "cause" in schemes.SCHEMES and "practical" in schemes.SCHEMES


def test_clean_accepts_only_catalog_ids():
    assert schemes.clean("Cause") == "cause"
    assert schemes.clean("none") is None
    assert schemes.clean("made_up") is None
    assert schemes.clean(None) is None
    assert schemes.name("practical") == "сделать X ради цели"
    assert schemes.name(None) == ""


def test_reply_review_uses_scheme_questions_for_think(monkeypatch):
    seen = _capture(monkeypatch)
    pools.review_draft("Штрафы выросли, потому что закон приняли.",
                       {"id": 1, "text": "Закон о языке"}, [], [])
    user = seen["user"]
    assert "REASONING SCHEMES" in user
    assert "Is there a third factor that produces both?" in user
    assert "SCHEME: first name the REASONING SCHEME" in user
    assert '"scheme": "cause|practical|' in user


def test_root_of_other_kind_also_gets_schemes(monkeypatch):
    seen = _capture(monkeypatch, {"actual_type": "proposal", "verdict": "new"})
    pools.review_draft("Давайте введём омбудсмена", None, [], [], root_kind="proposal")
    assert "REASONING SCHEMES" in seen["user"]


def test_problem_root_has_no_scheme(monkeypatch):
    seen = _capture(monkeypatch, {"actual_type": "problem", "verdict": "new"})
    pools.review_draft("Пытки в полиции остаются безнаказанными", None, [], [],
                       root_kind="problem")
    assert "REASONING SCHEMES" not in seen["user"]
    assert '"scheme"' not in seen["user"]


def test_companion_knows_the_same_schemes(monkeypatch):
    seen = _capture(monkeypatch, {"reply": "ok", "suggestion": ""})
    pools.companion_reply("текст", [], {"id": 1, "text": "родитель"}, [])
    assert "REASONING SCHEMES" in seen["user"]
    assert "unanswered critical question of the scheme" in seen["user"]


@pytest.mark.skipif(not TEST_DB, reason="TEST_DATABASE_URL not set")
def test_review_endpoint_keeps_only_known_schemes(monkeypatch):
    from app import main
    from app import pools as pools_mod
    from tests.test_concessions import _client

    c = _client("scheme_user", False)
    try:
        base = {"actual_type": "refute", "type_note": "", "quality_note": "",
                "verdict": "new", "node_id": None, "position_id": None, "note": "",
                "split": None, "placement": "here", "place_id": None,
                "place_note": "", "think": "Нет ли третьей причины?"}
        monkeypatch.setattr(pools_mod, "review_draft",
                            lambda *a, **kw: {**base, "scheme": "cause"})
        out = c.post("/api/draft/review", json={
            "text": "Штрафы — следствие закона", "connect_to": c.ids["root"],
            "edge_type": "refute"}).json()
        assert out["scheme"] == "cause" and out["scheme_name"] == "от причины к следствию"

        main._REVIEW_CACHE.clear()
        monkeypatch.setattr(pools_mod, "review_draft",
                            lambda *a, **kw: {**base, "scheme": "astrology"})
        out = c.post("/api/draft/review", json={
            "text": "Штрафы — следствие закона", "connect_to": c.ids["root"],
            "edge_type": "refute"}).json()
        assert out["scheme"] is None and out["scheme_name"] == ""
    finally:
        c.__exit__(None, None, None)
