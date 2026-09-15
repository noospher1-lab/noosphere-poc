"""
Плоский граф: карта проблем и ветка слоями (vault: decisions/2026-09-15-flat-graph).

Прежний 3D-граф (силовая раскладка, свечения, бегущие импульсы) каждый раз
ложился по-разному и не читался: Alex 15.09 — «не читабельно и непонятно для
людей… проще, стабильней, без движущихся элементов». Здесь только ДАННЫЕ для
картинки, без раскладки: раскладку считает страница, детерминированно.

Чистые функции над выдачей db.get_graph() — без базы, поэтому тестируются
напрямую:
  - map_view   — проблемы с уровнем «порождает» (выше та, у которой не названо
                 причин) и сводкой ветки; прочие корни отдельно;
  - topic_view — ветка одной проблемы в порядке обхода, у каждого узла отметки,
                 которые страница подсвечивает своим цветом:
                   unanswered — возражение, на которое никто, кроме его
                                автора, ещё не ответил (любым видом ответа);
                   open       — вопрос без единого ответа;
                   holds      — довод оспаривали, а он держится (dialectic);
                   concedes   — ответ признаёт часть того, на что отвечает.
"""

import json
from collections import defaultdict

from . import dialectic
from . import values as values_mod

LABEL_MAX = 90


def _index(graph):
    nodes = {n["id"]: n for n in graph.get("nodes", [])}
    parent, rel, kids = {}, {}, defaultdict(list)
    for link in graph.get("links", []):
        s, t = link["source"], link["target"]
        if s in nodes and t in nodes and s not in parent and s != t:
            parent[s], rel[s] = t, link["type"]
            kids[t].append(s)
    for t in kids:                               # по времени, как в дереве
        kids[t].sort(key=lambda i: (nodes[i].get("created_at") is None,
                                    str(nodes[i].get("created_at") or ""), i))
    conceded = {(c["node_id"], c["target_id"]): c.get("anchor_quote")
                for c in graph.get("concessions", [])}
    return nodes, parent, rel, kids, conceded


def _preorder(root, kids):
    order, depth, stack, seen = [], {root: 0}, [root], set()
    while stack:
        i = stack.pop()
        if i in seen:
            continue
        seen.add(i)
        order.append(i)
        for c in reversed(kids.get(i, [])):
            depth[c] = depth[i] + 1
            stack.append(c)
    return order, depth


def _label(n, is_root):
    if is_root and n.get("title"):
        return str(n["title"])
    bd = n.get("poi_breakdown")
    if isinstance(bd, dict) and bd.get("topic"):
        return str(bd["topic"])
    t = " ".join(str(n.get("text") or "").split())
    return t if len(t) <= LABEL_MAX else t[:LABEL_MAX].rstrip() + "…"


def node_label(n, is_root=False):
    """Одно название узла на всех экранах — граф, строка дерева, заголовок
    панели. Раньше граф показывал короткую тему, а дерево — начало текста, и
    найденное в графе в дереве было не узнать (Alex 15.09: «слова или названия
    должны быть одинаковыми»). Корень — свой заголовок; ответ — тема, которую
    проставляет оценка; пока оценки нет — начало текста, везде одинаково."""
    bd = n.get("poi_breakdown")
    if isinstance(bd, str):
        try:
            bd = json.loads(bd)
        except ValueError:
            bd = None
    return _label({**n, "poi_breakdown": bd}, is_root)


def _rows(ids, root, nodes, parent, rel):
    return [{"id": i, "parent_id": None if i == root else parent.get(i),
             "rel": None if i == root else rel.get(i),
             "poi_score": nodes[i].get("poi_score"),
             "author_id": nodes[i].get("author_id"),
             "retracted": bool(nodes[i].get("retracted_at"))} for i in ids]


def _topic(idx, root):
    nodes, parent, rel, kids, conceded = idx
    ids, depth = _preorder(root, kids)
    strength = dialectic.strengths(_rows(ids, root, nodes, parent, rel))
    out = []
    summary = {"replies": len(ids) - 1, "unanswered": 0, "open": 0,
               "holds": 0, "concedes": 0}
    for i in ids:
        n = nodes[i]
        retracted = bool(n.get("retracted_at"))
        r = None if i == root else rel.get(i)
        live = [c for c in kids.get(i, []) if not nodes[c].get("retracted_at")]
        attacks = [c for c in live if rel.get(c) in dialectic.ATTACK]
        supports = [c for c in live if rel.get(c) in dialectic.SUPPORT]
        base = dialectic.base_score(n)
        word = dialectic.word(base, strength[i], len(attacks), len(supports))
        is_question = n.get("kind") == "question" or r == "question"
        flags = {
            "unanswered": (not retracted and r in dialectic.ATTACK
                           and not any(dialectic.is_answer(nodes[c], n) for c in live)),
            "open": (not retracted and is_question and not live),
            "holds": (not retracted and word == "holds" and bool(attacks)),
            "concedes": (i != root and (i, parent.get(i)) in conceded),
        }
        for k, v in flags.items():
            if v:
                summary[k] += 1
        out.append({
            "id": i, "parent": None if i == root else parent.get(i),
            "depth": depth.get(i, 0), "rel": r, "kind": n.get("kind"),
            "label": _label(n, i == root), "text": n.get("text") or "",
            "author": n.get("author"), "author_id": n.get("author_id"),
            "poi_score": n.get("poi_score"), "retracted": retracted,
            "value": n.get("value"), "value_name": values_mod.name(n.get("value")),
            "value_phrase": n.get("value_phrase"),
            "verdict": word, "attacks": len(attacks), "supports": len(supports),
            "unanswered_ids": [c for c in attacks
                               if not any(dialectic.is_answer(nodes[g], nodes[c])
                                          for g in kids.get(c, [])
                                          if not nodes[g].get("retracted_at"))],
            "flags": flags,
            "concede_quote": conceded.get((i, parent.get(i))) if flags["concedes"] else None,
        })
    return out, summary


def _is_root(i, nodes, parent):
    # Корень — не ответ ни на что и сам себе тема. Узлы-атрибуции живут в теме
    # проблемы без ребра: они не корни, у них topic_root_id — чужой.
    return i not in parent and nodes[i].get("topic_root_id") in (None, i)


def topic_view(graph, root_id):
    idx = _index(graph)
    nodes, parent = idx[0], idx[1]
    if root_id not in nodes or not _is_root(root_id, nodes, parent):
        return None
    out, summary = _topic(idx, root_id)
    return {"root": root_id, "title": _label(nodes[root_id], True),
            "kind": nodes[root_id].get("kind"), "nodes": out, "summary": summary}


def map_view(graph):
    idx = _index(graph)
    nodes, parent, rel, kids, _ = idx
    roots = [i for i in sorted(nodes) if _is_root(i, nodes, parent)]
    problems = {i for i in roots if nodes[i].get("kind") == "problem"}

    causes_of, effects_of, links = defaultdict(list), defaultdict(list), []
    for pl in graph.get("problem_links", []):
        c, e, j = pl.get("cause_id"), pl.get("effect_id"), pl.get("node_id")
        if c not in problems or e not in problems or c == e:
            continue
        causes_of[e].append(c)
        effects_of[c].append(e)
        dx = None
        if j in nodes and j not in (c, e):
            sub, _ = _preorder(j, kids)
            rows = _rows(sub, j, nodes, parent, rel)
            dx = dialectic.verdict(rows, j)
        links.append({"cause_id": c, "effect_id": e, "node_id": j,
                      "verdict": dx["verdict"] if dx else None,
                      "unanswered": len(dx["unanswered"]) if dx else 0})

    memo = {}

    def level(p, trail=()):
        # выше всех — проблема без названных причин; круг причин не ошибка,
        # а структура: разрываем его там, где обход вернулся в себя
        if p in memo:
            return memo[p]
        if p in trail:
            return 0
        cs = causes_of.get(p, [])
        v = 0 if not cs else 1 + max(level(c, trail + (p,)) for c in cs)
        memo[p] = v
        return v

    def card(i):
        _, summary = _topic(idx, i)
        n = nodes[i]
        return {"id": i, "kind": n.get("kind"), "title": _label(n, True),
                "summary": summary}

    return {
        "problems": [{**card(i), "level": level(i),
                      "causes": sorted(set(causes_of.get(i, []))),
                      "effects": sorted(set(effects_of.get(i, [])))}
                     for i in roots if i in problems],
        "others": [card(i) for i in roots if i not in problems],
        "links": links,
    }
