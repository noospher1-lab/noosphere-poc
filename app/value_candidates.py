"""
Кандидаты в новые ценности (vault: decisions/2026-09-15-value-candidates).

Список ценностей живой (decisions/2026-09-15-values): у довода хранится пункт
списка и формулировка своими словами. Новая ценность проявляется в формулировках
раньше, чем в списке, двумя путями:
  - «other» — модель не нашла ей дома в списке;
  - «split» — одна и та же по смыслу формулировка раскладывается по разным
    пунктам, и ни один не держит её уверенно (дома нет, есть соседи).
Похожие по смыслу формулировки группируются (векторы — локальная многоязычная
модель, как у поиска похожих проблем). Кандидат — группа, которую набрали разные
люди в разных обсуждениях: одному человеку в одном споре ценность не открыть.

Решает, добавлять ли, пока Alex, позже — голосование. Имён тут нет: только
формулировки (они и так публичны у доводов) и счётчики. Чистые функции.
"""

import math
from collections import Counter

SIM_FLOOR = 0.6       # ниже — формулировки о разном
MIN_PEOPLE = 3        # кандидат набран хотя бы тремя людьми…
MIN_TOPICS = 2        # …хотя бы в двух обсуждениях
HOME_SHARE = 0.6      # пункт списка «держит» группу, если у него не меньше этой доли


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def value_counts(items):
    """Сколько доводов и людей опираются на каждый пункт списка."""
    nodes, people = Counter(), {}
    for it in items:
        v = it.get("value")
        if not v:
            continue
        nodes[v] += 1
        people.setdefault(v, set()).add(it.get("author_id"))
    return {v: {"nodes": nodes[v], "people": len(people[v])} for v in nodes}


def _clusters(items, vectors, sim_floor):
    """Детерминированно: по возрастанию id узла; узел идёт в группу, со всеми
    членами которой у него в среднем сходство не ниже порога (лучшую из таких)."""
    groups = []
    for it in sorted(items, key=lambda x: x["node_id"]):
        vec = vectors.get(it["node_id"])
        if vec is None:
            continue
        best, best_sim = None, sim_floor
        for g in groups:
            s = sum(cosine(vec, vectors[m["node_id"]]) for m in g) / len(g)
            if s >= best_sim:
                best, best_sim = g, s
        if best is None:
            groups.append([it])
        else:
            best.append(it)
    return groups


def candidates(items, vectors, sim_floor=SIM_FLOOR, min_people=MIN_PEOPLE,
               min_topics=MIN_TOPICS, home_share=HOME_SHARE, limit=10):
    """items: [{node_id, author_id, topic, value, phrase}] — доводы с формулировкой.
    vectors: {node_id: вектор формулировки}."""
    with_phrase = [it for it in items if it.get("phrase") and it.get("value")]
    out = []
    for g in _clusters(with_phrase, vectors, sim_floor):
        people = {m.get("author_id") for m in g}
        topics = {m.get("topic") for m in g}
        if len(people) < min_people or len(topics) < min_topics:
            continue
        vals = Counter(m["value"] for m in g)
        top_value, top_n = vals.most_common(1)[0]
        if vals.get("other", 0) / len(g) >= 0.5:
            reason = "other"
        elif top_n / len(g) < home_share:
            reason = "split"
        else:
            continue                      # у группы есть дом в списке
        phrases = Counter(m["phrase"] for m in g)
        out.append({
            "phrases": [p for p, _ in sorted(phrases.items(), key=lambda x: (-x[1], len(x[0]), x[0]))][:5],
            "people": len(people), "topics": len(topics), "nodes": len(g),
            "values": dict(sorted(vals.items(), key=lambda x: (-x[1], x[0]))),
            "reason": reason,
            "node_ids": sorted(m["node_id"] for m in g)[:10],
            "_first": min(m["node_id"] for m in g),
        })
    out.sort(key=lambda c: (-c["people"], -c["nodes"], c["_first"]))
    for c in out:
        c.pop("_first")
    return out[:limit]
