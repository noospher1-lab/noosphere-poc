"""
Сверка карты мнений на синтетике (фаза 3):

    python -m tools.opinionmap.check

1. Полный пересчёт из журналов (opinionmap.replay) == инкрементальные счётчики
   position_stats, current_stance и главные возражения — по каждой позиции.
2. Последний дневной снимок == пересчёт на конец того дня.
3. Хэш-цепочка событий цела.
4. Время ответа эндпоинтов экранов A и B (через приложение, без сети).
"""

import asyncio
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app import chain, db, opinion_db, opinionmap as om  # noqa: E402
from tools.opinionmap.synth import DEFAULT_URL  # noqa: E402


async def compare(url):
    db.DATABASE_URL = url
    await db.close_pool()
    pool = await db.init_pool()
    async with pool.acquire() as conn:
        topic = await conn.fetchval("SELECT min(topic_root_id) FROM positions")
    t = time.monotonic()
    ref = await opinion_db.recompute(topic)
    t_replay = time.monotonic() - t
    async with pool.acquire() as conn:
        stored = {r["position_id"]: dict(r) for r in await conn.fetch(
            "SELECT * FROM position_stats WHERE topic_root_id = $1", topic)}
        cur = {(r["user_id"], r["topic_root_id"]): r["position_id"] for r in await conn.fetch(
            "SELECT * FROM current_stance WHERE topic_root_id = $1", topic)}
        tops = {}
        for r in await conn.fetch("SELECT * FROM position_top ORDER BY position_id, rank"):
            tops.setdefault(r["position_id"], []).append(r["node_id"])
        last_day = await conn.fetchval(
            "SELECT max(day) FROM position_stats_daily WHERE topic_root_id = $1", topic)
        daily = {r["position_id"]: dict(r) for r in await conn.fetch(
            "SELECT * FROM position_stats_daily WHERE day = $1 AND topic_root_id = $2",
            last_day, topic)} if last_day else {}
    bad = []
    if cur != ref["current"]:
        bad.append(f"current_stance расходится: {len(cur)} против {len(ref['current'])}")
    for pid, s in sorted(ref["positions"].items()):
        got = stored.get(pid, {"in_now": 0, "stood": 0, "converted": 0})
        a = (got["in_now"], got["stood"], got["converted"])
        b = (s["in_now"], s["stood"], s["converted"])
        if a != b:
            bad.append(f"позиция #{pid}: счётчики {a}, пересчёт {b}")
        if tops.get(pid, []) != s["top"]:
            bad.append(f"позиция #{pid}: главные возражения {tops.get(pid)} против {s['top']}")
    if last_day:
        async with pool.acquire() as conn:
            j = await opinion_db.journals(conn, topic)
        snap = om.replay(*j[:6], until=om.day_end(last_day), k=j[6])["positions"]
        for pid, s in snap.items():
            got = daily.get(pid)
            if got and (got["in_now"], got["stood"], got["converted"]) != \
                    (s["in_now"], s["stood"], s["converted"]):
                bad.append(f"снимок {last_day} #{pid}: {got} против {s}")
    ch = await chain.verify()
    await db.close_pool()
    return topic, ref, bad, t_replay, ch


def timing(url, topic):
    from fastapi.testclient import TestClient
    from app import main
    db.DATABASE_URL = url
    res = {}

    if True:
        with TestClient(main.app) as c:
            pids = [p["id"] for p in c.get(f"/api/opinion/topic/{topic}").json()["positions"]]
            for name, path in (("экран A, 30 дней", f"/api/opinion/topic/{topic}?period=30"),
                               ("экран A, всё время", f"/api/opinion/topic/{topic}?period=all"),
                               ("экран B, 30 дней", f"/api/opinion/position/{pids[0]}?period=30"),
                               ("экран B, всё время", f"/api/opinion/position/{pids[0]}?period=all")):
                c.get(path)                          # прогрев
                t = time.monotonic()
                r = c.get(path)
                res[name] = (r.status_code, round((time.monotonic() - t) * 1000))
    return res


def main():
    url = os.environ.get("OPMAP_DATABASE_URL", DEFAULT_URL)
    topic, ref, bad, t_replay, ch = asyncio.run(compare(url))
    people = sum(p["in_now"] for p in ref["positions"].values())
    print(f"обсуждение #{topic}: людей в позициях {people}, "
          f"пересчёт из журналов {t_replay:.1f} с")
    if bad:
        print("❌ расхождения:")
        for b in bad[:30]:
            print("   ", b)
    else:
        print(f"✅ счётчики, главные возражения и снимок совпадают с пересчётом "
              f"({len(ref['positions'])} позиций)")
    print(("✅" if not ch["broken"] else "❌") +
          f" цепочка: {ch['checked']} событий, разрывов {len(ch['broken'])}")
    for name, (code, ms) in timing(url, topic).items():
        print(f"   {name}: {code}, {ms} мс")
    sys.exit(1 if bad or ch["broken"] else 0)


if __name__ == "__main__":
    main()
