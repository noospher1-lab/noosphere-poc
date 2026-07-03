"""
Replay: prove the event log is a sufficient source of truth.

Rebuilds state from the append-only `events` table alone and checks it against
the live tables. This is the property the scaling draft buys with principle 1:
when the vote-weight formula changes, history is REPLAYED from events instead
of migrated — so a formula change is a code change, not a data migration.

Run:  python -m app.replay
"""

import asyncio
import math

from . import db, voteweight


def rebuild_state(events):
    """Fold the event stream into state. Last-write-wins for votes/PoI,
    insert-only for nodes/edges — mirroring the tables' semantics."""
    st = {
        "nodes": {},          # id -> {kind, poi_score, author_id}
        "edges": set(),       # (source, target, type)
        "reactions": {},      # (author, node) -> stance
        "position_votes": {}, # (author, position) -> stance
        "topic_poi": {},      # (author, topic_root) -> poi
        "authors": set(),
    }
    for e in events:
        p, a = e["payload"], e["author_id"]
        t = e["type"]
        if t == "author_added":
            st["authors"].add(a)
        elif t == "node_added":
            st["nodes"][p["node_id"]] = {
                "kind": p.get("kind", "argument"),
                "poi_score": p.get("poi_score"),
                "author_id": a,
            }
        elif t == "node_scored":
            if p["node_id"] in st["nodes"]:
                st["nodes"][p["node_id"]]["poi_score"] = p["poi_score"]
        elif t == "edge_added":
            st["edges"].add((p["source_id"], p["target_id"], p["edge_type"]))
        elif t == "reaction_set":
            st["reactions"][(a, p["node_id"])] = p["stance"]
        elif t == "position_vote_set":
            st["position_votes"][(a, p["position_id"])] = p["stance"]
        elif t == "topic_poi_set":
            st["topic_poi"][(a, p["topic_root_id"])] = p["poi"]
    return st


async def read_table_state():
    """The same shape, read from the live tables."""
    pool = db._pool_or_raise()
    async with pool.acquire() as conn:
        nodes = await conn.fetch("SELECT id, kind, poi_score, author_id FROM nodes")
        edges = await conn.fetch("SELECT source_id, target_id, type FROM edges")
        reactions = await conn.fetch("SELECT author_id, node_id, stance FROM reactions")
        pvotes = await conn.fetch(
            "SELECT author_id, position_id, stance FROM position_reactions")
        tpoi = await conn.fetch("SELECT author_id, topic_root_id, poi FROM author_topic_poi")
        authors = await conn.fetch("SELECT id FROM authors")
    return {
        "nodes": {r["id"]: {"kind": r["kind"], "poi_score": r["poi_score"],
                            "author_id": r["author_id"]} for r in nodes},
        "edges": {(r["source_id"], r["target_id"], r["type"]) for r in edges},
        "reactions": {(r["author_id"], r["node_id"]): r["stance"] for r in reactions},
        "position_votes": {(r["author_id"], r["position_id"]): r["stance"] for r in pvotes},
        "topic_poi": {(r["author_id"], r["topic_root_id"]): r["poi"] for r in tpoi},
        "authors": {r["id"] for r in authors},
    }


async def verify():
    """Compare replayed state to table state; returns a list of mismatches."""
    events = []
    after = 0
    while True:
        page = await db.get_events(after, 1000)
        if not page:
            break
        events.extend(page)
        after = page[-1]["id"]
    replayed = rebuild_state(events)
    actual = await read_table_state()

    problems = []
    for key in ("nodes", "edges", "reactions", "position_votes", "topic_poi", "authors"):
        if replayed[key] != actual[key]:
            problems.append(
                f"{key}: replay {len(replayed[key])} items != tables {len(actual[key])}")
    return problems, replayed


def total_weight(state, formula=voteweight.vote_weight):
    """Aggregate vote weight under ANY formula — this is the replay payoff:
    swap `formula` and the whole history is re-scored without touching data."""
    return round(sum(
        formula(n["poi_score"]) for n in state["nodes"].values()
        if n["kind"] == "argument"), 3)


async def main():
    await db.init_pool()
    problems, replayed = await verify()
    if problems:
        print("MISMATCH — лог НЕ достаточен для восстановления состояния:")
        for p in problems:
            print("  -", p)
    else:
        print(f"OK — состояние полностью восстановимо из лога "
              f"({len(replayed['nodes'])} узлов, {len(replayed['edges'])} рёбер, "
              f"{len(replayed['reactions'])} реакций).")
        # Demo: the same history under two formulas, no migration required.
        current = total_weight(replayed)
        linear = total_weight(
            replayed, lambda poi, stake=100.0:
            0.0 if poi is None else round(stake * (poi / 100.0), 3))
        print(f"Σ вес (текущая формула sqrt(stake)·PoI/100): {current}")
        print(f"Σ вес (гипотетическая linear-stake):          {linear}")
    await db.close_pool()


if __name__ == "__main__":
    asyncio.run(main())
