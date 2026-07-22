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
from datetime import datetime, timezone

from . import db, taxonomy

# Test personas. Real influence is per-topic PoI (seeded below). (name, color)
AUTHORS = [
    ("Профессор", "#b98cff"),
    ("Алекс",     "#d8af6e"),
    ("Студент",   "#5aa9e6"),
    ("Тролль",    "#e25b56"),
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


# Проблемы — единица навигации. У проблемы есть СОСТОЯНИЕ: постановка (текст
# корня), причины, масштаб (данные извне) и реестр попыток с исходами. Реестр
# копит провалы наравне с успехами — «пробовали там, не сработало, из-за чего»
# самое ценное. source_* — несущая внешняя ссылка + текстовая выдержка (блоб не
# хостим). Данные здесь ИЛЛЮСТРАТИВНЫЕ, для демонстрации формы, не справка.
PROBLEMS = [
    {
        "title": "Пытки в полиции остаются безнаказанными",
        "statement": "Насилие при задержании и в отделах массово не доходит до "
                     "приговора: жалоба уходит тем же ведомствам, что и подозреваемые.",
        "author_idx": 1,
        "domain": "society", "sub": "Права человека",
        "geo": ["Россия"], "tags": ["полиция", "безнаказанность"],
        "causes": "Расследование ведёт та же система, чьих сотрудников проверяет; "
                  "нет независимой экспертизы травм; свидетели зависят от той же "
                  "полиции; сроки давности гасят дела.",
        "scale_note": "Доля жалоб на насилие, дошедших до приговора, — единицы процентов.",
        "scale_url": "https://www.coe.int",
        "scale_excerpt": "Профильные доклады фиксируют разрыв в разы между числом "
                         "заявлений о насилии и числом осуждённых сотрудников.",
        "interventions": [
            {"what": "Независимый орган расследования жалоб на полицию (IPCC/IOPC)",
             "actor": "Государство", "geo": "Великобритания", "when_text": "2004→2018",
             "outcome": "Часть дел дошла до суда, но доля обоснованных жалоб с "
                        "санкциями осталась низкой; критика за медлительность.",
             "outcome_kind": "partial",
             "conditions": "Работает при реальной независимости бюджета и доступа к "
                           "материалам; без этого превращается в фильтр.",
             "source_url": "https://www.policeconduct.gov.uk",
             "source_excerpt": "Отдельный орган принимает и расследует жалобы на "
                               "полицию, минуя саму полицию."},
            {"what": "Нагрудные камеры на патрульных",
             "actor": "Полиция", "geo": "США", "when_text": "2014→",
             "outcome": "Снижение жалоб в части округов, но эффект исчезал там, где "
                        "запись включалась по усмотрению сотрудника.",
             "outcome_kind": "mixed",
             "conditions": "Даёт эффект только при жёстком правиле обязательной "
                           "записи и внешнем доступе к архиву.",
             "source_url": "https://bja.ojp.gov",
             "source_excerpt": "Массовые программы нательных камер; результаты по "
                               "снижению применения силы неоднородны между сайтами."},
            {"what": "Внутренняя проверка силами того же ведомства",
             "actor": "МВД", "geo": "Россия", "when_text": "постоянно",
             "outcome": "Подавляющее большинство проверок — отказ в возбуждении дела.",
             "outcome_kind": "failure",
             "conditions": "Провал воспроизводится везде, где проверяющий и "
                           "проверяемый — одна вертикаль.",
             "source_url": None, "source_excerpt": None},
        ],
    },
    {
        "title": "Уличная бездомность в крупных городах",
        "statement": "Люди годами живут на улице; ночлежки снимают симптом на ночь, "
                     "но не выводят из бездомности.",
        "author_idx": 2,
        "domain": "society", "sub": "Социальная поддержка",
        "geo": ["Финляндия", "США"], "tags": ["бездомность", "жильё"],
        "causes": "Дефицит доступного жилья; потеря жилья опережает помощь; "
                  "требование сначала «решить» зависимость/занятость как условие "
                  "жилья замыкает круг.",
        "scale_note": "Финляндия — единственная страна ЕС с устойчивым снижением "
                      "бездомности за десятилетие.",
        "scale_url": "https://ec.europa.eu",
        "scale_excerpt": "На фоне роста бездомности в большинстве стран ЕС Финляндия "
                         "показывает обратную динамику.",
        "interventions": [
            {"what": "Housing First — жильё без предварительных условий",
             "actor": "Государство + НКО", "geo": "Финляндия", "when_text": "2008→",
             "outcome": "Устойчивое сокращение длительной бездомности; высокий "
                        "процент удержавшихся в жилье.",
             "outcome_kind": "success",
             "conditions": "Опирается на реальный фонд социального жилья и "
                           "сопровождение; без запаса квартир не масштабируется.",
             "source_url": "https://ysaatio.fi",
             "source_excerpt": "Жильё предоставляется первым, без требования сперва "
                               "решить зависимость или найти работу.",
             # атрибуция (причинная претензия) и спор по ней — вся петля реестра
             "attributions": [
                 {"text": "Сработало благодаря готовому фонду соцжилья, "
                          "а не самой модели «жильё сначала».",
                  "poi": 60, "author_idx": 0,
                  "disputes": [
                      {"text": "Модель как раз и создаёт политический спрос на "
                               "фонд — без неё квартиры не выделяли бы.",
                       "edge_type": "undercut", "quote": "а не самой модели",
                       "poi": 57, "author_idx": 2},
                  ]},
             ]},
            {"what": "Криминализация ночёвки в публичных местах",
             "actor": "Муниципалитеты", "geo": "США", "when_text": "разное",
             "outcome": "Перемещает людей между районами, повышает издержки, "
                        "бездомность не снижает.",
             "outcome_kind": "failure",
             "conditions": "Провал устойчив: наказание не создаёт жилья.",
             "source_url": None, "source_excerpt": None},
        ],
    },
]


async def _seed_problems(author_ids):
    """Проблемы с реестром попыток — поверх обычных тем, аддитивно."""
    n_problems = n_interv = n_attr = 0
    for p in PROBLEMS:
        root_id = await db.add_node(
            p["statement"], author_id=author_ids[p["author_idx"]],
            kind="problem", topic_root_id=None, title=p["title"])
        await db.set_topic_facets(
            root_id, p["domain"], p.get("sub"),
            sorted(taxonomy.geo_closure(p.get("geo"))), p.get("tags", []))
        await db.set_problem(
            root_id, causes=p.get("causes"), scale_note=p.get("scale_note"),
            scale_url=p.get("scale_url"), scale_excerpt=p.get("scale_excerpt"),
            scale_retrieved_at=datetime.now(timezone.utc) if p.get("scale_url") else None,
            author_id=author_ids[p["author_idx"]])
        for iv in p["interventions"]:
            row = await db.add_intervention(
                root_id, what=iv["what"], actor=iv.get("actor"),
                geo=iv.get("geo"), when_text=iv.get("when_text"),
                outcome=iv.get("outcome"), outcome_kind=iv["outcome_kind"],
                conditions=iv.get("conditions"), source_url=iv.get("source_url"),
                source_excerpt=iv.get("source_excerpt"),
                source_retrieved_at=datetime.now(timezone.utc) if iv.get("source_url") else None,
                author_id=author_ids[p["author_idx"]])
            n_interv += 1
            # атрибуции о факте — узлы-аргументы, по ним спорят рёбрами (undercut
            # на участок): вся петля реестра «факт → причинная претензия → подрыв»
            for at in iv.get("attributions", []):
                attr_id = await db.add_node(
                    at["text"], poi_score=at.get("poi"),
                    poi_breakdown={"seed": True}, kind="attribution",
                    author_id=author_ids[at.get("author_idx", p["author_idx"])],
                    topic_root_id=root_id, intervention_id=row["id"])
                n_attr += 1
                for dsp in at.get("disputes", []):
                    dnode = await db.add_node(
                        dsp["text"], poi_score=dsp.get("poi"),
                        poi_breakdown={"seed": True}, topic_root_id=root_id,
                        author_id=author_ids[dsp.get("author_idx", p["author_idx"])])
                    q = dsp.get("quote")
                    a_s = a_e = a_hash = None
                    if q and q in at["text"]:
                        a_s = at["text"].index(q); a_e = a_s + len(q)
                        a_hash = db.text_hash(at["text"])
                    await db.add_edge(dnode, attr_id, dsp["edge_type"],
                                      anchor_hash=a_hash, anchor_start=a_s,
                                      anchor_end=a_e,
                                      anchor_quote=q if a_hash else None)
        n_problems += 1
    return n_problems, n_interv, n_attr


async def _seed():
    """Wipe and re-seed. Assumes the pool + schema are already up."""
    await db.wipe()

    author_ids = [await db.add_author(name, color) for name, color in AUTHORS]

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

    n_problems, n_interv, n_attr = await _seed_problems(author_ids)

    print(f"seeded {len(author_ids)} authors, {len(TOPICS)} topics, "
          f"{n_nodes} nodes, {n_edges} edges, "
          f"{n_problems} problems, {n_interv} interventions, "
          f"{n_attr} attributions")


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
