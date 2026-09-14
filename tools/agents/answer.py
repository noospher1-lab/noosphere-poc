#!/usr/bin/env python3
"""Ответить на конкретный узел — когда человеку в графе не ответили.

Обычный ход агента выбирает цель сам, и слабый по PoI текст живого участника
он спокойно обходит. Здесь цель задана: агенты обязаны разобрать именно её.

    python3 answer.py 59 --who skeptik inzhener polevoy
    python3 answer.py 35 --cast arena --who institut oksana --base-url http://localhost:8010
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import export                                        # noqa: E402
import runner                                        # noqa: E402
import view                                          # noqa: E402
from personas import ARENA, PERSONAS                 # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("node_id", type=int)
    ap.add_argument("--who", nargs="*", default=None, help="ключи персон")
    ap.add_argument("--cast", choices=["default", "arena"], default="default",
                    help="из какого состава брать персоны (arena — трое для спора)")
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--model", default="claude-opus-5")
    ap.add_argument("--effort", default="medium")
    ap.add_argument("--budget", type=float, default=3.0)
    ap.add_argument("--password", default="agents-local-2026")
    args = ap.parse_args()

    runner.load_env()
    cast = ARENA if args.cast == "arena" else PERSONAS
    chosen = [p for p in cast
              if not args.who or p["key"] in args.who] or cast[:3]

    probe = runner.Poc(args.base_url)
    node = probe.get(f"/api/nodes/{args.node_id}")
    root = node.get("topic_root_id") or args.node_id
    runlog = export.RunLog()

    runner.head(f'Ответ на узел #{args.node_id} — {node.get("author")}')
    print(node.get("text"), "\n", flush=True)

    # Новая персона может ещё не иметь аккаунта — заводим и подтверждаем
    # почту ровно так же, как в основном прогоне.
    agents = [runner.Agent(persona, i, args) for i, persona in enumerate(chosen)]
    for a in agents:
        a.signin()
    runner.verify_accounts([a.username for a in agents], args.budget)

    for a in agents:
        a.runlog = runlog
        a.login()
        if not a.afford(reserve=0.05):
            continue
        ctx = (f"{view.problem_brief(a.poc, root)}\n\n"
               f"РАЗБОР СЕЙЧАС:\n{view.branch_view(a.poc, root)}\n\n"
               f"Тебе нужно ответить ИМЕННО на узел #{args.node_id} — его "
               f"написал живой участник, и ему до сих пор никто не ответил.\n\n"
               f'Его текст:\n«{node.get("text")}»\n\n'
               f"Отвечай ему, а не соседям по ветке. Если довод слаб — скажи "
               f"прямо, в чём, но найди в нём то, что стоит разбора: человек "
               f"пришёл с настоящим вопросом, даже если сформулировал его "
               f"наспех. Не поучай и не хвали.")
        d = a.brain.choose(ctx, runner.ACT_TOOL)
        if not d or not (d.get("draft") or "").strip():
            a.log("нечего сказать", dim=True)
            continue
        edge = d.get("edge_type") or "qualify"
        a.log(f"отвечает ({edge}) — {d.get('why', '')}", dim=True)
        a._publish(root, args.node_id, edge, d["draft"].strip(), ctx)

    runner.poll_poi(probe, [n for n in runlog.companion])


if __name__ == "__main__":
    main()
