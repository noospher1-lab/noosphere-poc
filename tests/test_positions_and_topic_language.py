"""
Позиции и язык темы — две находки прогона агентов 14.09.

1. Пересборка позиций падала по таймауту уже на 15 доводах: один вызов модели
   и группировал доводы, и писал полный текст каждой позиции. Теперь группировка
   короткая, а сведение — отдельный вызов на группу, параллельно.
2. Тема узла (подпись в дереве и на 3D-графе) бралась на языке цитаты или
   родителя: русские доводы с украинской цитатой получали украинские подписи.

Модель подменяется заглушкой — проверяется сборка запросов и обвязка.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app import poi, pools  # noqa: E402

RU_QUOTING = ("Принимаю поправку: выборы — не референдум по пунктам. Но как было "
              "сказано выше, «пів сторінки з цитуванням статті» — это и есть ответ "
              "органа, который ничего не объясняет и которого никто не проверяет.")
UK_PARENT = "Уряд України забороняє використання російської мови в державних установах."
SCORES = {"clarity": 50, "depth": 50, "counterargument": 50, "evidence": 50,
          "awareness_of_limits": 50, "relevance": 50, "incisiveness": 50,
          "generativity": 50, "informativeness": 50, "accuracy": 50,
          "topic": "тема", "comment": "c"}


def _capture_scoring(monkeypatch):
    seen = []

    def fake_complete(system, user, **kw):
        seen.append(user)
        return json.dumps(SCORES)

    monkeypatch.setattr(poi, "complete", fake_complete)
    return seen


# ------------------------------------------------------------- язык темы
def test_argument_topic_language_follows_the_text_not_the_quote(monkeypatch):
    seen = _capture_scoring(monkeypatch)
    poi.score_argument(RU_QUOTING)
    assert seen[-1].rstrip().endswith("the claim it replies to.")
    assert "write 'topic' in Russian" in seen[-1]


def test_reply_scorers_name_the_texts_language_not_the_parents(monkeypatch):
    seen = _capture_scoring(monkeypatch)
    poi.score_detail(RU_QUOTING, UK_PARENT)
    assert "write 'topic' in Russian" in seen[-1]
    poi.score_question("Кто и как будет проверять качество ответа органа?", UK_PARENT)
    assert "write 'topic' in Russian" in seen[-1]


def test_every_scorer_carries_the_rule(monkeypatch):
    seen = _capture_scoring(monkeypatch)
    poi.score_proposal("Треба зробити так, щоб кожна скарга мала публічну відповідь.")
    assert "write 'topic' in Ukrainian" in seen[-1]
    poi.score_exploration(RU_QUOTING)
    assert "write 'topic' in Russian" in seen[-1]
    poi.score_detail("Die Wähler werden ignoriert.")
    assert "the language of the evaluated text itself" in seen[-1]


# ---------------------------------------------------------------- позиции
def _fake_pool_model(monkeypatch, groups, compose=None, fail_compose=False):
    calls = []

    def fake_complete(system, user, max_tokens=1024, timeout=90, temperature=None):
        calls.append({"user": user, "timeout": timeout})
        sink = poi.current_usage.get()
        if sink is not None:
            sink.append(timeout)          # «трата» — видна ли она из потока
        if "Group them into POOLS" in user:
            return json.dumps({"pools": groups})
        if fail_compose:
            raise TimeoutError("read operation timed out")
        return json.dumps(compose or {"headline": "h", "composed": "СВЕДЕНО"})

    monkeypatch.setattr(pools.poi, "complete", fake_complete)
    return calls


ARGS = [{"id": 1, "text": "первый"}, {"id": 2, "text": "второй"}, {"id": 3, "text": "одиночка"}]
GROUPS = [{"headline": "Штрафы не меняют привычку", "stance": "oppose", "member_ids": [1, 2]},
          {"headline": "Отдельный довод", "stance": "mixed", "member_ids": [3]}]


def test_grouping_is_short_and_each_pool_is_composed_separately(monkeypatch):
    calls = _fake_pool_model(monkeypatch, GROUPS)
    sink = []
    token = poi.current_usage.set(sink)
    try:
        out = pools.cluster_arguments(ARGS)
    finally:
        poi.current_usage.reset(token)

    grouping = calls[0]["user"]
    assert "Do NOT write the merged position text" in grouping
    assert "'composed'" not in grouping
    assert out[0]["member_ids"] == [1, 2] and out[0]["composed"] == "СВЕДЕНО"
    assert out[0]["headline"] == "Штрафы не меняют привычку"
    # группа из одного довода не сводится моделью — это сам довод
    assert out[1]["composed"] == "одиночка"
    assert len(calls) == 2
    assert all(c["timeout"] >= pools.POOL_TIMEOUT for c in calls)
    # трата сведения из потока дошла до счётчика автора
    assert len(sink) == 2


def test_failed_compose_keeps_the_pool(monkeypatch):
    _fake_pool_model(monkeypatch, GROUPS, fail_compose=True)
    out = pools.cluster_arguments(ARGS)
    assert out[0]["composed"] == "первый\n\nвторой"
    assert out[1]["composed"] == "одиночка"


def test_first_argument_opens_the_first_position(monkeypatch):
    seen = {}

    def fake_complete(system, user, **kw):
        seen["user"] = user
        return json.dumps({"position_id": None, "headline": "Первая",
                           "composed": "текст", "stance": "mixed"})

    monkeypatch.setattr(pools.poi, "complete", fake_complete)
    out = pools.assign_argument("первый довод в теме", [])
    assert "none yet" in seen["user"]
    assert out["position_id"] is None and out["headline"] == "Первая"
