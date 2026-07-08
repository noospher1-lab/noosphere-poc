# -*- coding: utf-8 -*-
"""
One-off backfill: re-score every existing question node with the
relevance-split rubric (root questions no longer get a relevance criterion;
reply questions get the claim they respond to as context). Run once after
deploying the rubric change in app/poi.py.

Usage: python3 -m tools.recompute_questions   (from the project root, with
DATABASE_URL and ANTHROPIC_API_KEY set — e.g. `set -a; source .env; set +a`).
"""
import asyncio
import sys

sys.path.insert(0, ".")

from app import db, poi


async def main():
    await db.init_pool()
    pool = db._pool_or_raise()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT n.id, n.text, n.author_id, n.topic_root_id, e.target_id AS parent_id,
                   p.text AS parent_text
            FROM nodes n
            LEFT JOIN edges e ON e.source_id = n.id AND e.type = 'question'
            LEFT JOIN nodes p ON p.id = e.target_id
            WHERE n.kind = 'question'
            ORDER BY n.id
            """
        )

    print(f"{len(rows)} question node(s) to rescore")
    accrue = set()
    for r in rows:
        old = await db.get_node(r["id"])
        old_score = old["poi_score"] if old else None
        score, breakdown = await asyncio.to_thread(poi.score_question, r["text"], r["parent_text"])
        await db.update_node_score(r["id"], score, breakdown)
        kind = "root" if r["parent_text"] is None else "reply"
        print(f"  #{r['id']:>4} [{kind}] {old_score} -> {score}")
        if r["author_id"] and r["topic_root_id"]:
            accrue.add((r["author_id"], r["topic_root_id"]))

    for author_id, topic_root_id in accrue:
        await db.recompute_topic_poi(author_id, topic_root_id)
    print(f"recomputed topic PoI for {len(accrue)} (author, topic) pair(s)")

    await db.close_pool()


if __name__ == "__main__":
    asyncio.run(main())
