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

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from app import lang, pools  # noqa: E402

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


@pytest.mark.parametrize("text, expected", [
    (RU_DRAFT, "Russian"),
    (UK_PROBLEM, "Ukrainian"),
    # живой случай 14.09: русская фраза без ы/э/ё ушла в украинский
    ("предлагаю уволить всех депутатов", "Russian"),
    ("Депутаты не представляют избирателей", "Russian"),
    ("Надо менять систему, а не законы", "Russian"),
    # украинская фраза без і/ї/є — не должна стать русской
    ("Треба зробити щось краще", "Ukrainian"),
    ("нехай громадяни голосують напряму", "Ukrainian"),
    ("рішення має бути прозорим", "Ukrainian"),
    ("Згоден, але проблема не в законах", "Ukrainian"),
    ("пам'ять про це", "Ukrainian"),
    ("Elected officials should represent the voters", "English"),
])
def test_language_is_told_apart(text, expected):
    assert lang.detect_language(text) == expected


@pytest.mark.parametrize("text", [
    "ок?",                              # слишком коротко
    "Проблема",                         # слово есть в обоих языках
    "Die Wähler werden ignoriert.",     # латиница, но не английский
    "Беларуская мова мае літару ў.",    # другой кириллический язык
    "Съгласен съм с проблема.",         # болгарский
])
def test_unsure_or_other_language_is_not_named(text):
    assert lang.detect_language(text) is None


def test_short_draft_takes_language_from_authors_turns_not_companions():
    history = [
        {"role": "companion", "text": "Чи є конкретні приклади виборів?"},
        {"role": "author", "text": "закон о мове принят Радой в апреле 2019, "
                                   "за считанные дни до передачи власти"},
    ]
    assert pools.author_language("Проблема", history) == "Russian"
    # реплика компаньона на другом языке сама по себе язык не задаёт
    assert pools.author_language("Проблема", history[:1]) is None


def test_review_names_the_authors_language_last(monkeypatch):
    seen = _capture(monkeypatch, {"actual_type": "qualify", "verdict": "new"})
    pools.review_draft("предлагаю уволить всех депутатов",
                       {"id": 1, "text": UK_PROBLEM}, UK_BRANCH, [], in_problem=True)
    last = _last_block(seen["user"])
    assert last.startswith("LANGUAGE")
    assert "in Russian" in last
    assert "never in the language of the discussion" in seen["system"]


def test_unknown_language_still_points_at_the_draft(monkeypatch):
    seen = _capture(monkeypatch, {"actual_type": "qualify", "verdict": "new"})
    pools.review_draft("Die Wähler werden ignoriert.",
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


def test_language_given_by_the_server_wins(monkeypatch):
    # сервер добрал язык из прошлых текстов автора — черновик сам его не выдаёт
    seen = _capture(monkeypatch, {"actual_type": "qualify", "verdict": "new"})
    pools.review_draft("проблема в коррупции", {"id": 1, "text": UK_PROBLEM},
                       UK_BRANCH, [], lang="Russian")
    assert "in Russian" in _last_block(seen["user"])


def test_unknown_language_rule_warns_about_the_discussion(monkeypatch):
    seen = _capture(monkeypatch, {"reply": "ok", "suggestion": ""})
    pools.companion_reply("проблема в коррупции", [], {"id": 1, "text": UK_PROBLEM},
                          UK_BRANCH)
    last = _last_block(seen["user"])
    assert "Russian and Ukrainian are different languages" in last
