"""
Сила довода в споре (vault: decisions/2026-09-15-dialectic-strength).

PoI говорит, насколько проработан сам текст, — и фиксируется при публикации.
Выдержал ли довод спор, по одному тексту не понять: это видно только по ветке
под ним. Отвеченное возражение уже не должно бить так, как неотвеченное, а
«оспорено · 2» у связи проблем считало и отбитые возражения тоже.

Счёт — градуальная семантика DF-QuAD (Rago, Toni и др., 2016), чистая функция
над деревом, без модели:
  - у каждого узла базовая сила: его PoI / 100, пока оценки нет — 0.5;
  - атаки (против, не доказывает) и поддержки (за) складываются как
    1 − Π(1 − сила): много слабых возражений вместе весят заметно, одно
    сильное — сильно;
  - атака перевешивает → сила падает от базовой к нулю, поддержка
    перевешивает → растёт к единице;
  - сила ребёнка сама посчитана так же, поэтому отбитое возражение почти
    ничего не отнимает.
Уточнение, вопрос, предложение, разбор — не за и не против: в счёт не идут.
Отозванный автором ответ тоже не идёт — автор на нём больше не настаивает.

Числа — для аудита; людям показываются слова (verdict), потому что пороги
условны и ещё будут меняться.
"""

from collections import defaultdict

ATTACK = frozenset({"refute", "undercut"})
SUPPORT = frozenset({"support"})
NEUTRAL_BASE = 0.5

# пороги слов: доля базовой силы, которая осталась у довода. 0.7, а не 0.8:
# у неоценённых узлов (база 0.5) возражение, на которое ответили равным по
# силе ответом, оставляет 0.75 — это «держится», а не «ослаблен». Неотвеченное
# возражение той же силы оставляет 0.5 — «ослаблен».
HOLDS_RATIO = 0.7
WEAKENED_RATIO = 0.4


def base_score(row):
    poi = row.get("poi_score")
    if poi is None:
        return NEUTRAL_BASE
    return min(1.0, max(0.0, float(poi) / 100.0))


def aggregate(values):
    """Совокупная сила нескольких атак (или поддержек): 1 − Π(1 − v)."""
    left = 1.0
    for v in values:
        left *= 1.0 - v
    return 1.0 - left


def combine(base, attack, support):
    if attack >= support:
        return base - base * (attack - support)
    return base + (1.0 - base) * (support - attack)


def strengths(rows):
    """rows: [{id, parent_id, rel, poi_score, retracted}] — дерево ответов.
    Возвращает {id: сила}. Обход снизу вверх без рекурсии (глубина до 50)."""
    by_id = {r["id"]: r for r in rows}
    kids = defaultdict(list)
    for r in rows:
        if r.get("parent_id") in by_id:
            kids[r["parent_id"]].append(r["id"])
    out = {}
    order, stack = [], [r["id"] for r in rows if r.get("parent_id") not in by_id]
    while stack:
        nid = stack.pop()
        order.append(nid)
        stack.extend(kids[nid])
    for nid in reversed(order):                 # дети раньше родителя
        att, sup = [], []
        for cid in kids[nid]:
            c = by_id[cid]
            if c.get("retracted"):
                continue
            if c.get("rel") in ATTACK:
                att.append(out[cid])
            elif c.get("rel") in SUPPORT:
                sup.append(out[cid])
        out[nid] = combine(base_score(by_id[nid]), aggregate(att), aggregate(sup))
    return out


def is_answer(reply, target):
    """Снимает ли ответ reply с узла target отметку «без ответа».

    Любой ответ ДРУГОГО человека, любого вида. Сначала отвеченным считалось
    только возражение, на которое возразили, — но на стенде на возражения
    почти всегда отвечают уточнением, и «без ответа» висело там, где ответ был
    (Alex 15.09: «сделай»). На силу это не влияет: она по-прежнему только из
    «за» и «против». Свой же ответ автора ответом не считается; автор
    неизвестен — считается (отказать в ответе хуже, чем засчитать лишний)."""
    a, b = reply.get("author_id"), target.get("author_id")
    return a is None or b is None or a != b


def word(base, strength, attacks, supports):
    """Слово для людей: не оспаривался / держится / ослаблен / сильно ослаблен."""
    if not attacks and not supports:
        return "untested"
    ratio = strength / base if base > 0 else (1.0 if strength >= base else 0.0)
    return ("holds" if ratio >= HOLDS_RATIO
            else "weakened" if ratio >= WEAKENED_RATIO else "shaken")


def verdict(rows, node_id):
    """Положение узла node_id в споре: слово, числа для аудита и возражения,
    на которые никто не ответил. rows — поддерево, корнем в node_id."""
    by_id = {r["id"]: r for r in rows}
    if node_id not in by_id:
        return None
    s = strengths(rows)
    live = [r for r in rows if r.get("parent_id") == node_id and not r.get("retracted")]
    attacks = [r for r in live if r.get("rel") in ATTACK]
    supports = [r for r in live if r.get("rel") in SUPPORT]
    answered = {r["parent_id"] for r in rows
                if not r.get("retracted") and r.get("parent_id") in by_id
                and is_answer(r, by_id[r["parent_id"]])}
    unanswered = [r["id"] for r in attacks if r["id"] not in answered]
    base = base_score(by_id[node_id])
    strength = s[node_id]
    return {"verdict": word(base, strength, len(attacks), len(supports)),
            "base": round(base, 3), "strength": round(strength, 3),
            "attacks": len(attacks), "supports": len(supports),
            "unanswered": unanswered}
