"""
Одно название узла на всех экранах (vault: decisions/2026-09-15-one-label).

Alex 15.09 (скриншоты): «в графе коротко отображается о чем речь, в древе сразу
начинается текст… слова или названия должны быть одинаковыми». Граф, строка
дерева и заголовок панели берут название из одной функции graphview.node_label.
"""

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from app import graphview  # noqa: E402

TEST_DB = os.environ.get("TEST_DATABASE_URL")


def test_label_reads_breakdown_stored_as_text():
    n = {"id": 5, "text": "Длинный текст довода о языке в школах",
         "poi_breakdown": json.dumps({"topic": "язык в школах"})}
    assert graphview.node_label(n) == "язык в школах"


def test_label_falls_back_to_text_and_root_title():
    assert graphview.node_label({"id": 5, "text": "Коротко", "poi_breakdown": None}) == "Коротко"
    assert graphview.node_label({"id": 5, "text": "x", "poi_breakdown": "не json"}) == "x"
    assert graphview.node_label({"id": 1, "text": "постановка", "title": "Запрет"},
                                is_root=True) == "Запрет"


@pytest.mark.skipif(not TEST_DB, reason="TEST_DATABASE_URL not set")
def test_tree_panel_and_graph_show_the_same_name():
    from tests.test_concessions import _client
    c = _client("label_bot", True)
    try:
        root = c.ids["root"]
        r = c.post("/api/argument", json={
            "text": "Межа проходить не між примусом і м'якістю, а між тим, що буде звичкою",
            "connect_to": root, "edge_type": "qualify"})
        assert r.status_code == 200, r.text
        reply = r.json()["id"]

        async def score():
            from app import db
            await db.update_node_score(reply, 73.1, {"topic": "мовне середовище vs стимул"})
        c.portal.call(score)

        tree = {k["id"]: k for k in c.get(f"/api/nodes/{root}/children").json()["children"]}
        panel = c.get(f"/api/nodes/{reply}").json()
        graph = {n["id"]: n for n in c.get(f"/api/graph/topic/{root}").json()["nodes"]}
        assert tree[reply]["label"] == panel["label"] == graph[reply]["label"] \
            == "мовне середовище vs стимул"
        assert "poi_breakdown" not in tree[reply]
        # корень: заголовок один и тот же
        assert c.get(f"/api/nodes/{root}").json()["label"] == graph[root]["label"] == "Третий вариант"
    finally:
        c.__exit__(None, None, None)
