"""
История мнений у довода (vault: decisions/2026-09-15-opinion-history).

Alex 15.09 (размежевание): при смене «за/против» — необязательная строка
«почему»; история смен видна у довода и ничего не начисляет. Таблица реакций
хранит только последнюю отметку — история читается из лога.

Идут только при TEST_DATABASE_URL — стирают базу.
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

TEST_DB = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DB, reason="TEST_DATABASE_URL not set")


@pytest.fixture
def client():
    from tests.test_concessions import _client
    c = _client("opinion_user", False)
    yield c
    c.__exit__(None, None, None)


def _react(c, node, stance, why=None):
    body = {"node_id": node, "stance": stance}
    if why is not None:
        body["why"] = why
    r = c.post("/api/reactions", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _state(c, node):
    r = c.get(f"/api/reactions/{node}?topic={node}")
    assert r.status_code == 200, r.text
    return r.json()


def test_first_mark_and_repeat_are_not_changes(client):
    node = client.ids["root"]
    assert _state(client, node)["mine"] is None
    first = _react(client, node, "agree", why="это не смена, а первая отметка")
    assert first["prev"] is None and first["changed"] is False
    again = _react(client, node, "agree")
    assert again["changed"] is False
    s = _state(client, node)
    assert s["mine"] == "agree" and s["changes"] == []


def test_change_with_and_without_why_goes_to_history(client):
    node = client.ids["root"]
    _react(client, node, "agree")
    long_why = "после   #43 передумал:\n" + "очень " * 80
    r = _react(client, node, "disagree", why=long_why)
    assert r["prev"] == "agree" and r["changed"] is True
    _react(client, node, "agree")                     # обратно, без объяснения

    s = _state(client, node)
    assert s["mine"] == "agree"
    assert [(c["from"], c["to"]) for c in s["changes"]] == [
        ("agree", "disagree"), ("disagree", "agree")]
    why = s["changes"][0]["why"]
    assert why.startswith("после #43 передумал: очень")    # пробелы схлопнуты
    assert len(why) <= 280
    assert s["changes"][1]["why"] is None
    assert s["changes"][0]["author"] == "Вера"
    assert s["agree"]["count"] == 1 and s["disagree"]["count"] == 0


def test_guest_sees_history_but_has_no_side(client):
    node = client.ids["root"]
    _react(client, node, "agree")
    _react(client, node, "disagree", why="убедили")
    client.cookies.clear()
    s = _state(client, node)
    assert s["mine"] is None
    assert len(s["changes"]) == 1 and s["changes"][0]["why"] == "убедили"


def test_log_still_replays_after_changes(client):
    node = client.ids["root"]
    _react(client, node, "agree")
    _react(client, node, "disagree", why="убедили")

    async def check():
        from app import replay
        problems, _ = await replay.verify()
        assert not [p for p in problems if p.startswith("reactions")], problems
    client.portal.call(check)
