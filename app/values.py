"""
Ценности у доводов (vault: decisions/2026-09-15-values, drafts/values-list).

Люди часто расходятся не в фактах, а в том, что важнее: «язык как рабочий
инструмент» против «это моя страна». Метка ценности делает это видимым — у довода
и в «Размежевании» (какие ценности на первом месте у каждой группы).

Выбор Alex 15.09 — совместить оба подхода: пункт из общего списка (чтобы
сравнивать между людьми, обсуждениями и языками) + формулировка своими словами
(оттенок, который список теряет). Список ЖИВОЙ: «завтра общество может стать
другим, а затем появятся и новые ценности». Поэтому:
  - id не удаляются и не меняют смысл, новые добавляются, VERSION растёт;
  - у довода хранится версия списка, по которой ставили метку;
  - формулировки хранятся всегда — по ним находятся кандидаты в новые ценности
    и по ним прошлое можно заново разложить по новому списку.
Новую ценность пока добавляет Alex, позже — голосование участников.

Версия 1 — по теории базовых ценностей Шварца, переложенной на язык споров о
проблемах; «самоуправление» добавлено под платформу управления (у Шварца его нет).
Ставит метку разбор черновика, автор видит и может сменить или снять.
"""

VERSION = 1
PHRASE_MAX = 120

VALUES = {
    "liberty": {"name": "свобода выбора",
                "meaning": "человек сам решает, как жить и говорить",
                "en": "people decide for themselves how to live, speak and act"},
    "security": {"name": "безопасность",
                 "meaning": "защищённость жизни, здоровья, страны",
                 "en": "protection of life, health and the country"},
    "fairness": {"name": "справедливость и равенство",
                 "meaning": "одинаковые права и правила для всех",
                 "en": "the same rights and rules for everyone"},
    "care": {"name": "забота о людях",
             "meaning": "помочь слабым, не навредить",
             "en": "helping the vulnerable, doing no harm"},
    "identity": {"name": "идентичность и традиция",
                 "meaning": "язык, культура, память, принадлежность",
                 "en": "language, culture, memory, belonging"},
    "order": {"name": "порядок и закон",
              "meaning": "правила соблюдаются, государство работает",
              "en": "rules are kept and the state functions"},
    "prosperity": {"name": "благосостояние",
                   "meaning": "достаток, работа, развитие",
                   "en": "income, jobs, development"},
    "tolerance": {"name": "терпимость и сосуществование",
                  "meaning": "жить рядом с теми, кто думает иначе",
                  "en": "living alongside those who think and live differently"},
    "nature": {"name": "природа и будущие поколения",
               "meaning": "среда, долгий горизонт",
               "en": "the environment and the long horizon"},
    "selfrule": {"name": "самоуправление",
                 "meaning": "право людей решать самим",
                 "en": "people's right to decide their own matters"},
    "other": {"name": "другое",
              "meaning": "не ложится ни в один пункт",
              "en": "a real value is at stake but none of the above fits"},
}


def clean(value_id):
    """id ценности из списка, иначе None."""
    s = str(value_id or "").strip().lower()
    return s if s in VALUES else None


def name(value_id):
    return VALUES[value_id]["name"] if value_id in VALUES else ""


def clean_phrase(phrase):
    return " ".join(str(phrase or "").split())[:PHRASE_MAX]


def as_list():
    return [{"id": k, "name": v["name"], "meaning": v["meaning"]} for k, v in VALUES.items()]


def prompt_block():
    lines = [f"- {k}: {v['en']}" for k, v in VALUES.items()]
    return ("VALUES. Beyond facts, arguments appeal to what matters most — people "
            "often disagree because they rank these differently. The shared list:\n"
            + "\n".join(lines))


def prompt_step():
    return ("   VALUE: which underlying VALUE the draft appeals to — what it treats "
            "as mattering most here (from the list of values), or 'none' when it "
            "makes no value appeal at all (a bare factual or procedural point). "
            "Use 'other' only when a real value is at stake and none fits. In "
            "value_phrase, name that value in a few words in the draft's language "
            "and about its subject (e.g. «право говорить на родном языке») — it "
            "keeps the shade the list loses; empty when value is 'none'.\n")


def json_fields():
    return ('"value": "' + "|".join(VALUES) + '|none", '
            '"value_phrase": "the value in a few words, or empty", ')
