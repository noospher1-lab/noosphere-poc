"""
Tests for the author/persona layer, now against async Postgres.

These are gated on TEST_DATABASE_URL because they wipe the database. Point it at
a THROWAWAY Postgres DB, e.g.:
    createdb noosphere_test
    TEST_DATABASE_URL=postgresql://noosphere:noosphere@localhost/noosphere_test \
        python -m pytest tests/ -q
If it isn't set, these tests skip (the pure vote-weight tests still run).
"""

import asyncio
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

TEST_DB = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DB, reason="TEST_DATABASE_URL not set")


def _run(coro):
    return asyncio.run(coro)


async def _fresh_db():
    from app import db
    db.DATABASE_URL = TEST_DB
    await db.close_pool()
    await db.init_pool()
    await db.init_db()
    await db.wipe()
    return db


def test_author_crud():
    async def go():
        db = await _fresh_db()
        aid = await db.add_author("Профессор", 90, "#b98cff")
        a = await db.get_author(aid)
        assert a["name"] == "Профессор"
        assert a["reputation"] == 90
        assert a["color"] == "#b98cff"
        assert [x["id"] for x in await db.list_authors()] == [aid]
        assert await db.update_author_reputation(aid, 42) is True
        assert (await db.get_author(aid))["reputation"] == 42
        assert await db.update_author_reputation(9999, 50) is False
        await db.close_pool()
    _run(go())


def test_node_carries_author_into_graph():
    async def go():
        db = await _fresh_db()
        aid = await db.add_author("Тролль", 15, "#e25b56")
        nid = await db.add_node("a claim", poi_score=80, author_id=aid)
        g = await db.get_graph()
        node = next(n for n in g["nodes"] if n["id"] == nid)
        assert node["author"] == "Тролль"
        assert node["reputation"] == 15
        assert node["author_color"] == "#e25b56"
        await db.close_pool()
    _run(go())


def test_node_without_author_has_null_reputation():
    async def go():
        db = await _fresh_db()
        nid = await db.add_node("orphan claim", poi_score=50)
        node = next(n for n in (await db.get_graph())["nodes"] if n["id"] == nid)
        assert node["author"] is None
        assert node["reputation"] is None
        await db.close_pool()
    _run(go())


def test_children_ranked_and_paginated():
    async def go():
        db = await _fresh_db()
        root = await db.add_node("root", poi_score=70)
        # children with deliberately shuffled PoI (incl. an unscored one)
        ids = {}
        for label, poi in (("mid", 50), ("top", 90), ("unscored", None), ("low", 10)):
            nid = await db.add_node(label, poi_score=poi)
            await db.add_edge(nid, root, "support")
            ids[label] = nid
        page = await db.get_children(root, limit=10, offset=0)
        assert page["total"] == 4
        # ranked by PoI desc, NULL (unscored) last
        assert [c["text"] for c in page["children"]] == ["top", "mid", "low", "unscored"]
        # pagination slices the same ranking
        p1 = await db.get_children(root, limit=2, offset=0)
        p2 = await db.get_children(root, limit=2, offset=2)
        assert [c["text"] for c in p1["children"]] == ["top", "mid"]
        assert [c["text"] for c in p2["children"]] == ["low", "unscored"]
        await db.close_pool()
    _run(go())


def test_event_log_and_replay():
    async def go():
        from app import replay
        db = await _fresh_db()
        aid = await db.add_author("Автор", 50, None)
        root = await db.add_node("корень", poi_score=70, author_id=aid)
        child = await db.add_node("ответ", author_id=aid)          # unscored
        await db.add_edge(child, root, "refute")
        await db.set_reaction(aid, root, "agree")
        await db.set_reaction(aid, root, "disagree")               # changed mind
        await db.update_node_score(child, 55.0, {"clarity": 55})   # bg scoring

        events = await db.get_events(0, 100)
        types = [e["type"] for e in events]
        # the log keeps BOTH votes; state tables keep only the latest
        assert types.count("reaction_set") == 2
        assert "node_added" in types and "edge_added" in types and "node_scored" in types

        problems, replayed = await replay.verify()
        assert problems == []                                       # log is sufficient
        assert replayed["reactions"][(aid, root)] == "disagree"     # last write wins
        assert replayed["nodes"][child]["poi_score"] == 55.0
        await db.close_pool()
    _run(go())


def test_topics_lists_roots_only():
    async def go():
        db = await _fresh_db()
        root = await db.add_node("root claim", poi_score=70)
        reply = await db.add_node("a reply", poi_score=60)
        await db.add_edge(reply, root, "support")   # reply -> root
        topics = await db.list_topics()
        ids = [t["id"] for t in topics]
        assert root in ids
        assert reply not in ids       # a reply is not a top-level topic
        await db.close_pool()
    _run(go())


def test_topic_root_of_walks_to_root():
    async def go():
        db = await _fresh_db()
        root = await db.add_node("root", poi_score=70)
        mid = await db.add_node("mid", poi_score=60)
        leaf = await db.add_node("leaf", poi_score=50)
        await db.add_edge(mid, root, "support")
        await db.add_edge(leaf, mid, "refute")
        assert await db.topic_root_of(leaf) == root
        assert await db.topic_root_of(mid) == root
        assert await db.topic_root_of(root) == root   # a root is its own root
        await db.close_pool()
    _run(go())
