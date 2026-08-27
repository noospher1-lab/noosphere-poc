"""Выгрузка прогона в папку с текстами — чтобы структуру было видно глазом.

    runs/<прогон>/
      README.md              чем был прогон: участники, параметры, счёт
      problem-33/
        problem.md           постановка: текст корня, причины, чего не хватает
        tree.md              структура дерева: отступ = ответ на узел выше
        nodes/035-refute-dan-volf.md    один узел = один файл
        companion/035.md     разговор с компаньоном перед этой публикацией
      summary.md             PoI и траты по участникам

Диалог с компаньоном платформа не хранит (он эфемерен по устройству), поэтому
его пишет раннер — по ходу прогона, в `companion/`. У узлов, написанных до
того, как это появилось, раздела не будет.
"""

import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

RUNS = Path(__file__).resolve().parent / "runs"

_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "",
    "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya", " ": "-",
}
REL_RU = {"support": "довод за", "refute": "возражение",
          "qualify": "уточнение", "question": "вопрос",
          "undercut": "подрыв", "proposal": "предложение",
          "exploration": "исследование", "detail": "деталь"}


def slug(text, limit=28):
    out = "".join(_TRANSLIT.get(ch, ch) for ch in (text or "").lower())
    out = re.sub(r"[^a-z0-9-]+", "-", out).strip("-")
    return out[:limit] or "bez-imeni"


class RunLog:
    """Копилка разговоров с компаньоном: заполняется по ходу прогона."""

    def __init__(self):
        self.companion = defaultdict(list)   # node_id -> [ходы]
        self.pending = []                    # ходы до того, как узел получил id

    def turn(self, author, draft, reply, suggestion, final=None):
        self.pending.append({"автор": author, "черновик": draft,
                             "компаньон": reply, "предложение": suggestion,
                             "итоговый текст": final})

    def attach(self, node_id):
        """Привязать накопленные ходы к опубликованному узлу."""
        if self.pending:
            self.companion[node_id].extend(self.pending)
            self.pending = []

    def drop(self):
        self.pending = []


def _node_file(n, rel, parent_id, comp_turns):
    poi = n.get("poi_score")
    lines = [f'# Узел {n["id"]} · {REL_RU.get(rel, rel or "постановка")}'
             + (f' → #{parent_id}' if parent_id else ""), ""]
    lines += [f'- **автор:** {n.get("author") or "?"}',
              f'- **вид:** {n.get("kind")}',
              f'- **PoI:** {poi:.1f}' if isinstance(poi, (int, float))
              else "- **PoI:** — (не оценивается)"]
    b = n.get("poi_breakdown") or {}
    crit = {k: v for k, v in (b.get("criteria") or b).items()
            if k not in ("total", "summary", "topic")}
    if crit:
        lines.append("- **по критериям:** " + ", ".join(
            f'{k} {v.get("score") if isinstance(v, dict) else v}'
            for k, v in crit.items()))
    if b.get("summary"):
        lines.append(f'- **разбор оценки:** {b["summary"]}')
    if n.get("created_at"):
        lines.append(f'- **опубликован:** {str(n["created_at"])[:19]}')
    lines += ["", "## Текст", "", (n.get("text") or "").strip(), ""]

    if comp_turns:
        lines += ["## Разговор с компаньоном перед публикацией", ""]
        for i, t in enumerate(comp_turns, 1):
            lines += [f"**Черновик {i}:**", "", t["черновик"].strip(), "",
                      "**Компаньон:**", "", (t["компаньон"] or "").strip(), ""]
            if t.get("предложение"):
                lines += ["**Компаньон предложил формулировку:**", "",
                          t["предложение"].strip(), ""]
            if t.get("итоговый текст"):
                lines += ["**Автор оставил в итоге:**", "",
                          t["итоговый текст"].strip(), ""]
    return "\n".join(lines) + "\n"


def export_problem(poc, root_id, out_dir, runlog=None):
    import view

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "nodes").mkdir(exist_ok=True)

    prob = poc.problem(root_id)
    root = poc.node(root_id)
    head = [f'# Проблема #{root_id} — {root.get("title") or ""}', "",
            "## Постановка", "", (root.get("text") or "").strip(), ""]
    if prob.get("causes"):
        head += ["## Почему держится", "", prob["causes"].strip(), ""]
    if prob.get("gap"):
        head += ["## Чего не хватает", "", prob["gap"].strip(), ""]
    if prob.get("scale"):
        head += ["## Масштаб", ""]
        head += [f'- {s.get("figure")} ({s.get("region")})' for s in prob["scale"]]
        head.append("")
    (out_dir / "problem.md").write_text("\n".join(head), encoding="utf-8")

    g = poc.graph()
    nodes = {n["id"]: n for n in g["nodes"]}
    authors = {n["id"]: n.get("author") for n in poc.topic_nodes(root_id)}
    kids = defaultdict(list)
    rel_of = {}
    for l in g["links"]:
        kids[l["target"]].append(l["source"])
        rel_of[l["source"]] = l["type"]

    tree, written = [f'# Дерево проблемы #{root_id}', "",
                     "Отступ — ответ на узел выше.", ""], []

    def walk(nid, depth):
        n = nodes.get(nid)
        if n is None:
            return
        n["author"] = authors.get(nid) or n.get("author")
        rel, parent = rel_of.get(nid), None
        for p, cs in kids.items():
            if nid in cs:
                parent = p
        poi = n.get("poi_score")
        poi_s = f'PoI {poi:.1f}' if isinstance(poi, (int, float)) else "PoI —"
        name = f'{nid:03d}-{slug(rel or "problem", 10)}-{slug(n.get("author"), 16)}.md'
        tree.append(f'{"  " * depth}- **[{nid}]** {REL_RU.get(rel, rel or "постановка")}'
                    f' · {n.get("author") or "?"} · {poi_s} — [`{name}`](nodes/{name})')
        tree.append(f'{"  " * depth}  {(n.get("text") or "")[:160].strip()}…')
        turns = (runlog.companion.get(nid) if runlog else None) or []
        (out_dir / "nodes" / name).write_text(
            _node_file(n, rel, parent, turns), encoding="utf-8")
        written.append(nid)
        if turns:
            (out_dir / "companion").mkdir(exist_ok=True)
            (out_dir / "companion" / f"{nid:03d}.md").write_text(
                "\n".join([f"# Разговор перед публикацией узла {nid}", ""]
                          + [json.dumps(t, ensure_ascii=False, indent=2)
                             for t in turns]), encoding="utf-8")
        for c in sorted(kids.get(nid, [])):
            walk(c, depth + 1)

    walk(root_id, 0)
    (out_dir / "tree.md").write_text("\n".join(tree) + "\n", encoding="utf-8")
    return written


def new_run_dir(tag=None):
    name = datetime.now().strftime("%Y-%m-%d-%H%M") + (f"-{tag}" if tag else "")
    d = RUNS / name
    d.mkdir(parents=True, exist_ok=True)
    return d
