"""
У каждого узла свой адрес: /n/123 (vault: decisions/2026-09-16-node-permalinks).

Alex 16.09: «сделать для каждого узла свой url, чтобы в дальнейшем можно было
делиться ссылкой на конкретное место из обсуждения». Сервер отдаёт по этому
адресу то же дерево, но с мета-тегами узла: ссылка в чате должна показывать, о
чём спор, а не общее «Noosphere».
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

TEST_DB = os.environ.get("TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not TEST_DB, reason="TEST_DATABASE_URL not set")


def test_meta_text_is_one_line_and_cut():
    from app.main import _meta_text
    assert _meta_text("  два\nслова  ") == "два слова"
    assert _meta_text("а" * 300).endswith("…")
    assert len(_meta_text("а" * 300)) <= 201


@pytest.fixture
def client():
    from tests.test_concessions import _client
    c = _client("permalink_bot", True)
    yield c
    c.__exit__(None, None, None)


@needs_db
def test_root_page_carries_its_own_title_and_og_tags(client):
    root = client.ids["root"]
    html = client.get(f"/n/{root}").text
    assert "<title>Третий вариант — Noosphere</title>" in html
    assert '<meta property="og:title" content="Третий вариант" />' in html
    assert '<meta property="og:type" content="article" />' in html
    assert f'/n/{root}"' in html          # og:url — тот самый адрес
    # это по-прежнему страница дерева, а не отдельная вёрстка
    assert '<script src="/tree.js"></script>' in html


@needs_db
def test_reply_page_names_its_discussion(client):
    root = client.ids["root"]
    r = client.post("/api/argument", json={
        "text": "Скрытый отказ так не поймать: человек кивает и делает по-своему.",
        "connect_to": root, "edge_type": "refute"})
    assert r.status_code == 200, r.text
    reply = r.json()["id"]
    html = client.get(f"/n/{reply}").text
    assert "Скрытый отказ так не поймать" in html
    assert "в обсуждении «Третий вариант»" in html


@needs_db
def test_retracted_reply_is_marked_in_preview(client):
    root = client.ids["root"]
    reply = client.post("/api/argument", json={
        "text": "Довод, от которого автор потом отказался.",
        "connect_to": root, "edge_type": "refute"}).json()["id"]
    r = client.post(f"/api/nodes/{reply}/retract", json={"note": "передумал"})
    assert r.status_code == 200, r.text
    html = client.get(f"/n/{reply}").text
    assert "Довод отозван автором." in html


@needs_db
def test_unknown_and_junk_ids_still_serve_the_tree(client):
    # Номер, которого нет, и мусор в адресе не должны отдавать 404: дерево само
    # скажет «узел не найден», и человек остаётся в интерфейсе.
    for path in ("/n/99999999", "/n/abc"):
        r = client.get(path)
        assert r.status_code == 200, path
        assert "<title>Noosphere — Дерево обсуждений</title>" in r.text
