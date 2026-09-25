"""
Проверка хэш-цепочки журнала событий (vault: decisions/2026-09-25-opinion-map).

    python -m app.chain verify

Идёт по событиям с хэшем по порядку и пересчитывает каждый хэш из предыдущего
и полей строки. Разрыв — строку правили, выкинули или вставили задним числом.
События до миграции (без хэша) не проверяются: цепочка начинается с генезиса.
"""

import asyncio
import json
import sys

from . import db


async def verify(batch=5000):
    """Возвращает {"checked": N, "broken": [event_id, …]}."""
    pool = await db.init_pool()
    prev, broken, checked, after = db.GENESIS, [], 0, 0
    async with pool.acquire() as conn:
        while True:
            rows = await conn.fetch(
                "SELECT id, ts, type, author_id, payload, prev_hash, hash FROM events "
                "WHERE hash IS NOT NULL AND id > $1 ORDER BY id LIMIT $2", after, batch)
            if not rows:
                break
            for r in rows:
                payload = r["payload"]
                if isinstance(payload, str):
                    payload = json.loads(payload)
                want = db.event_digest(prev, r["id"], r["ts"], r["type"],
                                       r["author_id"], payload)
                if r["prev_hash"] != prev or r["hash"] != want:
                    broken.append(r["id"])
                prev = r["hash"]
                checked += 1
                after = r["id"]
    return {"checked": checked, "broken": broken}


def main():
    if sys.argv[1:] != ["verify"]:
        print("использование: python -m app.chain verify")
        sys.exit(2)
    res = asyncio.run(verify())
    if res["broken"]:
        print(f"❌ цепочка разорвана: {len(res['broken'])} из {res['checked']}, "
              f"первое событие — #{res['broken'][0]}")
        sys.exit(1)
    print(f"✅ цепочка цела: {res['checked']} событий")


if __name__ == "__main__":
    main()
