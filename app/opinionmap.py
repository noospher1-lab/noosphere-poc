"""
Карта мнений: правила счёта (vault: decisions/2026-09-25-opinion-map).

Позиция здесь — место, где СТОИТ ЧЕЛОВЕК, а не группа доводов. Кто где стоит,
записано только в журналах: `stance_log` (переходы), `exposures` (ответы на
возражения), `node_joins`, `answer_acceptances`. Реакции «согласен / не
согласен» и PoI людей в числа карты не входят — иначе вернётся ровно то, от
чего ушли: число, которое накручивается кликом.

Этот модуль — чистые функции без базы. Им пользуются дважды:
  - `replay` пересобирает все числа из журналов с нуля — это эталон, с которым
    сверяются инкрементальные счётчики `position_stats` (тест) и из которого
    строятся дневные снимки;
  - функции ранжирования и группировки — общие для эталона и для базы, чтобы
    правило было записано в одном месте.
"""

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

# Сколько главных возражений у позиции (ТЗ: K = 3, настраивается).
TOP_K = 3
# Доля спросивших, принявших один ответ, при которой вопрос «ответ найден».
ACCEPT_SHARE = 0.5
# Сколько людей нужно позиции, чтобы выйти на карту из «складывается». Тот же
# порог, что MIN_GROUP в alignment.py: группу меньше трёх легко узнать поимённо.
MIN_SUPPORTERS = 3
# Пока активных позиций меньше, главным экраном остаётся дерево.
MIN_POSITIONS = 3
# Поток между позициями меньше этого числа людей не показывается отдельной
# строкой, а уходит в «ещё N» — по той же причине, что MIN_SUPPORTERS.
MIN_FLOW = 3

# Переход засчитывается как «человек передумал», только если его сделал сам
# человек. Слияние и раскол переносят людей системно, а отмена возвращает
# туда, где человек уже был, — ни то, ни другое не «переубедили».
PERSUASION = "person"
SOURCES = ("person", "merge", "split", "undo")

# Ответ на возражение. «shown» — только знак, что возражение показали; он не
# затирает настоящий ответ. «withdrawn» — отмена ответа.
RESPONSES = ("shown", "convinced", "partial", "not_convinced", "withdrawn")
STOOD = ("not_convinced", "partial")

OBJECTION_EDGES = ("refute", "undercut")

# Маршрутизатор ответа. Выше DUP_SIM — «это уже сказано, присоединиться?»,
# выше PLACE_SIM — кандидат в место для нового текста. DUP_SIM строже порога
# проблем (embed.SIM_FLOOR = 0,6): там подсказка «похожая проблема», здесь —
# предложение вообще не писать своё, и ложное срабатывание заставило бы человека
# молчать. Значения сверены на живой модели — см. decisions/2026-09-25-opinion-map.
DUP_SIM = 0.85
PLACE_SIM = 0.5
ACTION_EDGE = {"support": "support", "qualify": "qualify", "refute": "refute",
               "question": "question"}
EDGE_WORD = {"support": "за", "qualify": "уточнение", "refute": "против",
             "question": "вопрос", "undercut": "не доказывает"}
CRUX_KINDS = ("question", "undercut")

STATUSES = ("forming", "active", "empty", "merged", "split")
OPEN_STATUSES = ("forming", "active", "empty")   # в них можно перейти


def next_status(status, in_now):
    """Статус позиции после изменения числа людей в ней. None — не меняется.

    Складывается → на карте, когда набралось MIN_SUPPORTERS. Держится, пока в
    ней есть хоть один человек (макет: «держится, если есть хотя бы один
    сторонник»); опустевшая позиция не удаляется — её ID остаётся в журналах
    и в чужих ссылках, она просто уходит с карты до первого вернувшегося.
    """
    if status == "forming" and in_now >= MIN_SUPPORTERS:
        return "active"
    if status == "active" and in_now == 0:
        return "empty"
    if status == "empty" and in_now >= 1:
        return "active"
    return None


def rank_objections(candidates, out_counts, k=TOP_K):
    """Главные возражения позиции.

    candidates: [{id, poi, created_at}] — узлы «против» / «не доказывает», направленные
    на узлы позиции. out_counts: {node_id: сколько людей ушло из позиции, назвав
    этот узел причиной}. Порядок: по уходам, при равенстве — по PoI текста
    (нет оценки — ниже любой оценки), затем старше выше: дольше висит — больше
    людей успели его увидеть.
    """
    def key(c):
        poi = c.get("poi")
        return (-out_counts.get(c["id"], 0),
                0 if poi is not None else 1, -(poi or 0),
                _ts(c.get("created_at")), c["id"])
    return [c["id"] for c in sorted(candidates, key=key)[:k]]


def _ts(v):
    if v is None:
        return 0.0
    if isinstance(v, datetime):
        return v.timestamp()
    return float(v)


def fold_small(groups, min_n=MIN_FLOW, limit=None):
    """Группы [{…, n}] по убыванию n; меньше min_n и сверх limit — в «ещё N».

    Возвращает (показанные, сколько людей свёрнуто)."""
    ordered = sorted(groups, key=lambda g: (-g["n"], str(g.get("key"))))
    shown, rest = [], 0
    for g in ordered:
        if g["n"] < min_n or (limit is not None and len(shown) >= limit):
            rest += g["n"]
        else:
            shown.append(g)
    return shown, rest


def question_status(askers, accepted, share=ACCEPT_SHARE, opened_at=None, now=None):
    """Статус вопроса по принятым ответам.

    askers: множество спросивших (автор вопроса + «у меня тот же вопрос»).
    accepted: {user_id: answer_node_id} — чей ответ кто принял сейчас; в счёт
    идут только спросившие. «Ответ найден», если один ответ принят долей не
    ниже share, иначе «открыт N дней».
    """
    n = len(askers)
    per_answer = defaultdict(int)
    for uid, ans in accepted.items():
        if uid in askers and ans is not None:
            per_answer[ans] += 1
    best, best_n = None, 0
    for ans, cnt in sorted(per_answer.items()):
        if cnt > best_n:
            best, best_n = ans, cnt
    found = n > 0 and best_n / n >= share
    out = {"askers": n, "accepted": best_n, "answer_id": best if found else None,
           "status": "found" if found else "open",
           "not_satisfied": n - best_n if found else None}
    if not found and opened_at is not None:
        now = now or datetime.now(timezone.utc)
        out["open_days"] = max(0, (now - opened_at).days)
    return out


# ------------------------------------------------------------- эталон из журналов
def replay(stance_rows, exposure_rows, members, objections, node_meta,
           events=(), until=None, k=TOP_K):
    """Все числа позиций, пересчитанные из журналов с нуля.

    stance_rows: строки stance_log по id — {id, user_id, topic_root_id,
      from_position_id, to_position_id, cause_node_id, source, undo_of, created_at}.
    exposure_rows: строки exposures по id — {id, user_id, node_id, response, created_at}.
    members: {position_id: set(node_id)} — состав позиции.
    objections: {position_id: set(node_id)} — узлы «против»/«не доказывает» на состав.
    node_meta: {node_id: {poi, created_at}}.
    events: события позиций [{kind, position_id, payload, created_at}] — нужен
      только раскол (пометка «не уточнил»).
    until: считать на этот момент (включительно); None — на сейчас.

    Возвращает {"positions": {pid: {in_now, stood, unchecked, converted,
    ever, unclarified, top}}, "current": {(user, topic): pid}}.
    """
    def upto(rows):
        return [r for r in rows if until is None or r["created_at"] <= until]

    stance_rows = upto(stance_rows)
    exposure_rows = upto(exposure_rows)
    events = upto(list(events))

    undone = {r["undo_of"] for r in stance_rows
              if r["source"] == "undo" and r.get("undo_of")}
    current = {}
    leavers = defaultdict(set)
    out_counts = defaultdict(lambda: defaultdict(int))
    # Порядок — по id, а не по времени: строки пишутся под замком обсуждения,
    # и id монотонен в порядке применения. created_at берётся в начале
    # транзакции и у двух одновременных записей может идти наоборот.
    for r in sorted(stance_rows, key=lambda r: r["id"]):
        key = (r["user_id"], r["topic_root_id"])
        if r["to_position_id"] is None:
            current.pop(key, None)
        else:
            current[key] = r["to_position_id"]
        counted = (r["source"] == PERSUASION and r["id"] not in undone)
        if counted and r["from_position_id"] is not None:
            leavers[r["from_position_id"]].add(r["user_id"])
            if r.get("cause_node_id"):
                out_counts[r["from_position_id"]][r["cause_node_id"]] += 1
    # «Не уточнил»: стоит в расколотой позиции. Войти в расколотую нельзя —
    # там остаются только те, кто был в ней в момент раскола и ещё не выбрал.
    split_pids = {e["position_id"] for e in events if e["kind"] == "split"}
    unclarified = {key for key, pid in current.items() if pid in split_pids}

    state = {}
    for e in sorted(exposure_rows, key=lambda r: r["id"]):
        ukey = (e["user_id"], e["node_id"])
        if e["response"] == "shown":
            state.setdefault(ukey, "shown")
        elif e["response"] == "withdrawn":
            state.pop(ukey, None)
        else:
            state[ukey] = e["response"]

    in_pos = defaultdict(set)
    for (uid, _topic), pid in current.items():
        in_pos[pid].add(uid)

    out = {}
    for pid in set(members) | set(in_pos) | set(leavers):
        cands = [{"id": n, **node_meta.get(n, {})}
                 for n in objections.get(pid, ()) if n not in members.get(pid, ())]
        top = rank_objections(cands, out_counts[pid], k)
        people = in_pos.get(pid, set())
        stood = {u for u in people
                 if any(state.get((u, n)) in STOOD for n in top)}
        converted = leavers[pid] - people
        out[pid] = {
            "in_now": len(people),
            "stood": len(stood),
            "unchecked": len(people) - len(stood),
            "converted": len(converted),
            "ever": len(people) + len(converted),
            "unclarified": sum(1 for key in unclarified
                               if current.get(key) == pid),
            "top": top,
        }
    return {"positions": out, "current": current}


def days_between(first, last):
    d = first
    while d <= last:
        yield d
        d += timedelta(days=1)


def day_end(d):
    """Конец дня d по UTC — снимок «на этот день» считается включительно."""
    return datetime(d.year, d.month, d.day, 23, 59, 59, 999999, tzinfo=timezone.utc)


def daily_series(stance_rows, exposure_rows, members, objections, node_meta,
                 events=(), first=None, last=None, k=TOP_K):
    """Дневные снимки {date: {pid: stats}} — для «состава во времени».

    Состав позиций и рёбра берутся текущими: журнал хранит переходы людей, а не
    историю рёбер, поэтому снимок прошлого дня — это люди того дня на
    сегодняшней раскладке доводов.
    """
    stamps = [r["created_at"] for r in stance_rows] + \
             [r["created_at"] for r in exposure_rows]
    if not stamps:
        return {}
    first = first or min(stamps).astimezone(timezone.utc).date()
    last = last or max(stamps).astimezone(timezone.utc).date()
    series = {}
    for d in days_between(first, last):
        series[d] = replay(stance_rows, exposure_rows, members, objections,
                           node_meta, events, until=day_end(d), k=k)["positions"]
    return series


def flows(stance_rows, position_id, since=None, min_n=MIN_FLOW, limit=4):
    """«Пришли / ушли» за период, по второй позиции, с главным доводом.

    Считаются только переходы, сделанные самим человеком и не отменённые.
    Главный довод группы — самая частая названная причина. Возвращает
    {"in": {...}, "out": {...}}, в каждом: groups, rest (свёрнуто в «ещё N»),
    total.
    """
    undone = {r["undo_of"] for r in stance_rows
              if r["source"] == "undo" and r.get("undo_of")}
    res = {}
    for side, mine, other in (("in", "to_position_id", "from_position_id"),
                              ("out", "from_position_id", "to_position_id")):
        groups = defaultdict(lambda: {"n": 0, "causes": defaultdict(int)})
        for r in stance_rows:
            if r[mine] != position_id or r["source"] != PERSUASION:
                continue
            if r["id"] in undone:
                continue
            if since is not None and r["created_at"] < since:
                continue
            g = groups[r[other]]
            g["n"] += 1
            if r.get("cause_node_id"):
                g["causes"][r["cause_node_id"]] += 1
        items = []
        for other_pid, g in groups.items():
            cause = None
            if g["causes"]:
                cause = sorted(g["causes"].items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
            items.append({"key": other_pid, "position_id": other_pid,
                          "n": g["n"], "cause_node_id": cause})
        shown, rest = fold_small(items, min_n, limit)
        res[side] = {"groups": shown, "rest": rest,
                     "total": sum(i["n"] for i in items)}
    return res


def movers(stance_rows, position_id, limit=5):
    """«Что сдвигало людей»: узлы по числу переходов, где они названы причиной,
    раздельно «привёл сюда» и «увёл отсюда». PoI на порядок не влияет."""
    undone = {r["undo_of"] for r in stance_rows
              if r["source"] == "undo" and r.get("undo_of")}
    cnt = defaultdict(int)
    for r in stance_rows:
        if r["source"] != PERSUASION or r["id"] in undone or not r.get("cause_node_id"):
            continue
        if r["to_position_id"] == position_id:
            cnt[(r["cause_node_id"], "in")] += 1
        if r["from_position_id"] == position_id:
            cnt[(r["cause_node_id"], "out")] += 1
    items = [{"node_id": n, "direction": d, "n": c} for (n, d), c in cnt.items()]
    items.sort(key=lambda i: (-i["n"], i["node_id"], i["direction"]))
    return items[:limit]
