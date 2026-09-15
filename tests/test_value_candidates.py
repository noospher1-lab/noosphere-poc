"""
Кандидаты в новые ценности (vault: decisions/2026-09-15-value-candidates).

Похожие по смыслу формулировки, которым нет дома в списке («другое» или разброс
по пунктам), набранные разными людьми в разных обсуждениях. Векторы здесь
игрушечные — проверяется сама логика, без модели.
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from app import value_candidates as vc  # noqa: E402

TEST_DB = os.environ.get("TEST_DATABASE_URL")

PRIVACY = [1.0, 0.0, 0.0]     # «цифровая приватность» — новой ценности в списке нет
IDENTITY = [0.0, 1.0, 0.0]
NOISE = [0.0, 0.0, 1.0]


def item(nid, author, topic, value, phrase):
    return {"node_id": nid, "author_id": author, "topic": topic, "value": value, "phrase": phrase}


def test_other_phrases_from_many_people_become_a_candidate():
    items = [item(1, 10, 100, "other", "право не быть отслеженным"),
             item(2, 11, 200, "other", "цифровая приватность"),
             item(3, 12, 200, "liberty", "цифровая приватность"),
             item(4, 13, 300, "identity", "родной язык")]
    vectors = {1: PRIVACY, 2: [0.95, 0.05, 0.0], 3: [0.9, 0.1, 0.0], 4: IDENTITY}
    out = vc.candidates(items, vectors)
    assert len(out) == 1
    c = out[0]
    assert c["reason"] == "other" and c["people"] == 3 and c["topics"] == 2
    assert c["phrases"][0] == "цифровая приватность"          # чаще всех
    assert c["values"] == {"other": 2, "liberty": 1}
    assert "author" not in str(c)


def test_phrases_split_across_the_list_are_a_candidate_too():
    items = [item(1, 10, 100, "liberty", "приватность"),
             item(2, 11, 200, "security", "приватность данных"),
             item(3, 12, 300, "fairness", "никто не следит")]
    vectors = {1: PRIVACY, 2: PRIVACY, 3: PRIVACY}
    out = vc.candidates(items, vectors)
    assert [c["reason"] for c in out] == ["split"]


def test_a_group_with_a_home_is_not_a_candidate():
    items = [item(i, 10 + i, 100 + i, "identity", "родной язык") for i in range(1, 5)]
    vectors = {i: IDENTITY for i in range(1, 5)}
    assert vc.candidates(items, vectors) == []


def test_one_person_or_one_discussion_is_not_enough():
    same_person = [item(i, 10, 100 + i, "other", "приватность") for i in range(1, 5)]
    one_topic = [item(i, 10 + i, 100, "other", "приватность") for i in range(1, 5)]
    vectors = {i: PRIVACY for i in range(1, 5)}
    assert vc.candidates(same_person, vectors) == []
    assert vc.candidates(one_topic, vectors) == []


def test_dissimilar_phrases_do_not_merge_and_missing_vectors_are_skipped():
    items = [item(1, 10, 100, "other", "приватность"),
             item(2, 11, 200, "other", "шум"),
             item(3, 12, 300, "other", "приватность"),
             item(4, 13, 400, "other", "без вектора")]
    vectors = {1: PRIVACY, 2: NOISE, 3: PRIVACY}
    assert vc.candidates(items, vectors) == []                 # 1 и 3 — только двое


def test_value_counts():
    items = [item(1, 10, 100, "identity", "a"), item(2, 10, 200, "identity", None),
             item(3, 11, 200, "liberty", "b")]
    assert vc.value_counts(items) == {"identity": {"nodes": 2, "people": 1},
                                      "liberty": {"nodes": 1, "people": 1}}


@pytest.mark.skipif(not TEST_DB, reason="TEST_DATABASE_URL not set")
def test_overview_counts_values_and_is_honest_without_the_model():
    from tests.test_concessions import _client
    c = _client("overview_bot", True)
    try:
        root = c.ids["root"]
        for text, v, ph in (("Это моя страна", "identity", "право на родной язык"),
                            ("Память предков", "identity", "память"),
                            ("Каждый выбирает сам", "liberty", None)):
            r = c.post("/api/argument", json={"text": text, "connect_to": root,
                                              "edge_type": "refute",
                                              "value": {"id": v, "phrase": ph}})
            assert r.status_code == 200, r.text
        out = c.get("/api/values/overview").json()
        by_id = {v["id"]: v for v in out["values"]}
        assert len(by_id) == 11 and out["version"] == 1
        assert by_id["identity"]["nodes"] == 2 and by_id["identity"]["people"] == 1
        assert by_id["liberty"]["nodes"] == 1 and by_id["security"]["nodes"] == 0
        assert out["thresholds"] == {"min_people": 3, "min_topics": 2}
        if not out["embed"]:                      # run-tests.sh выключает модель
            assert out["candidates"] == []
        assert "Вера" not in str(out)
    finally:
        c.__exit__(None, None, None)
