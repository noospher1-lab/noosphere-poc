"""Баллы за участие — правила начисления (vault: decisions/2026-09-21-participation-points).

Зачем вообще: первым тестерам нужен видимый след их работы. Балл — не оценка
качества мысли (это PoI, он живёт у текста и ничего не даёт автору) и не вес
голоса (кейс-74: как только балл конвертируется во власть, выгодно прогонять
всё через внешний ИИ). Балл говорит одно: человек здесь работал, и вот сколько.

Начисляем «активность + вехи» (решение Alex 21.09): небольшие очки за действия
с дневным потолком, плюс разовые вехи за то, что случается один раз. Потолок —
единственная защита от накрутки количеством: десять доводов подряд в один день
не дают в десять раз больше, а одиннадцатый не даёт ничего.

Модуль чистый: ни базы, ни сети. На вход — запись из лога событий (`events`,
append-only), на выход — начисление. Значит баллы полностью переигрываемы:
правила поменялись → журнал пересобирается из того же лога (`points_rebuild`),
а не мигрируется руками. Это тот же принцип, что у PoI (poiformula.py).

Связанное: `db.points_sync` (SQL и журнал), `main.py: /api/points/*`.
"""

from datetime import timedelta

# ── Действия ────────────────────────────────────────────────────────────────
# (баллы, сколько таких начислений в день максимум). Вопрос стоит вдвое дешевле
# довода — ровно как в формуле PoI (вклад 0.5 против 1.0): спросить полезно,
# но это не то же, что ответить.
ACTIONS = {
    "argument":        (10, 5),   # довод или ответ в ветке
    "question":        (5,  5),   # вопрос / уточнение
    "problem":         (15, 2),   # завёл проблему (корень обсуждения)
    "mark":            (2, 10),   # за/против на доводе
    "stance":          (2,  5),   # за/против на позиции
    "vote":            (5,  3),   # голос в голосовании
    "concession":      (15, 3),   # признал часть чужого довода
    "problem_link":    (15, 3),   # связал проблемы «Б порождает А»
    "intervention":    (10, 3),   # предложение в реестр решений
    "trainer":         (20, 1),   # закончил сессию тренажёра
}

# Событие лога → вид начисления. Разбор сложных случаев — в classify().
_SIMPLE = {
    "reaction_set":       "mark",
    "position_vote_set":  "stance",
    "vote_cast":          "vote",
    "concession_added":   "concession",
    "problem_link_added": "problem_link",
    "intervention_added": "intervention",
    "dialogue_poi_set":   "trainer",
}

# Снятое и удалённое не должно оставлять баллы: правило часа позволяет убрать
# своё высказывание, пока нет ответа (decisions/edit-delete-window), и балл
# обязан уйти вместе с ним — иначе «написал и снял» становится фермой очков.
# Значение — поле payload, по которому ищем исходное начисление.
REVERSALS = {
    "node_retracted":         "node_id",
    "node_deleted":           "node_id",
    "intervention_retracted": "intervention_id",
    "intervention_deleted":   "intervention_id",
}


def classify(ev):
    """Событие лога → (вид, баллы, дневной потолок, ключ объекта) или None.

    `ключ объекта` — то, чем начисление привязано к узлу/записи, чтобы снятие
    могло его найти и отменить. None — начисление неотменяемое (голос, отметка).
    """
    kind = ev["type"]
    p = ev.get("payload") or {}

    if kind == "node_added":
        # Атомы разбора не в счёт: их нарезает ИИ из одного текста автора, и
        # платить за каждый кусок значило бы платить за длину.
        if p.get("atom_group"):
            return None
        node_id = p.get("node_id")
        if node_id is not None and node_id == p.get("topic_root_id"):
            what = "problem"
        elif p.get("kind") == "question":
            what = "question"
        else:
            what = "argument"
        points, cap = ACTIONS[what]
        return (what, points, cap, ("node_id", node_id))

    what = _SIMPLE.get(kind)
    if what is None:
        return None
    points, cap = ACTIONS[what]
    ref = None
    if kind == "intervention_added":
        ref = ("intervention_id", p.get("intervention_id"))
    elif kind == "concession_added":
        ref = ("node_id", p.get("node_id"))
    return (what, points, cap, ref)


# ── Вехи ────────────────────────────────────────────────────────────────────
# Разовые, порядок в списке = порядок показа в кабинете. Смысл вех не в сумме,
# а в подсказке «что тут вообще можно делать»: список читается как маршрут
# новичка. Две последние веху дают за то, что нельзя накрутить в одиночку —
# за чужой ответ на твой текст.
MILESTONES = [
    {"key": "trainer_done",   "points": 50, "section": "trainer",
     "title": "Пройден тренажёр",
     "hint":  "Разобрать один вопрос в разговоре с ИИ",
     "test":  lambda s: s["trainer"] >= 1},
    {"key": "first_argument", "points": 25,
     "title": "Первый довод",
     "hint":  "Ответить в чужой ветке или начать свою",
     "test":  lambda s: s["arguments"] >= 1},
    {"key": "first_problem",  "points": 40,
     "title": "Первая своя проблема",
     "hint":  "Завести проблему, которой здесь ещё нет",
     "test":  lambda s: s["problems"] >= 1},
    {"key": "first_vote",     "points": 15, "section": "votes",
     "title": "Первый голос",
     "hint":  "Проголосовать в любом голосовании",
     "test":  lambda s: s["votes"] >= 1},
    {"key": "first_concession", "points": 30,
     "title": "Первая уступка",
     "hint":  "Признать часть довода, с которым споришь",
     "test":  lambda s: s["concessions"] >= 1},
    {"key": "first_link",     "points": 30,
     "title": "Первая связь проблем",
     "hint":  "Показать, что одна проблема порождает другую",
     "test":  lambda s: s["problem_links"] >= 1},
    {"key": "ten_arguments",  "points": 50,
     "title": "Десять доводов",
     "hint":  "Десять доводов или вопросов в графе",
     "test":  lambda s: s["arguments"] + s["questions"] >= 10},
    {"key": "streak_five",    "points": 50,
     "title": "Пять дней подряд",
     "hint":  "Появляться и работать пять дней кряду",
     "test":  lambda s: s["streak"] >= 5},
    {"key": "answered",       "points": 40,
     "title": "Тебе ответили",
     "hint":  "Кто-то другой ответил на твой довод",
     "test":  lambda s: s["replies_received"] >= 1},
    {"key": "discussion",     "points": 50,
     "title": "Пошло обсуждение",
     "hint":  "Под твоей проблемой пять доводов от других людей",
     "test":  lambda s: s["problem_replies"] >= 5},
]

MILESTONE_POINTS = {m["key"]: m["points"] for m in MILESTONES}


def earned(stats):
    """Ключи вех, которые заслужены при таком состоянии счётчиков."""
    return [m["key"] for m in MILESTONES if m["test"](stats)]


def milestone_view(stats, done, hidden=()):
    """Список вех для кабинета: что взято, что осталось.

    Веха закрытого раздела (`hidden`) из списка уходит — но только пока она не
    взята: кто успел пройти тренажёр до того, как вход закрыли, должен видеть
    свою веху, а не потерять её вместе с разделом.
    """
    got = set(done)
    hide = set(hidden)
    return [{"key": m["key"], "title": m["title"], "hint": m["hint"],
             "points": m["points"], "done": m["key"] in got}
            for m in MILESTONES
            if m["key"] in got or m.get("section") not in hide]


def max_streak(days):
    """Самая длинная череда идущих подряд дней (дни могут повторяться)."""
    uniq = sorted(set(days))
    best = run = 0
    prev = None
    for d in uniq:
        run = run + 1 if prev is not None and d - prev == timedelta(days=1) else 1
        prev = d
        best = max(best, run)
    return best
