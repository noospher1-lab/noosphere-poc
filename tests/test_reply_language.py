"""
Язык ответа ИИ — язык автора, а не обсуждения
(vault: decisions/2026-09-14-reply-language).

Живой случай 10–11.09.2026: русский черновик под украинской проблемой получал
от разбора и компаньона ответы по-украински, и компаньон переводил на
украинский сам текст автора. Модель подменяется заглушкой: проверяется, что
язык определяет код и что правило с именем языка стоит ПОСЛЕДНИМ в запросе —
после узла, ветки и прошлых реплик на другом языке.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app import pools  # noqa: E402

# тексты из живого случая, сокращённо
RU_DRAFT = ("Полностью согласен с проблемой. Но считаю эту проблему не основной. "
            "Так как каждый раз люди голосуют на выборах против людей, которые "
            "выступают за такие методы, но по итогам выборов, те кого избрали, "
            "начинают вести абсолютно противоположную политику.")
UK_PROBLEM = ("Уряд України забороняє використання російської мови в державних "
              "та громадських установах — в письмовому та усному вигляді.")
UK_BRANCH = [{"id": 1, "depth": 0, "kind": "problem", "rel": None,
              "parent_id": None, "text": UK_PROBLEM}]


def _capture(monkeypatch, answer):
    seen = {}

    def fake_complete(system, user, **kw):
        seen["system"], seen["user"] = system, user
        return json.dumps(answer)

    monkeypatch.setattr(pools.poi, "complete", fake_complete)
    return seen


def _last_block(user):
    return user.split("\n\n---\n\n")[-1]


def test_language_is_told_apart_by_letters():
    assert pools.author_language(RU_DRAFT) == "Russian"
    assert pools.author_language(UK_PROBLEM) == "Ukrainian"
    # не на чем судить — не угадываем
    assert pools.author_language("да") is None
    assert pools.author_language("Elected officials ignore voters.") is None
    # белорусский: и «і», и «ы» — выдаёт «ў»
    assert pools.author_language("Беларуская мова мае літару ў і гукі ы.") is None


def test_short_draft_takes_language_from_authors_turns_not_companions():
    history = [
        {"role": "companion", "text": "Чи є конкретні приклади виборів?"},
        {"role": "author", "text": "закон о мове принят Радой в апреле 2019, "
                                   "за считанные дни до передачи власти"},
    ]
    assert pools.author_language("Согласен", history) == "Russian"
    # реплика компаньона на другом языке сама по себе язык не задаёт
    assert pools.author_language("Согласен", history[:1]) is None


def test_review_names_the_authors_language_last(monkeypatch):
    seen = _capture(monkeypatch, {"actual_type": "qualify", "verdict": "new"})
    pools.review_draft(RU_DRAFT, {"id": 1, "text": UK_PROBLEM}, UK_BRANCH, [],
                       in_problem=True)
    last = _last_block(seen["user"])
    assert last.startswith("LANGUAGE")
    assert "in Russian" in last
    assert "never in the language of the discussion" in seen["system"]


def test_unknown_language_still_points_at_the_draft(monkeypatch):
    seen = _capture(monkeypatch, {"actual_type": "qualify", "verdict": "new"})
    pools.review_draft("Voters are ignored between elections.",
                       {"id": 1, "text": UK_PROBLEM}, UK_BRANCH, [])
    assert "the language of the author's own draft" in _last_block(seen["user"])


def test_companion_keeps_draft_language_after_its_own_wrong_turn(monkeypatch):
    seen = _capture(monkeypatch, {"reply": "ok", "suggestion": ""})
    history = [
        {"role": "companion", "text": "Добрий приклад. Хочеш, я складу варіант?"},
        {"role": "author", "text": "да"},
    ]
    out = pools.companion_reply(RU_DRAFT, history, {"id": 1, "text": UK_PROBLEM},
                                UK_BRANCH, turns_left=3)
    assert out["reply"] == "ok"
    last = _last_block(seen["user"])
    assert last.startswith("LANGUAGE") and "in Russian" in last
    assert "Never translate the author's text" in last
    # метки реплик больше не русские — сами по себе они тянули язык
    assert "AUTHOR: да" in seen["user"] and "АВТОР" not in seen["user"]
