"""
Seed the graph so a reviewer sees something immediately. Seeding does NOT call
the LLM — it inserts placeholder PoI scores. Re-running wipes first, so it
doubles as a clean-slate reset. Run:  python -m app.seed

Async + Postgres version (see app/db.py for the why).

Model (per the design discussion):
- One TOPIC == one discussion == one branch (one root node). Topics are NOT
  mixed on one screen; the UI shows a single discussion at a time.
- An author's PoI is DOMAIN-SPECIFIC, stored per topic (author_topic_poi).
  Here we seed contrasting values so "expert here, novice there" is visible.
- An argument's weight is its OWN PoI only (author-independent).
"""

import asyncio

from . import db

# Test personas. `reputation` here is only a lossy global label; real influence
# is per-topic (seeded below). (name, reputation, color)
AUTHORS = [
    ("Профессор", 75, "#b98cff"),
    ("Алекс",     68, "#d8af6e"),
    ("Студент",   60, "#5aa9e6"),
    ("Тролль",    20, "#e25b56"),
]

# Each topic is one self-contained discussion. nodes[0] is the root (proposal);
# edges connect arguments to it. author_idx points into AUTHORS. topic_poi maps
# author_idx -> that author's PoI IN THIS topic (hybrid seed).
TOPICS = [
    {
        "title": "Меритократия управления",
        # (text, poi, topic_label, author_idx)
        "nodes": [
            ("Коллективные решения должны взвешиваться качеством рассуждения, а не числом голосов.", 80, "Качество vs число", 0),
            ("Голосование по числу голов уязвимо к искусственному большинству.", 71, "Искусственное большинство", 0),
            ("Качество рассуждения можно оценивать без единственно верного ответа.", 64, "Оценка рассуждения", 2),
            ("LLM может закодировать популярные искажения из обучающих данных.", 69, "Искажения LLM", 1),
            ("Прозрачные критерии оценки провоцируют подгонку под метрику.", 62, "Подгонка под метрику", 3),
            ("Композитный PoI (диалог + ревью + трек-рекорд) устойчивее любого одного сигнала.", 73, "Композитный PoI", 0),
        ],
        # (source_index, target_index, type)
        "edges": [
            (1, 0, "support"),
            (2, 0, "support"),
            (3, 2, "refute"),
            (4, 0, "refute"),
            (5, 0, "support"),
        ],
        "topic_poi": {0: 88, 1: 72, 2: 40, 3: 12},
    },
    {
        "title": "Право на форк и анти-захват",
        "nodes": [
            ("Право на форк — главный механизм против захвата сферы.", 74, "Право на форк", 2),
            ("Сублинейное взвешивание капитала ограничивает «деньги покупают голоса».", 66, "Сублинейный капитал", 2),
            ("Видимое меньшинство ломает иллюзию изоляции несогласных.", 77, "Видимое меньшинство", 1),
            ("Форки дробят сообщество и ликвидность — нужен порог.", 58, "Порог форка", 3),
            ("Доля казны при форке делает захват большинства экономически бессмысленным.", 70, "Экономика форка", 0),
        ],
        "edges": [
            (1, 0, "support"),
            (2, 0, "support"),
            (3, 0, "refute"),
            (4, 0, "support"),
        ],
        "topic_poi": {0: 35, 1: 65, 2: 82, 3: 18},
    },
]


async def _seed():
    """Wipe and re-seed. Assumes the pool + schema are already up."""
    await db.wipe()

    author_ids = [await db.add_author(name, rep, color) for name, rep, color in AUTHORS]

    n_nodes = n_edges = 0
    for topic in TOPICS:
        ids = []
        root_id = None
        for text, poi, label, ai in topic["nodes"]:
            nid = await db.add_node(
                text,
                poi_score=poi,
                poi_breakdown={"seed": True, "topic": label},
                author_id=author_ids[ai],
                topic_root_id=root_id,          # None for node[0] -> it IS the root
            )
            if root_id is None:
                root_id = nid
            ids.append(nid)
        for s, t, typ in topic["edges"]:
            await db.add_edge(ids[s], ids[t], typ)
        # topic_poi values seed the PRIOR (P₀) — a stand-in for the onboarding
        # dialogue; the current poi is then recomputed by the accrual formula.
        for ai, poi in topic["topic_poi"].items():
            await db.set_topic_prior(author_ids[ai], root_id, poi)
        # authors with contributions but no seeded prior accrue from default 10
        for aid in set(author_ids) - {author_ids[ai] for ai in topic["topic_poi"]}:
            await db.recompute_topic_poi(aid, root_id)
        n_nodes += len(ids)
        n_edges += len(topic["edges"])

    print(f"seeded {len(author_ids)} authors, {len(TOPICS)} topics, "
          f"{n_nodes} nodes, {n_edges} edges")


async def run_within_pool():
    """Re-seed using the already-running server's pool (does NOT close it)."""
    await db.init_db()
    await _seed()


async def run():
    """CLI entrypoint: open a pool, seed, close it."""
    await db.init_pool()
    await db.init_db()
    await _seed()
    await db.close_pool()


if __name__ == "__main__":
    asyncio.run(run())
