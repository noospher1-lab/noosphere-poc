"""
Схемы рассуждения и их проверочные вопросы (vault: decisions/2026-09-15-argument-schemes).

По Дугласу Уолтону (Walton, Reed, Macagno, «Argumentation Schemes», 2008): у
довода обычно есть узнаваемый вид — «это вызвано тем-то», «надо сделать X ради
Y», «там так сделали», «так говорит эксперт», — и у каждого вида свой короткий
список критических вопросов, на которых такие доводы ломаются. Из десятков схем
взяты восемь, которые встречаются в спорах о проблемах и решениях.

Как используется: разбор черновика называет схему, и его единственный вопрос
автору (think) — самый важный НЕОТВЕЧЕННЫЙ вопрос этой схемы, а не общий. Вопросы
— подсказка, не ворота: публикации ничто не мешает. Компаньон знает те же списки.
Вопросы на английском — это часть запроса модели; автору модель задаёт их на его
языке. Названия — по-русски, для интерфейса.
"""

SCHEMES = {
    "cause": {
        "name": "от причины к следствию",
        "what": "claims that one thing produces or explains another",
        "questions": [
            "Is there actually a correlation between the supposed cause and the effect?",
            "Could the link be coincidence?",
            "Is there a third factor that produces both?",
            "Could the causation run the other way?",
            "Is the mechanism by which the cause produces the effect stated?",
        ],
    },
    "practical": {
        "name": "сделать X ради цели",
        "what": "argues that an action should be taken because it achieves a goal",
        "questions": [
            "Is the goal stated, and is it shared by those affected?",
            "Will the action actually achieve the goal — by what mechanism?",
            "Are there alternative actions that achieve it better or at lower cost?",
            "Is it feasible — who does it, with what resources, on what path?",
            "What side effects or costs follow, and do they conflict with other goals?",
        ],
    },
    "consequences": {
        "name": "от последствий",
        "what": "argues for or against something by its good or bad consequences",
        "questions": [
            "How likely are the stated consequences, and what supports that they will follow?",
            "Are there opposite consequences that should be weighed against them?",
            "For whom are the consequences good or bad?",
        ],
    },
    "precedent": {
        "name": "по аналогии или прецеденту",
        "what": "argues from what happened in another place, time or case",
        "questions": [
            "Are the two cases similar in the respects that matter?",
            "Are there relevant differences in conditions — country, time, scale, institutions?",
            "What was the actual outcome there, and is there a source for it?",
            "Are there counter-examples where the same thing failed?",
        ],
    },
    "expert": {
        "name": "ссылка на эксперта или источник",
        "what": "rests a claim on what an expert, study or authority says",
        "questions": [
            "Is the source an expert in this particular field?",
            "What exactly did the source claim, and where?",
            "Do other experts or sources agree?",
            "Does the source have an interest in the conclusion?",
        ],
    },
    "evidence": {
        "name": "от данных и признаков",
        "what": "infers a conclusion from data, statistics or observed signs",
        "questions": [
            "Are the data reliable, and is their source given?",
            "Do the data point to this conclusion rather than to another explanation?",
            "Are the cases representative, or picked to fit?",
        ],
    },
    "values": {
        "name": "от ценностей",
        "what": "argues that something is right or wrong because of a value it serves or violates",
        "questions": [
            "Which value exactly is at stake, and for whom?",
            "Does the value really require this conclusion?",
            "Which competing values are sacrificed, and why does this one weigh more here?",
        ],
    },
    "slope": {
        "name": "скользкий склон",
        "what": "argues that a first step will lead through a chain to a bad end",
        "questions": [
            "Is each step of the chain actually likely to follow from the previous one?",
            "What would stop the slide at some step?",
        ],
    },
}


def clean(scheme_id):
    """id схемы из ответа модели, если он из каталога; иначе None."""
    s = str(scheme_id or "").strip().lower()
    return s if s in SCHEMES else None


def name(scheme_id):
    return SCHEMES[scheme_id]["name"] if scheme_id in SCHEMES else ""


def prompt_block():
    """Каталог схем для запроса модели — один и тот же у разбора и компаньона."""
    lines = []
    for sid, s in SCHEMES.items():
        lines.append(f"- {sid}: {s['what']}. Critical questions:")
        lines.extend(f"    * {q}" for q in s["questions"])
    return (
        "REASONING SCHEMES. Most arguments follow a recognisable scheme, and each "
        "scheme has critical questions on which such arguments typically break. "
        "Use them to find what the draft leaves open — never as a checklist to "
        "recite, and never ask a question the draft already answers:\n"
        + "\n".join(lines))


def json_field():
    return '"scheme": "' + "|".join(SCHEMES) + '|none", '
