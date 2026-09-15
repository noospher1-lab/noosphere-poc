"""
«С кем ты совпадаешь» и размежевание обсуждения (vault: decisions/2026-09-15-alignment-view).

Alex 15.09: фракция — похожий набор согласий; она считается, в неё не вступают
(decisions/2026-09-15-alignment-and-forking, п. 5). Видимость (Alex 15.09):
  - себе — с именами: с кем совпадаешь ты сам и на чём;
  - всем — только обезличенная картина: сколько групп и по каким доводам они
    расходятся. Готовых списков «кто с кем в одной группе» система не выдаёт: в
    темах вроде языка в Украине это ярлык, который можно использовать против
    человека. Отметки с именами публичны и раньше — пересчитать вручную можно;
    система просто не делает этого за других.

Вход — отметки «согласен / не согласен» на доводах и позициях одного обсуждения:
[(person_id, item, stance)], item — "n:42" (довод) или "p:7" (позиция). Ничего не
начисляется. Чистые функции, без базы; порядок входа на результат не влияет.
"""

from collections import defaultdict

MIN_COMMON = 3     # меньше общих отметок — совпадение ничего не значит
MIN_MARKS = 3      # в общую картину идут люди хотя бы с тремя отметками
JOIN_AT = 0.7      # группы сливаются, пока среднее совпадение между ними не ниже
MIN_GROUP = 3      # группа меньше трёх человек не показывается: её легко узнать
MIN_ITEM = 2       # доля «за» в группе по доводу — только если отметили хотя бы двое
SPLIT_AT = 0.5     # «расходятся» — разница долей «за» между группами не меньше


def by_person(marks):
    people = defaultdict(dict)
    for pid, item, stance in marks:
        people[pid][item] = stance
    return people


def compare(a, b):
    """Общие отметки двух людей: на чём сошлись и на чём разошлись."""
    common = sorted(i for i in a if i in b)
    return ([i for i in common if a[i] == b[i]],
            [i for i in common if a[i] != b[i]])


def neighbours(me, marks, limit=5, min_common=MIN_COMMON):
    """С кем совпадает сам человек me — только для него самого."""
    people = by_person(marks)
    mine = people.get(me, {})
    rows = []
    for pid in sorted(people):
        if pid == me:
            continue
        same, differ = compare(mine, people[pid])
        n = len(same) + len(differ)
        if n < min_common:
            continue
        rows.append({"person_id": pid, "same": len(same), "common": n,
                     "share": round(len(same) / n, 3),
                     "agree_items": same, "differ_items": differ})
    closest = [r for r in sorted(rows, key=lambda r: (-r["share"], -r["common"], r["person_id"]))
               if r["share"] >= 0.5][:limit]
    farthest = [r for r in sorted(rows, key=lambda r: (r["share"], -r["common"], r["person_id"]))
                if r["share"] < 0.5][:limit]
    return {"marks": len(mine), "min_common": min_common,
            "closest": closest, "farthest": farthest}


def _clusters(people, min_common, join_at):
    """Слияние снизу вверх по среднему совпадению (average linkage), пока
    ближайшие группы совпадают не меньше join_at. Детерминированно: люди и
    пары перебираются по возрастанию id. На масштабе PoC (десятки людей в
    обсуждении) кубическая сложность не мешает."""
    ids = sorted(people)
    sim = {}
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            same, differ = compare(people[a], people[b])
            n = len(same) + len(differ)
            sim[(a, b)] = len(same) / n if n >= min_common else None
    clusters = [[p] for p in ids]

    def link(c1, c2):
        vals = [sim[(min(a, b), max(a, b))] for a in c1 for b in c2]
        vals = [v for v in vals if v is not None]
        return sum(vals) / len(vals) if vals else None

    while True:
        best = None
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                s = link(clusters[i], clusters[j])
                if s is not None and s >= join_at and (best is None or s > best[0]):
                    best = (s, i, j)
        if best is None:
            return clusters
        _, i, j = best
        clusters[i] = sorted(clusters[i] + clusters[j])
        del clusters[j]


def summary(marks, me=None, item_values=None, min_marks=MIN_MARKS,
            min_common=MIN_COMMON, join_at=JOIN_AT, min_group=MIN_GROUP, top_items=5):
    """Обезличенная картина обсуждения + номер группы самого me (только для него).

    item_values — {item: ценность довода}; тогда у каждой группы видно, на какие
    ценности опираются доводы, с которыми она в большинстве согласна (до трёх)."""
    people = {p: m for p, m in by_person(marks).items() if len(m) >= min_marks}
    clusters = _clusters(people, min_common, join_at)
    shown = sorted((c for c in clusters if len(c) >= min_group),
                   key=lambda c: (-len(c), c[0]))
    unplaced = sum(len(c) for c in clusters if len(c) < min_group)

    profiles = []
    for c in shown:
        counts = defaultdict(lambda: [0, 0])
        for p in c:
            for item, stance in people[p].items():
                counts[item][0 if stance == "agree" else 1] += 1
        profiles.append(counts)

    dividing = []
    if len(shown) >= 2:
        for item in sorted({it for pr in profiles for it in pr}):
            shares = []
            for pr in profiles:
                a, d = pr[item] if item in pr else (0, 0)
                shares.append(round(a / (a + d), 3) if a + d >= MIN_ITEM else None)
            known = [s for s in shares if s is not None]
            if len(known) >= 2 and max(known) - min(known) >= SPLIT_AT:
                dividing.append({"item": item, "agree_share": shares,
                                 "spread": round(max(known) - min(known), 3)})
        dividing.sort(key=lambda x: (-x["spread"], x["item"]))
        dividing = dividing[:top_items]

    def top_values(pr):
        counts = defaultdict(int)
        for item, (a, d) in pr.items():
            v = item_values.get(item)
            if v and a + d >= MIN_ITEM and a / (a + d) >= 0.5:
                counts[v] += 1
        return [{"id": v, "count": n}
                for v, n in sorted(counts.items(), key=lambda x: (-x[1], x[0]))[:3]]

    groups_out = []
    for c, pr in zip(shown, profiles):
        g = {"size": len(c)}
        if item_values is not None:
            g["values"] = top_values(pr)
        groups_out.append(g)

    my_group = next((i for i, c in enumerate(shown) if me in c), None)
    return {
        "public": {"people": len(people), "groups": groups_out,
                   "unplaced": unplaced, "dividing": dividing},
        "my_group": my_group,
    }
