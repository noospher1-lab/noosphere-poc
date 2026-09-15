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

from . import db, poiformula, voteweight


def rebuild_state(events):
    """Fold the event stream into state. Last-write-wins for votes/PoI,
    insert-only for nodes/edges — mirroring the tables' semantics."""
    st = {
        "nodes": {},          # id -> {kind, poi_score, author_id}
        "edges": set(),       # (source, target, type)
        "concessions": set(), # (node, target, quote)
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
                "atom_group": p.get("atom_group"),
            }
        elif t == "node_scored":
            if p["node_id"] in st["nodes"]:
                st["nodes"][p["node_id"]]["poi_score"] = p["poi_score"]
        elif t == "edge_added":
            st["edges"].add((p["source_id"], p["target_id"], p["edge_type"]))
        elif t == "concession_added":
            st["concessions"].add((p["node_id"], p["target_id"], p.get("anchor_quote")))
        elif t == "reaction_set":
            st["reactions"][(a, p["node_id"])] = p["stance"]
        elif t == "position_vote_set":
            st["position_votes"][(a, p["position_id"])] = p["stance"]
        elif t == "topic_poi_set":
            st["topic_poi"][(a, p["topic_root_id"])] = p["poi"]
    return st


def recompute_topic_poi_from_log(events):
    """
    Recompute EVERY author's topic PoI from first principles over the event log
    alone — prior + scored contributions + FROZEN reaction weights — using the
    same pure formula the app uses. Returns {(author, topic_root): poi}.

    This is the real proof the path-dependency is gone: the result depends only
    on what is in the log, never on the ORDER in which authors were recomputed.
    It is possible only because reaction_set now carries the reactor's frozen
    weight; with live reactor PoI this recomputation would be circular.
    """
    nodes = {}                 # id -> {author, kind, topic, poi_score}
    dialogue_poi = {}          # author -> P₀ from the onboarding dialogue
    prior = {}                 # (author, topic) -> explicit per-topic P₀
    reactions = {}             # (reactor, node) -> (stance, frozen_weight); LWW
    for e in events:
        p, a, t = e["payload"], e["author_id"], e["type"]
        if t == "node_added":
            nodes[p["node_id"]] = {"author": a, "kind": p.get("kind", "argument"),
                                   "topic": p.get("topic_root_id") or p["node_id"],
                                   "poi_score": p.get("poi_score")}
        elif t == "node_scored" and p["node_id"] in nodes:
            nodes[p["node_id"]]["poi_score"] = p["poi_score"]
        elif t == "dialogue_poi_set":
            dialogue_poi[a] = p["dialogue_poi"]
        elif t == "topic_prior_set":
            prior[(a, p["topic_root_id"])] = p["prior"]
        elif t == "reaction_set":
            reactions[(a, p["node_id"])] = (p["stance"], p.get("reactor_weight"))

    # which (author, topic) pairs have any state at all
    pairs = {(n["author"], n["topic"]) for n in nodes.values() if n["author"]}
    pairs |= set(prior)
    for (reactor, node_id), _ in reactions.items():
        n = nodes.get(node_id)
        if n and n["author"]:
            pairs.add((n["author"], n["topic"]))

    out = {}
    for (author, topic) in pairs:
        # prior precedence mirrors the SQL: explicit per-topic > dialogue > None
        p0 = prior.get((author, topic), dialogue_poi.get(author))
        contribs = [(n["poi_score"], n["kind"]) for n in nodes.values()
                    if n["author"] == author and n["topic"] == topic]
        reacts = [(w, stance) for (reactor, node_id), (stance, w) in reactions.items()
                  if reactor != author
                  and (n := nodes.get(node_id)) is not None
                  and n["author"] == author and n["topic"] == topic]
        out[(author, topic)] = poiformula.topic_poi(p0, contribs, reacts)
    return out


async def read_table_state():
    """The same shape, read from the live tables."""
    pool = db._pool_or_raise()
    async with pool.acquire() as conn:
        nodes = await conn.fetch(
            "SELECT id, kind, poi_score, author_id, atom_group FROM nodes")
        edges = await conn.fetch("SELECT source_id, target_id, type FROM edges")
        concessions = await conn.fetch(
            "SELECT node_id, target_id, anchor_quote FROM concessions")
        reactions = await conn.fetch("SELECT author_id, node_id, stance FROM reactions")
        pvotes = await conn.fetch(
            "SELECT author_id, position_id, stance FROM position_reactions")
        tpoi = await conn.fetch("SELECT author_id, topic_root_id, poi FROM author_topic_poi")
        authors = await conn.fetch("SELECT id FROM authors")
    return {
        "nodes": {r["id"]: {"kind": r["kind"], "poi_score": r["poi_score"],
                            "author_id": r["author_id"],
                            "atom_group": r["atom_group"]} for r in nodes},
        "edges": {(r["source_id"], r["target_id"], r["type"]) for r in edges},
        "concessions": {(r["node_id"], r["target_id"], r["anchor_quote"])
                        for r in concessions},
        "reactions": {(r["author_id"], r["node_id"]): r["stance"] for r in reactions},
        "position_votes": {(r["author_id"], r["position_id"]): r["stance"] for r in pvotes},
        "topic_poi": {(r["author_id"], r["topic_root_id"]): r["poi"] for r in tpoi},
        "authors": {r["id"] for r in authors},
    }


async def _all_events():
    """Read the whole append-only log in order."""
    events, after = [], 0
    while True:
        page = await db.get_events(after, 1000)
        if not page:
            break
        events.extend(page)
        after = page[-1]["id"]
    return events


async def verify():
    """Compare replayed state to table state; returns a list of mismatches."""
    events = await _all_events()
    replayed = rebuild_state(events)
    actual = await read_table_state()

    problems = []
    for key in ("nodes", "edges", "concessions", "reactions", "position_votes",
                "topic_poi", "authors"):
        if replayed[key] != actual[key]:
            problems.append(
                f"{key}: replay {len(replayed[key])} items != tables {len(actual[key])}")

    # Stronger than the echo check above: recompute topic PoI from the formula
    # over the log and confirm it reproduces the stored table. If this holds,
    # the stored value is a pure function of the log — order-independent.
    recomputed = recompute_topic_poi_from_log(events)
    for key, stored in actual["topic_poi"].items():
        rv = recomputed.get(key)
        if rv is None or abs(rv - float(stored)) > 0.05:
            problems.append(
                f"topic_poi {key}: recomputed {rv} != stored {stored} "
                f"(formula not reproducible from log)")
    return problems, replayed


def total_weight(state, formula=voteweight.vote_weight):
    """Aggregate vote weight under ANY formula — this is the replay payoff:
    swap `formula` and the whole history is re-scored without touching data.
    Same predicate as the live endpoint: only taken positions (arguments,
    non-atoms) carry weight."""
    return round(sum(
        formula(n["poi_score"]) for n in state["nodes"].values()
        if voteweight.carries_vote_weight(n)), 3)


async def main():
    await db.init_pool()
    problems, replayed = await verify()
    if problems:
        print("MISMATCH — лог НЕ достаточен для восстановления состояния:")
        for p in problems:
            print("  -", p)
    else:
        recomputed = recompute_topic_poi_from_log(await _all_events())
        print(f"OK — состояние полностью восстановимо из лога "
              f"({len(replayed['nodes'])} узлов, {len(replayed['edges'])} рёбер, "
              f"{len(replayed['reactions'])} реакций).")
        print(f"     топик-PoI пересчитан из формулы по логу и совпал со стором "
              f"({len(recomputed)} пар автор×тема) — путезависимости нет.")
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
