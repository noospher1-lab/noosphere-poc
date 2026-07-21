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
    await db.wipe(force=True)
    return db


def test_author_crud():
    async def go():
        db = await _fresh_db()
        aid = await db.add_author("Профессор", "#b98cff")
        a = await db.get_author(aid)
        assert a["name"] == "Профессор"
        assert a["color"] == "#b98cff"
        assert "reputation" not in a          # the rudiment is gone
        assert [x["id"] for x in await db.list_authors()] == [aid]
        await db.close_pool()
    _run(go())


def test_node_carries_author_into_graph():
    async def go():
        db = await _fresh_db()
        aid = await db.add_author("Тролль", "#e25b56")
        nid = await db.add_node("a claim", poi_score=80, author_id=aid)
        g = await db.get_graph()
        node = next(n for n in g["nodes"] if n["id"] == nid)
        assert node["author"] == "Тролль"
        assert node["author_color"] == "#e25b56"
        await db.close_pool()
    _run(go())


def test_node_without_author_is_anonymous():
    async def go():
        db = await _fresh_db()
        nid = await db.add_node("orphan claim", poi_score=50)
        node = next(n for n in (await db.get_graph())["nodes"] if n["id"] == nid)
        assert node["author"] is None
        assert node["author_color"] is None
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
        aid = await db.add_author("Автор", None)
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


def test_reaction_weight_is_frozen_at_cast_time():
    """The core of the path-dependency fix: a reaction counts the reactor's PoI
    FROZEN when it was cast. If the reactor's PoI later grows, votes already
    cast do NOT silently reweight — so a recipient's stored PoI no longer
    depends on WHEN it happens to be recomputed relative to others' growth."""
    async def go():
        from app import replay
        db = await _fresh_db()
        author = await db.add_author("Автор", None)
        reactor = await db.add_author("Реактор", None)
        root = await db.add_node("тезис", poi_score=60, author_id=author,
                                 topic_root_id=None)

        # reactor (default PoI 10) agrees; weight is frozen at ~10
        w = await db.topic_poi_of(reactor, root)
        await db.set_reaction(reactor, root, "agree", w)
        poi_before = await db.recompute_topic_poi(author, root)

        # the reactor becomes an expert in this very topic (PoI jumps to 90)
        await db.set_topic_prior(reactor, root, 90.0)
        assert (await db.topic_poi_of(reactor, root)) == 90.0

        # recomputing the author again must NOT move their PoI: the vote's
        # weight was frozen at cast time, not re-read live
        poi_after = await db.recompute_topic_poi(author, root)
        assert poi_after == poi_before

        # and the whole thing is still reproducible from the log alone
        problems, _ = await replay.verify()
        assert problems == []
        await db.close_pool()
    _run(go())


def test_atomize_is_idempotent():
    """Confirming atomization twice (double-click / retry) must NOT duplicate
    atoms: the second call returns the same atom ids and writes nothing."""
    async def go():
        db = await _fresh_db()
        author = await db.add_author("Автор", None)
        expl = await db.add_node("разбор темы", poi_score=70, author_id=author,
                                 kind="exploration", topic_root_id=None)
        atoms = [{"text": "тезис раз", "kind": "argument", "group": "g"},
                 {"text": "вопрос два", "kind": "question", "group": "g"}]

        first, already1 = await db.add_atoms_once(expl, expl, author, atoms)
        assert already1 is False and len(first) == 2
        assert await db.atom_children(expl) == first

        # a retry: same ids, flagged as already done, no new nodes
        second, already2 = await db.add_atoms_once(expl, expl, author, atoms)
        assert already2 is True and second == first
        assert await db.atom_children(expl) == first        # still exactly two

        # the atoms are unscored -> they never feed PoI (neither farm nor drag)
        for aid in first:
            assert (await db.get_node(aid))["poi_score"] is None
        await db.close_pool()
    _run(go())


def test_dissent_pins_argument_as_own_verbatim_position():
    """п.10: an author who rejects how their argument was composed pulls it into
    its OWN verbatim position, and a full re-cluster must never fold it back."""
    async def go():
        from app import main
        db = await _fresh_db()
        author = await db.add_author("A", None)
        # question root so the ONLY argument in the topic is the one we pin —
        # then re-cluster has no free arguments and makes no LLM call
        root = await db.add_node("вопрос-корень?", poi_score=70, author_id=author,
                                 kind="question", topic_root_id=None)
        arg = await db.add_node("мой аргумент дословно", poi_score=65,
                                author_id=author, topic_root_id=root)
        await db.add_edge(arg, root, "support")

        # simulate auto-clustering into a pool whose composed text isn't the
        # author's words, then check get_node_full surfaces where it landed
        pool = await db.add_position(root, "чужая композиция", "искажённый текст", "support")
        await db.set_node_position(arg, pool)
        assert (await db.get_node_full(arg))["position_headline"] == "чужая композиция"

        # dissent (db-level): pin + spin into a verbatim own position
        own = await db.add_position(root, "мой аргумент дословно", "мой аргумент дословно", "dissent")
        await db.set_node_position(arg, own)
        await db.mark_dissented(arg)
        assert (await db.get_node(arg))["dissented"] is True

        # a full re-cluster re-creates the pinned arg as its own verbatim position
        # (no LLM: there are no free arguments to cluster)
        await main._recompute_positions(root)
        positions = await db.list_positions(root)
        assert len(positions) == 1
        assert positions[0]["stance"] == "dissent"
        assert positions[0]["composed"] == "мой аргумент дословно"
        await db.close_pool()
    _run(go())


def test_conclusion_is_authored_and_backed_by_a_scored_node():
    """п.9: a conclusion enters the map as the author's SIGNED, scorable claim —
    a conclusion position attributed to them and backed by an argument node —
    not anonymous, unscored LLM text."""
    async def go():
        from app import main
        db = await _fresh_db()
        author = await db.add_author("Автор", None)
        root = await db.add_node("тема", poi_score=70, author_id=author,
                                 topic_root_id=None)
        src = await db.add_position(root, "исходная", "исходный текст", "support")
        m = await db.add_node("член пула", poi_score=60, author_id=author,
                              topic_root_id=root)
        await db.set_node_position(m, src)

        # emulate conclude/confirm's writes: authored argument node + signed position
        concl = await db.add_node("мой вывод вперёд", author_id=author,
                                  kind="argument", topic_root_id=root)
        new_pid = await db.add_position(root, "вывод", "мой вывод вперёд",
                                        "conclusion", author_id=author)
        await db.set_node_position(concl, new_pid)
        await db.add_position_link(new_pid, src, "conclusion")

        pos = await db.get_position(new_pid)
        assert pos["author_id"] == author                 # the position is signed
        payload = await main._position_payload(pos)
        assert payload["author"] == "Автор"               # surfaced, not anonymous
        assert concl in payload["member_ids"]             # backed by the authored node
        node = await db.get_node(concl)                    # a normal scorable argument
        assert node["author_id"] == author and node["kind"] == "argument"
        await db.close_pool()
    _run(go())
