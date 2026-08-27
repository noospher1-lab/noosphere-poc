"""Что агент видит, когда открывает проблему.

Ровно те же данные, что видит человек в дереве: постановка, реестр попыток,
ветка целиком с оценками и авторами. Ничего служебного — никаких подсказок
модели о том, «куда бы ответить повыгоднее».
"""

from collections import defaultdict


def _clip(s, n):
    s = (s or "").strip().replace("\n", " ")
    return s if len(s) <= n else s[:n - 1] + "…"


def problem_brief(poc, root_id, max_interventions=6):
    prob = poc.problem(root_id)
    root = poc.node(root_id)
    out = [f'ПРОБЛЕМА #{root_id} — {root.get("title") or ""}', "",
           (root.get("text") or "").strip()]
    if prob.get("causes"):
        out += ["", "ПОЧЕМУ ДЕРЖИТСЯ (рамка автора):", prob["causes"].strip()]
    if prob.get("gap"):
        out += ["", "ЧЕГО НЕ ХВАТАЕТ:", prob["gap"].strip()]
    scale = prob.get("scale") or []
    if scale:
        out += ["", "МАСШТАБ:"]
        out += [f'— {s.get("figure")} ({s.get("region")}, {s.get("source") or "источник не указан"})'
                for s in scale]
    iv = prob.get("interventions") or []
    if iv:
        out += ["", f"ЧТО УЖЕ ПРОБОВАЛИ ({prob.get('interventions_total', len(iv))} записей в реестре):"]
        for i in iv[:max_interventions]:
            out.append(f'— [{i.get("outcome_kind")}] {_clip(i.get("what"), 110)}'
                       f' | {i.get("geo") or "?"} | итог: {_clip(i.get("outcome"), 130)}')
    return "\n".join(out)


def branch_view(poc, root_id, text_chars=420):
    """Дерево обсуждения так, как оно выглядит на экране: отступ = глубина.

    Узел без ответов помечается: без этой пометки разбор вырождается в одну
    нитку — каждый отвечает на то, что написано минуту назад, а доводы выше по
    ветке так и остаются неразобранными.
    """
    g = poc.graph()
    nodes = {n["id"]: n for n in g["nodes"]}
    names = {n["id"]: n.get("author") for n in poc.topic_nodes(root_id)}
    kids = defaultdict(list)
    for l in g["links"]:
        kids[l["target"]].append((l["source"], l["type"]))

    lines = []
    seen = set()

    def walk(nid, depth):
        if nid in seen or nid not in nodes:
            return
        seen.add(nid)
        n = nodes[nid]
        poi = n.get("poi_score")
        poi_s = f"PoI {poi:.1f}" if isinstance(poi, (int, float)) else "PoI —"
        rel = n.pop("_rel", None)
        open_mark = " · БЕЗ ОТВЕТА" if not kids.get(nid) else ""
        head = (f'{"  " * depth}[{nid}] {rel or "проблема"} · '
                f'{names.get(nid) or "?"} · {n.get("kind")} · {poi_s}'
                f'{open_mark}')
        lines.append(head)
        lines.append(f'{"  " * depth}    {_clip(n.get("text"), text_chars)}')
        for child, rel_type in sorted(kids.get(nid, [])):
            if child in nodes:
                nodes[child]["_rel"] = rel_type
                walk(child, depth + 1)

    walk(root_id, 0)
    return "\n".join(lines)


def positions_view(positions):
    out = []
    for p in positions.get("positions", []):
        out.append(f'[позиция {p["id"]}] ({p.get("stance") or "—"}) '
                   f'{_clip(p.get("headline"), 160)}')
        if p.get("composed"):
            out.append(f'    {_clip(p["composed"], 400)}')
    return "\n".join(out) or "(позиции ещё не сведены)"
