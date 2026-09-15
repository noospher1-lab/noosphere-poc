"""
Плоский граф (vault: decisions/2026-09-15-flat-graph): данные для карты проблем
и для ветки слоями. Чистые функции над выдачей db.get_graph() — без базы.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app import graphview  # noqa: E402


def node(i, kind="argument", root=None, poi=None, title=None, retracted=False, text=None, **kw):
    return {"id": i, "kind": kind, "topic_root_id": root if root is not None else i,
            "poi_score": poi, "title": title, "text": text or f"узел {i}",
            "retracted_at": "2026-09-15" if retracted else None,
            "created_at": f"2026-09-15 00:00:{i:02d}", **kw}


def link(s, t, typ):
    return {"source": s, "target": t, "type": typ}


def graph():
    return {
        "nodes": [
            node(1, "problem", title="Запрет языка"),
            node(2, root=1, poi=60, author_id=7),    # против проблемы, без ответа
            node(3, root=1, poi=70),                 # за — его оспорили и отбили
            node(4, root=1, poi=60),                 # против 3
            node(5, root=1, poi=80),                 # против 4 — ответ
            node(6, "question", root=1),             # открытый вопрос
            node(7, "question", root=1),             # вопрос с ответом
            node(8, root=1),                         # ответ на 7
            node(9, root=1, poi=90, retracted=True), # отозванное возражение
            node(10, "attribution", root=1),         # живёт в теме без ребра
            node(11, "problem", title="Представители не представляют"),
            node(12, "problem", title="Круг А"),
            node(13, "problem", title="Круг Б"),
            node(20, "argument", title="Просто тезис"),
            # уточнение самого автора возражения — это не ответ на него
            node(21, root=1, author_id=7, poi_breakdown={"topic": "короткая подпись"}),
        ],
        "links": [
            link(2, 1, "refute"), link(3, 1, "support"), link(4, 3, "refute"),
            link(5, 4, "refute"), link(6, 1, "question"), link(7, 1, "question"),
            link(8, 7, "qualify"), link(9, 1, "refute"), link(21, 2, "qualify"),
        ],
        "problem_links": [
            {"cause_id": 11, "effect_id": 1, "node_id": 3},
            {"cause_id": 12, "effect_id": 13, "node_id": None},
            {"cause_id": 13, "effect_id": 12, "node_id": None},
        ],
        "concessions": [{"node_id": 4, "target_id": 3, "anchor_quote": "часть"}],
    }


def by_id(view):
    return {n["id"]: n for n in view["nodes"]}


def test_topic_flags_each_highlight():
    v = graphview.topic_view(graph(), 1)
    n = by_id(v)
    assert n[2]["flags"]["unanswered"] is True
    assert n[4]["flags"]["unanswered"] is False              # на него ответили (#5)
    assert n[5]["flags"]["unanswered"] is True
    assert n[6]["flags"]["open"] is True
    assert n[7]["flags"]["open"] is False                    # под ним есть ответ
    assert n[3]["flags"]["holds"] is True and n[3]["attacks"] == 1
    assert n[4]["flags"]["concedes"] is True and n[4]["concede_quote"] == "часть"
    assert n[9]["flags"]["unanswered"] is False              # отозвано — не зовёт ответить
    assert n[9]["retracted"] is True


def test_someone_elses_qualify_answers_the_objection():
    g = graph()
    for x in g["nodes"]:
        if x["id"] == 21:
            x["author_id"] = 8                       # уже не сам автор возражения
    n = by_id(graphview.topic_view(g, 1))
    assert n[2]["flags"]["unanswered"] is False
    assert n[1]["unanswered_ids"] == []


def test_topic_order_depth_and_labels():
    v = graphview.topic_view(graph(), 1)
    ids = [x["id"] for x in v["nodes"]]
    assert ids[0] == 1 and 10 not in ids
    assert ids.index(2) < ids.index(21) < ids.index(3)       # обход в глубину, по времени
    n = by_id(v)
    assert n[1]["parent"] is None and n[1]["label"] == "Запрет языка"
    assert n[5]["depth"] == 3
    assert n[21]["label"] == "короткая подпись"
    assert n[1]["unanswered_ids"] == [2]                     # 9 отозвано
    assert v["summary"]["replies"] == 9
    assert v["summary"]["unanswered"] == 2 and v["summary"]["open"] == 1


def test_topic_is_built_only_from_a_root():
    g = graph()
    assert graphview.topic_view(g, 3) is None                # ответ, не корень
    assert graphview.topic_view(g, 10) is None               # атрибуция
    assert graphview.topic_view(g, 999) is None


def test_map_levels_links_and_others():
    m = graphview.map_view(graph())
    probs = {p["id"]: p for p in m["problems"]}
    assert set(probs) == {1, 11, 12, 13}
    assert probs[11]["level"] == 0 and probs[1]["level"] == 1   # причина выше
    assert probs[1]["causes"] == [11] and probs[11]["effects"] == [1]
    assert {probs[12]["level"], probs[13]["level"]} <= {0, 1, 2}  # круг не зацикливает
    assert [o["id"] for o in m["others"]] == [20]
    link = next(l for l in m["links"] if l["cause_id"] == 11)
    assert link["verdict"] == "holds" and link["unanswered"] == 0
    assert probs[1]["summary"]["unanswered"] == 2


def test_empty_graph():
    assert graphview.map_view({"nodes": [], "links": []}) == {
        "problems": [], "others": [], "links": []}
