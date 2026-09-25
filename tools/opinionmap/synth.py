"""
Синтетика для карты мнений (vault: decisions/2026-09-25-opinion-map, фаза 2).

    python -m tools.opinionmap.synth            # ~10 000 аккаунтов, 6 недель
    python -m tools.opinionmap.synth --people 800 --days 21   # быстрый вариант

Создаёт в ОТДЕЛЬНОЙ базе (по умолчанию noosphere_opmap в тестовом кластере
на порту 5433, см. run-tests.sh) одно обсуждение «Низовая коррупция в
госзакупках» с 12 позициями и историю за шесть недель: приход людей,
знакомство с главными возражениями и ответы на них, переходы с причиной и без,
уходы «без позиции», вопросы с «у меня тот же вопрос» и принятыми ответами,
отмены, одно слияние, один раскол, одна признанная ошибка.

Всё пишется через те же функции, что и маршруты (opinion_db), по порядку
времени, — так синтетика проверяет и сами журналы, и счётчики. ИИ не
вызывается, тексты шаблонные. Генерация детерминирована по --seed.

Рабочую базу и прод не трогает: отказывается работать с базой, в имени
которой нет «opmap».
"""

import argparse
import asyncio
import os
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import asyncpg  # noqa: E402

from app import db, opinion_db  # noqa: E402

DEFAULT_URL = "postgresql://noosphere@127.0.0.1:5433/noosphere_opmap"

TOPIC = ("Низовая коррупция в госзакупках",
         "Мелкие чиновники в госзакупках договариваются с поставщиками заранее: "
         "закупки выигрывают «свои», бюджет переплачивает, честные участники уходят.")

# (заголовок, популярность, доводы позиции)
POSITIONS = [
    ("Все закупки — в открытом доступе в реальном времени", 9,
     ["Когда каждую закупку видно сразу, договорённость заранее становится заметной.",
      "Штрафы работают, только когда нарушение видно: сначала открытость, потом санкции.",
      "Открытые данные читают журналисты и конкуренты — это тысячи бесплатных контролёров."]),
    ("Открыто, но с задержкой 90 дней", 5,
     ["Задержка защищает цены поставщиков, но через квартал всё равно видно, кто выиграл.",
      "За 90 дней договор не успевают исполнить — нарушение ещё можно остановить."]),
    ("Независимый аудитор для каждой закупки", 6,
     ["Профессиональный аудитор заметит сговор, который не увидит случайный читатель.",
      "Без независимого аудитора открытые данные читают только сами заинтересованные."]),
    ("Штрафы и уголовная ответственность для чиновников", 7,
     ["Пока риск меньше выгоды, сговор выгоден — нужно поднять цену нарушения.",
      "Уголовная статья для заказчика работает сильнее любого регламента."]),
    ("Закупки только через автоматический аукцион", 4,
     ["Людей в цепочке надо убрать, а не надзирать за ними.",
      "Алгоритм не берёт откатов: критерии заданы заранее и одинаковы для всех."]),
    ("Ротация закупщиков каждые полгода", 3,
     ["Сговор держится на личных связях — ротация их рвёт.",
      "Новый человек не знает «своих» поставщиков и не должен им ничего."]),
    ("Вознаграждение информаторам", 3,
     ["Сговор всегда знает кто-то внутри — дайте ему повод говорить.",
      "Процент от возвращённых денег окупает программу с первого дела."]),
    ("Проблема не в закупках, а в зарплатах чиновников", 3,
     ["Закупщик с зарплатой ниже рынка ищет доход на стороне.",
      "Честная зарплата дешевле, чем переплата по каждому контракту."]),
    ("Общественные наблюдатели на комиссиях", 2,
     ["Местные жители лучше всех знают, кто чей родственник.",
      "Наблюдатель с правом голоса на комиссии ломает договорённость в зародыше."]),
    ("Сократить сами закупки — меньше денег, меньше соблазна", 2,
     ["Часть закупок не нужна вовсе — их отмена убирает и коррупцию.",
      "Бюджет, который не тратится, нельзя украсть."]),
    ("Ничего не менять: издержки контроля выше ущерба", 1,
     ["Каждая новая проверка удорожает закупку для всех, включая честных.",
      "Мелкая коррупция — плата за скорость, которую иначе не получить."]),
    ("Публичный рейтинг заказчиков по переплатам", 2,
     ["Сравнение цен с рынком показывает сговор без всяких расследований.",
      "Рейтинг бьёт по репутации руководителя — это сильнее штрафа."]),
]

# возражения: (по какой позиции бьёт, откуда оно — куда уводит, тип, текст, «сила»)
OBJECTIONS = [
    (0, 1, "refute", "Публикация в реальном времени раскрывает цены поставщиков конкурентам — "
                     "честные участники уходят с торгов, остаются те, кто договаривается заранее.", 0.30),
    (0, 2, "refute", "Без независимого аудитора открытые данные читают только сами заинтересованные.", 0.18),
    (0, 3, "undercut", "Открытость без санкций ничего не меняет: нарушение видно, но наказания нет.", 0.12),
    (1, 0, "refute", "За 90 дней деньги успевают уйти — задержка защищает не цены, а сговор.", 0.22),
    (1, 2, "undercut", "Задержка ничего не даёт, если данные всё равно никто не проверяет.", 0.10),
    (2, 0, "refute", "Аудитор не успеет за объёмом — нужен доступ для всех.", 0.25),
    (2, 3, "refute", "Аудитора покупают так же, как закупщика.", 0.15),
    (3, 0, "refute", "Штрафы работают, только когда нарушение видно: сначала открытость, потом санкции.", 0.35),
    (3, 7, "undercut", "Суровое наказание без поимки не пугает — пугает вероятность попасться.", 0.12),
    (4, 2, "refute", "Аукцион по цене выигрывают демпингом, а потом срывают контракт.", 0.20),
    (4, 0, "undercut", "Критерии аукциона тоже пишет чиновник — сговор просто уходит в ТЗ.", 0.18),
    (5, 3, "refute", "Ротация ломает экспертизу: новичок закупает хуже, а сговор ищет его заново.", 0.20),
    (6, 3, "refute", "Вознаграждение плодит ложные доносы на конкурентов.", 0.15),
    (7, 3, "refute", "Высокая зарплата не останавливает того, кому предлагают в десять раз больше.", 0.25),
    (8, 0, "refute", "Наблюдателей назначают те же люди, за которыми надо наблюдать.", 0.20),
    (9, 4, "undercut", "Отмена закупок бьёт по больницам и школам, а не по коррупционерам.", 0.20),
    (10, 11, "refute", "Ущерб от переплат в разы больше стоимости открытой публикации.", 0.40),
    (11, 0, "refute", "Рейтинг без открытых данных строится на данных самих заказчиков.", 0.20),
]

QUESTIONS = [
    (0, "Если всё открыто, но никто не читает — кто заметит нарушение?",
     ["Журналисты и конкуренты: у проигравшего поставщика прямой интерес.",
      "Автоматические проверки на аномальные цены, их можно запустить сразу."]),
    (0, "Не раскроет ли публикация в реальном времени коммерческую тайну поставщиков?",
     ["Публикуется цена контракта, а не себестоимость — тайна не раскрывается.",
      "Раскроет, поэтому нужна задержка на критичные позиции."]),
    (2, "Кто платит аудитору и как он остаётся независимым?",
     ["Аудитор назначается жребием из реестра и оплачивается из общего фонда."]),
    (3, "Сколько дел о сговоре в закупках доходит до суда сейчас?",
     ["Единицы: доказать сговор без открытых данных почти невозможно."]),
    (4, "Как аукцион учтёт качество, а не только цену?",
     ["Через допуск по опыту и штрафы за срыв, заданные заранее."]),
    (7, "Какая зарплата считается «честной» для закупщика?",
     ["Медиана рынка для аналогичной должности в частном секторе."]),
]


def _utc(d):
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)


async def ensure_database(url):
    """Создать отдельную базу для синтетики, если её нет, и ускорить запись."""
    name = url.rsplit("/", 1)[1].split("?")[0]
    if "opmap" not in name:
        raise SystemExit(f"отказ: база «{name}» — не синтетическая (нужно «opmap» в имени)")
    admin = await asyncpg.connect(url.rsplit("/", 1)[0] + "/postgres")
    try:
        if not await admin.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", name):
            await admin.execute(f'CREATE DATABASE "{name}" OWNER noosphere')
        # синтетике не нужна надёжность записи на диск — нужна скорость
        await admin.execute(f'ALTER DATABASE "{name}" SET synchronous_commit = off')
    finally:
        await admin.close()


async def build(url, people, days, seed, verbose=True):
    rnd = random.Random(seed)
    t0 = time.monotonic()
    await ensure_database(url)
    db.DATABASE_URL = url
    await db.close_pool()
    await db.init_pool()
    await db.init_db()
    await db.wipe(force=True)

    def log(msg):
        if verbose:
            print(f"[{time.monotonic() - t0:6.1f}s] {msg}", flush=True)

    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=days)

    # ---- люди: массовая вставка, без событий (это не действия, а фон)
    pool = db._pool_or_raise()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "INSERT INTO authors (name, color) "
            "SELECT 'участник ' || g, '#888' FROM generate_series(1, $1) g RETURNING id",
            people + 30)
    ids = [r["id"] for r in rows]
    writers, users = ids[:30], ids[30:]
    log(f"аккаунтов: {len(users)} (+30 авторов доводов)")

    # ---- граф: обсуждение, позиции, доводы, возражения, вопросы
    root = await db.add_node(TOPIC[1], author_id=writers[0], kind="problem", title=TOPIC[0])
    pids, member_nodes = [], []
    for i, (head, _w, args) in enumerate(POSITIONS):
        pid = await db.add_position(root, head, " ".join(args), "mixed")
        mem = []
        for j, text in enumerate(args):
            nid = await db.add_node(text, author_id=writers[(i + j) % 30], topic_root_id=root,
                                    poi_score=float(55 + (i * 7 + j * 11) % 35))
            await db.add_edge(nid, root, "support")
            await db.set_node_position(nid, pid)
            mem.append(nid)
        pids.append(pid)
        member_nodes.append(mem)
    objections = []   # (node_id, target_pos, dest_pos, strength)
    for k, (tgt, dest, etype, text, strength) in enumerate(OBJECTIONS):
        nid = await db.add_node(text, author_id=writers[(k * 3) % 30], topic_root_id=root,
                                poi_score=float(60 + (k * 13) % 30))
        await db.add_edge(nid, member_nodes[tgt][0], etype)
        objections.append((nid, tgt, dest, strength))
    questions = []    # (qid, [answer ids])
    for k, (tgt, text, answers) in enumerate(QUESTIONS):
        qid = await db.add_node(text, author_id=users[k], kind="question", topic_root_id=root)
        await db.add_edge(qid, member_nodes[tgt][0], "question")
        aids = []
        for a, atext in enumerate(answers):
            aid = await db.add_node(atext, author_id=writers[(k + a + 5) % 30], topic_root_id=root)
            await db.add_edge(aid, qid, "support")
            aids.append(aid)
        questions.append((qid, aids, tgt))
    # Узлы и позиции — не журналы: дату появления ставим задним числом, иначе
    # экран говорил бы «существует с сегодня» и «вопрос открыт 0 дней».
    # Вопросы появляются по ходу обсуждения, раз в несколько дней.
    async with pool.acquire() as conn:
        t0_ = _utc(start)
        await conn.execute("UPDATE nodes SET created_at = $1 WHERE topic_root_id = $2", t0_, root)
        await conn.execute("UPDATE positions SET created_at = $1 WHERE topic_root_id = $2", t0_, root)
        for k, (qid, aids, _t) in enumerate(questions):
            asked = t0_ + timedelta(days=k * days // (len(questions) + 1))
            await conn.execute("UPDATE nodes SET created_at = $1 WHERE id = ANY($2::int[])",
                               asked, [qid] + aids)
    log(f"граф: {len(pids)} позиций, {len(objections)} возражений, {len(questions)} вопросов")

    by_target = {}
    for o in objections:
        by_target.setdefault(o[1], []).append(o)
    weights = [w for _h, w, _a in POSITIONS]

    # ---- история по дням
    where = {}                      # user → pid | None (текущее, для генератора)
    last_cause = {}                 # user → (node, from) — для отмен
    arrivals = sorted(rnd.random() for _ in users)
    # прирост ускоряется к концу: доля пришедших к дню d ~ (d/days)^1.6
    arrive_day = {u: min(days - 1, int(days * (a ** (1 / 1.6)))) for u, a in zip(users, arrivals)}
    merged_src, merged_dst = pids[9], pids[4]      # «сократить закупки» → в «аукцион»
    split_pid = pids[3]                            # «штрафы» раскалывается
    split_parts = None
    ack_node = member_nodes[0][2]                  # «тысячи бесплатных контролёров» — признана ошибкой
    counts = {"moves": 0, "exposures": 0, "joins": 0, "accepts": 0, "undo": 0}
    open_pids = list(pids)

    def moment(d):
        return _utc(start + timedelta(days=d)) + timedelta(seconds=rnd.randrange(86400))

    async def move(u, to, cause, at):
        try:
            await opinion_db.set_stance(u, root, to, cause_node_id=cause, at=at)
            where[u] = to
            counts["moves"] += 1
        except opinion_db.OpinionError:
            pass

    for d in range(days):
        day_events = []
        # новые люди выбирают позицию (часть — без причины, часть приходит «по доводу»)
        for u in users:
            if arrive_day[u] == d:
                day_events.append(("arrive", u))
        # уже пришедшие: знакомятся с возражениями, иногда сами переходят
        active = [u for u in users if arrive_day[u] < d]
        for u in rnd.sample(active, k=min(len(active), max(1, len(active) // 9))):
            day_events.append(("look", u))
        for u in rnd.sample(active, k=min(len(active), len(active) // 120)):
            day_events.append(("drift", u))
        for u in rnd.sample(active, k=min(len(active), len(active) // 60)):
            day_events.append(("ask", u))
        stamps = sorted(moment(d) for _ in day_events)
        rnd.shuffle(day_events)
        for (kind, u), at in zip(day_events, stamps):
            cur = where.get(u)
            if kind == "arrive":
                cand = [p for p in open_pids]
                w = [weights[pids.index(p)] for p in cand]
                await move(u, rnd.choices(cand, weights=w)[0], None, at)
            elif kind == "look" and cur is not None:
                pos_i = pids.index(cur)
                obs = by_target.get(pos_i, [])
                if not obs:
                    continue
                node, _t, dest, strength = rnd.choice(obs)
                r = rnd.random()
                if r < 0.35:
                    resp = "shown"
                elif r < 0.35 + strength:
                    resp = "convinced"
                elif r < 0.35 + strength + 0.15:
                    resp = "partial"
                else:
                    resp = "not_convinced"
                await opinion_db.record_exposure(u, node, resp, at=at)
                counts["exposures"] += 1
                if resp == "convinced":
                    target = pids[dest]
                    if target not in open_pids:
                        target = merged_dst if target == merged_src else rnd.choice(open_pids)
                    await move(u, target, node, at + timedelta(seconds=30))
                    last_cause[u] = node
                    # каждый 25-й передумал обратно и нажал «отменить»
                    if rnd.random() < 0.04:
                        await opinion_db.undo_response(u, node, at=at + timedelta(seconds=90))
                        counts["undo"] += 1
                        where[u] = cur
            elif kind == "drift":
                r = rnd.random()
                if r < 0.25:
                    await move(u, None, None, at)            # ушёл без позиции
                elif d > 25 and cur in (pids[0],) and rnd.random() < 0.5:
                    await move(u, pids[2], ack_node, at)     # после признанной ошибки
                else:
                    await move(u, rnd.choice(open_pids), None, at)
            elif kind == "ask":
                qid, aids, _tgt = rnd.choice(questions)
                try:
                    j = await opinion_db.join_node(u, qid, at=at)
                    counts["joins"] += int(j["new"])
                except opinion_db.OpinionError:
                    continue
                if rnd.random() < 0.55:
                    ans = aids[0] if (len(aids) == 1 or rnd.random() < 0.7) else aids[1]
                    await opinion_db.accept_answer(u, qid, ans, at=at + timedelta(seconds=60))
                    counts["accepts"] += 1

        # события позиций
        if d == days // 2:
            await opinion_db.merge_positions(merged_src, merged_dst, at=_utc(start + timedelta(days=d + 1)) - timedelta(milliseconds=1))
            open_pids.remove(merged_src)
            for u, p in list(where.items()):
                if p == merged_src:
                    where[u] = merged_dst
            log(f"день {d}: позиция #{merged_src} слита в #{merged_dst}")
        if d == int(days * 0.6):
            m = member_nodes[3]
            res = await opinion_db.split_position(split_pid, [
                {"headline": "Штрафы для чиновников, без уголовных статей", "node_ids": [m[0]]},
                {"headline": "Уголовная ответственность за сговор", "node_ids": [m[1]]},
            ], at=_utc(start + timedelta(days=d + 1)) - timedelta(milliseconds=1))
            split_parts = res["parts"]
            async with pool.acquire() as conn:
                await conn.execute("UPDATE positions SET created_at = $1 WHERE id = ANY($2::int[])",
                                   _utc(start + timedelta(days=d + 1)), split_parts)
            open_pids.remove(split_pid)
            open_pids += split_parts
            pids += split_parts
            weights += [2, 2]
            log(f"день {d}: позиция #{split_pid} расколота на {split_parts}, "
                f"не уточнили {res['unclarified']}")
        if split_parts and d > int(days * 0.6):
            # уточняют позицию постепенно: треть оставшихся в день
            left = [u for u, p in where.items() if p == split_pid]
            for u in left[: max(1, len(left) // 3)] if d < days - 2 else left[: len(left) // 2]:
                await move(u, rnd.choice(split_parts), None, moment(d))
        if d == int(days * 0.6) - 1:
            await db.add_addendum(ack_node, writers[2 % 30],
                                  "Контролёров оказалось не тысячи: данные читают единицы.",
                                  kind="error_ack")
            async with pool.acquire() as conn:
                # примечание — не журнал, дату можно выставить задним числом
                await conn.execute(
                    "UPDATE node_addenda SET created_at = $2 WHERE node_id = $1 "
                    "AND kind = 'error_ack'", ack_node, _utc(start + timedelta(days=d, hours=12)))
            log(f"день {d}: автор признал ошибку в узле #{ack_node}")
        if d % 7 == 6 or d == days - 1:
            log(f"день {d + 1}/{days}: {counts}")

    n_days = await opinion_db.ensure_daily(root, today=today)
    log(f"дневных снимков: {n_days}")
    async with pool.acquire() as conn:
        summary = await conn.fetch(
            "SELECT p.id, p.headline, p.status, s.in_now, s.stood, s.converted "
            "FROM positions p LEFT JOIN position_stats s ON s.position_id = p.id "
            "WHERE p.topic_root_id = $1 ORDER BY s.in_now DESC NULLS LAST", root)
        n_log = await conn.fetchval("SELECT count(*) FROM stance_log")
        n_exp = await conn.fetchval("SELECT count(*) FROM exposures")
    await db.close_pool()
    if verbose:
        print(f"\nобсуждение #{root}: переходов {n_log}, ответов на возражения {n_exp}")
        print(f"{'#':>4}  {'статус':8} {'сейчас':>7} {'устояли':>8} {'переуб.':>8}  позиция")
        for r in summary:
            print(f"{r['id']:>4}  {r['status']:8} {r['in_now'] or 0:>7} {r['stood'] or 0:>8} "
                  f"{r['converted'] or 0:>8}  {r['headline']}")
    return {"root": root, "counts": counts}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--people", type=int, default=10000)
    ap.add_argument("--days", type=int, default=42)
    ap.add_argument("--seed", type=int, default=20260925)
    ap.add_argument("--url", default=os.environ.get("OPMAP_DATABASE_URL", DEFAULT_URL))
    a = ap.parse_args()
    asyncio.run(build(a.url, a.people, a.days, a.seed))


if __name__ == "__main__":
    main()
