"""
Разбор знает, какие узлы ветки написал сам автор, и различает «уже сказано» и
«уже возразили» (стенд 14.09).

Живой случай: Alex написал черновик про одесское двуязычие, разбор ответил
«на этот тезис уже есть возражение» и посоветовал «автору сначала прочитать
узел 38». Узел 38 не возражал — это был тот же тезис, и написал его сам Alex.
Модель подменяется заглушкой — проверяется сборка запроса.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app import pools  # noqa: E402

ALEX, VERA = 11, 20
PARENT = {"id": 37, "text": "Продавщиця не зобов'язана говорити українською."}
BRANCH = [
    {"id": 1, "depth": 0, "kind": "problem", "rel": None, "parent_id": None,
     "author_id": 16, "text": "Заборона використання російської мови"},
    {"id": 36, "depth": 1, "kind": "argument", "rel": "qualify", "parent_id": 1,
     "author_id": VERA, "text": "Продавщицу никто не обязывает говорить по-украински."},
    {"id": 38, "depth": 2, "kind": "argument", "rel": "qualify", "parent_id": 36,
     "author_id": ALEX, "text": "В Одессе до войны говорили на двух языках одновременно."},
]
SIMILAR = [{"id": 90, "kind": "argument", "topic_title": "Другая ветка",
            "author_id": ALEX, "text": "Двуязычие держится без закона."}]


def _capture(monkeypatch, answer):
    seen = {}

    def fake_complete(system, user, **kw):
        seen["system"], seen["user"] = system, user
        return json.dumps(answer)

    monkeypatch.setattr(pools.poi, "complete", fake_complete)
    return seen


def test_review_marks_the_authors_own_nodes(monkeypatch):
    seen = _capture(monkeypatch, {"actual_type": "refute", "verdict": "new"})
    pools.review_draft("В Одессе говорили на двух языках, и никто не уступал.",
                       PARENT, BRANCH, [], similar=SIMILAR, author_id=ALEX)
    user = seen["user"]
    assert "[38] (argument, qualify -> 36, YOURS)" in user
    assert "[36] (argument, qualify -> 1)" in user          # чужой узел без пометки
    assert "«Другая ветка», YOURS)" in user                  # и в похожих узлах
    assert "YOURS marks the draft author's own earlier nodes" in user


def test_review_knows_said_is_not_countered(monkeypatch):
    seen = _capture(monkeypatch, {"actual_type": "refute", "verdict": "new"})
    pools.review_draft("текст", PARENT, BRANCH, [], author_id=ALEX)
    user = seen["user"]
    assert "'said': an existing NODE already makes the SAME point" in user
    assert "Never 'countered' for a node that agrees with or repeats the draft" in user
    assert "countered|said" in user


def test_without_author_nothing_is_marked(monkeypatch):
    seen = _capture(monkeypatch, {"actual_type": "refute", "verdict": "new"})
    pools.review_draft("текст", PARENT, BRANCH, [])
    assert ", YOURS)" not in seen["user"]


def test_companion_marks_own_nodes_too(monkeypatch):
    seen = _capture(monkeypatch, {"reply": "ok", "suggestion": ""})
    pools.companion_reply("текст", [], PARENT, BRANCH, similar=SIMILAR, author_id=ALEX)
    user = seen["user"]
    assert "[38] (argument, YOURS)" in user
    assert "[36] (argument)" in user
    assert "YOURS marks the author's own earlier nodes" in user
