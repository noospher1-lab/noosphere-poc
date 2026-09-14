"""
Разбор и компаньон видят состояние проблемы и связанные проблемы
(vault: decisions/2026-09-14-ai-reads-problem-state).

До 14.09 ИИ читал только дерево ответов: не знал, что уже пробовали и чем
кончилось, и не знал, что причина, о которой пишет автор, уже заведена
отдельной проблемой. Модель подменяется заглушкой — проверяется сборка запроса.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app import pools  # noqa: E402

PROBLEM = {
    "causes_text": "Закон принят без обсуждения с жителями",
    "gap": "Нет данных, как штрафы применяют на практике",
    "scale_note": None,
    "scale": [{"region": "Київ", "figure": "12 штрафів за рік"}],
    "outcomes": {"failure": 1, "partial": 1},
    "registry_total": 2,
    "registry": [
        {"what": "Мовний омбудсмен", "actor": "Рада", "geo": "Україна",
         "when_text": "2019", "outcome": "штрафи рідкісні", "outcome_kind": "partial",
         "conditions": None},
        {"what": "Суд оскаржив закон", "actor": None, "geo": None, "when_text": None,
         "outcome": None, "outcome_kind": "failure", "conditions": None},
    ],
    "causes": [{"id": 4, "title": "Представители не отражают волю избирателей",
                "text": "Избранные ведут политику против избирателей",
                "justification": None, "supported": 2, "disputed": 1}],
    "effects": [{"id": 9, "title": "Отток русскоязычных специалистов",
                 "text": "Люди уезжают", "justification": "Опрос 2024",
                 "supported": 0, "disputed": 0}],
}
PARENT = {"id": 1, "text": "Уряд забороняє використання російської мови"}
BRANCH = [{"id": 1, "depth": 0, "kind": "problem", "rel": None,
           "parent_id": None, "text": PARENT["text"]}]


def _capture(monkeypatch, answer):
    seen = {}

    def fake_complete(system, user, **kw):
        seen["system"], seen["user"] = system, user
        return json.dumps(answer)

    monkeypatch.setattr(pools.poi, "complete", fake_complete)
    return seen


def test_review_reads_the_card_and_the_linked_problems(monkeypatch):
    seen = _capture(monkeypatch, {"actual_type": "qualify", "verdict": "new"})
    pools.review_draft("дело не в законе, а в депутатах", PARENT, BRANCH, [],
                       in_problem=True, problem=PROBLEM)
    user = seen["user"]
    assert "STATE OF THE PROBLEM" in user
    assert "Закон принят без обсуждения с жителями" in user
    assert "Нет данных, как штрафы применяют" in user
    assert "Київ: 12 штрафів за рік" in user
    assert "2 entries (partial 1, failure 1)" in user
    assert "Мовний омбудсмен (Рада, Україна, 2019) — outcome: partial: штрафи рідкісні" in user
    assert "[4] CAUSE (produces this problem): Представители не отражают" in user
    assert "supported 2, disputed 1" in user
    assert "[9] EFFECT (produced by this problem)" in user
    assert "why linked: Опрос 2024" in user
    # модель знает, что к уже заведённой причине надо отправлять, а не заводить её снова
    assert "already listed among LINKED PROBLEMS as a CAUSE" in user
    # правило языка по-прежнему последнее
    assert user.split("\n\n---\n\n")[-1].startswith("LANGUAGE")


def test_companion_reads_them_too(monkeypatch):
    seen = _capture(monkeypatch, {"reply": "ok", "suggestion": ""})
    pools.companion_reply("дело не в законе, а в депутатах", [], PARENT, BRANCH,
                          problem=PROBLEM)
    assert "LINKED PROBLEMS" in seen["user"] and "Мовний омбудсмен" in seen["user"]
    assert "registry already records as tried" in seen["system"]


def test_no_problem_no_blocks(monkeypatch):
    seen = _capture(monkeypatch, {"actual_type": "qualify", "verdict": "new"})
    pools.review_draft("текст", PARENT, BRANCH, [])
    assert "STATE OF THE PROBLEM" not in seen["user"]
    assert "LINKED PROBLEMS —" not in seen["user"]


def test_empty_card_adds_nothing(monkeypatch):
    seen = _capture(monkeypatch, {"reply": "ok", "suggestion": ""})
    empty = {"causes_text": None, "gap": None, "scale_note": None, "scale": [],
             "outcomes": {}, "registry_total": 0, "registry": [], "causes": [], "effects": []}
    pools.companion_reply("текст", [], PARENT, BRANCH, problem=empty)
    assert "STATE OF THE PROBLEM" not in seen["user"]
    assert "LINKED PROBLEMS —" not in seen["user"]
