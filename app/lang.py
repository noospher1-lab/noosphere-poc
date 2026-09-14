"""
Язык, на котором пишет автор, — для правила «отвечай на языке автора» в
промптах разбора и компаньона (vault: decisions/2026-09-14-reply-language).

Модель сама не справляется: черновик в запросе — один абзац среди узла, ветки
и соседей на другом языке, и она отвечает на языке окружения. Поэтому язык
определяет код — без модели и без внешних библиотек. Различать надо прежде
всего русский и украинский, а у них это выходит по буквам и частым словам.

Первая версия (14.09, утро) смотрела только на буквы-метки і/ї/є/ґ против
ы/э/ё и без них молчала. Но русская фраза без ы/э/ё — обычное дело
(«предлагаю уволить всех депутатов»), и модель снова ушла в украинский.
Теперь отсутствие меток — тоже улика: в украинском тексте і/ї/є — около 7%
букв, в русском ы/э/ё — около 2%, так что кириллица длиной в пару слов совсем
без меток куда вероятнее русская. Считается логарифм отношения правдоподобий
«русский : украинский» по буквам, частым словам и апострофу.
"""

import math
import re

_UK_MARK = frozenset("іїєґ")
_RU_MARK = frozenset("ыэё")

# Доля букв-меток среди кириллических букв: (в русском, в украинском).
# «Чужая» метка — опечатка или цитата, поэтому не ноль.
_P_RU_MARK = (0.022, 0.0005)
_P_UK_MARK = (0.0005, 0.07)
_P_OTHER = (1 - _P_RU_MARK[0] - _P_UK_MARK[0], 1 - _P_RU_MARK[1] - _P_UK_MARK[1])
_LLR_RU_MARK = math.log(_P_RU_MARK[0] / _P_RU_MARK[1])     # ≈ +3.8
_LLR_UK_MARK = math.log(_P_UK_MARK[0] / _P_UK_MARK[1])     # ≈ −4.9
_LLR_OTHER = math.log(_P_OTHER[0] / _P_OTHER[1])           # ≈ +0.05 на букву

# Частые слова, которых нет в другом языке. Общие («так», «но», «не», «до»,
# «для», «все», «люди», «закон») не берём. Слова с буквами-метками тоже не
# нужны — их и так посчитают буквы.
_RU_WORDS = frozenset("""
и что чтобы это этот эта эти как да нет или уже еще если только тоже также
когда где почему потому который которая которое которые будет может нужно
можно надо очень сейчас его ее меня мне они он она оно себя всех всего между
из от под против сделать сделай согласен согласна предлагаю хорошо конечно
спасибо сколько человек государство власть страна с
""".split())
_UK_WORDS = frozenset("""
й та що щоб щось як який яка яке чи але або вже ще якщо тільки також коли де
чому тому буде може треба потрібно можна дуже зараз його мене вони він вона
воно між від під проти зробити зроби згоден згодна пропоную добре звісно
дякую скільки людина держава влада країна мова мови мову нехай з
""".split())
_WORD_LLR = 2.5

# Формы, которые выдают язык и у незнакомого слова: украинские «голосують»,
# «рішення», «український»/«людського», русские «другие», «решение».
_UK_FORM = re.compile(r"(?:ють|ння)\b|[сц]ьк")
_RU_FORM = re.compile(r"\w{2,}ие\b")
_FORM_LLR = 2.0

# Апостроф внутри слова — украинская орфография (пам'ять, м'ясо).
_APOSTROPHE = re.compile(r"[а-яіїєґ]['’ʼ][а-яіїєґ]")
_APOSTROPHE_LLR = 4.0

# Буквы других кириллических языков (белорусский, сербский, македонский,
# казахский…) — их не различаем, чтобы не назвать русским.
_OTHER_CYRILLIC = frozenset("ўјљњћђџѓќѕәғқңөұүһ")

_EN_WORDS = frozenset("""
the and is are was were be been of to in that this it not for with you have has
will would should can could they we what why how which there their
""".split())

_WORD = re.compile(r"[^\W\d_]+(?:['’ʼ][^\W\d_]+)*")

# Порог уверенности: ~3,3 к 1. Ниже — язык не называем.
_THRESHOLD = 1.2


def _is_cyrillic(ch):
    return "а" <= ch <= "я" or ch in "ёіїєґ"


def detect_language(text):
    """'Russian' | 'Ukrainian' | 'English' | None (не уверены или другой язык)."""
    t = (text or "").lower()
    if any(ch in _OTHER_CYRILLIC for ch in t):
        return None
    words = _WORD.findall(t)
    cyr = sum(_is_cyrillic(ch) for ch in t)
    latin = sum(ch.isascii() and ch.isalpha() for ch in t)

    if latin > cyr:
        en = sum(w in _EN_WORDS for w in words)
        return "English" if en >= 2 or (en and en * 5 >= len(words)) else None
    if not cyr:
        return None

    ru_marks = sum(ch in _RU_MARK for ch in t)
    uk_marks = sum(ch in _UK_MARK for ch in t)
    # «ъ» без ы/э — скорее болгарский, чем русский
    if "ъ" in t and not ru_marks:
        return None

    llr = (ru_marks * _LLR_RU_MARK + uk_marks * _LLR_UK_MARK
           + (cyr - ru_marks - uk_marks) * _LLR_OTHER)
    llr += _WORD_LLR * (sum(w in _RU_WORDS for w in words)
                        - sum(w in _UK_WORDS for w in words))
    llr -= _APOSTROPHE_LLR * len(_APOSTROPHE.findall(t))
    llr += _FORM_LLR * (len(_RU_FORM.findall(t)) - len(_UK_FORM.findall(t)))

    if llr > _THRESHOLD:
        return "Russian"
    if llr < -_THRESHOLD:
        return "Ukrainian"
    return None


def author_language(draft, history=None):
    """Язык автора: сначала по черновику; не хватило уверенности — по черновику
    вместе с репликами АВТОРА в разговоре. Реплики компаньона не в счёт: они
    могли уйти не на тот язык, и ровно так ошибка воспроизводила себя."""
    lang = detect_language(draft)
    if lang is None and history:
        own = " ".join(h.get("text", "") for h in history if h.get("role") == "author")
        lang = detect_language(f"{draft or ''} {own}")
    return lang
