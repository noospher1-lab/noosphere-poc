"""
Эмбеддинги проблем — поиск похожих по СМЫСЛУ, а не по буквам.

Зачем: подсказка дублей и схождение причин искали триграммами (pg_trgm), а
русская и украинская постановки одной проблемы общих букв почти не имеют
(«запрет/заборона», «язык/мова»): сходство 0,1 при пороге 0,15 — русская
копия украинской проблемы заводилась молча (проверено на проде 2026-09-11).
Многоязычная модель даёт той же паре 0,94, посторонней проблеме ≤ 0,25.

Модель локальная (ONNX через fastembed), без ключа и без счёта: у Anthropic
эмбеддингов нет, а заводить ключ партнёра ради ста проблем — лишняя сущность.
Цена — ~240 МБ модели, которые качаются при первом старте, и ~0,5 ГБ памяти.

Всё здесь FAIL-OPEN: нет библиотеки, не скачалась модель, не успела
загрузиться — поиск работает триграммами, как раньше. Загрузка идёт в фоне
после старта, чтобы healthcheck не ждал модель.
"""

import asyncio
import hashlib
import logging
import os
import threading

log = logging.getLogger("noosphere.embed")

MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
DIM = 384
# ниже этого — не «похожая проблема», а шум; дубли на разных языках дают 0,85+
SIM_FLOOR = 0.6
# порог для ДОВОДОВ ниже: вопрос и утверждение об одном и том же дают ~0,5–0,75
# (замер: «Расширение дорог снижает пробки?» ↔ украинский довод 0,75, «помогает
# ли платная парковка» ↔ довод о парковках < 0,6), а кандидатов здесь читает
# модель и сервер принимает только предложенные id — ложный кандидат безвреден
NODE_SIM_FLOOR = 0.5

ENABLED = os.environ.get("NOOSPHERE_EMBED", "1") != "0"
CACHE_DIR = os.environ.get("NOOSPHERE_EMBED_CACHE") or None

_model = None
_lock = threading.Lock()
_failed = False


def _load():
    """Загрузить модель (потокобезопасно, один раз). None — если не вышло."""
    global _model, _failed
    if _model is not None or _failed or not ENABLED:
        return _model
    with _lock:
        if _model is not None or _failed:
            return _model
        try:
            from fastembed import TextEmbedding
            _model = TextEmbedding(MODEL, cache_dir=CACHE_DIR)
            log.info("модель эмбеддингов загружена: %s", MODEL)
        except Exception:
            _failed = True
            log.warning("модель эмбеддингов недоступна — поиск только триграммами",
                        exc_info=True)
    return _model


def ready():
    return _model is not None


def node_text(title, text):
    """Что именно кладётся в вектор: заголовок (у корня) + начало текста."""
    return ((title or "").strip() + ". " + (text or "").strip()[:1000]).strip(". ")


problem_text = node_text          # старое имя — вектор проблемы считается так же


def text_hash(s):
    return hashlib.sha256((s or "").encode("utf-8")).hexdigest()[:16]


def embed_sync(texts):
    """Векторы для списка текстов; [] если модель недоступна."""
    m = _load()
    if m is None or not texts:
        return []
    return [[float(x) for x in v] for v in m.embed(list(texts))]


async def embed(texts):
    if not ENABLED:
        return []
    return await asyncio.to_thread(embed_sync, texts)


async def query_vector(text):
    """Вектор запроса — только если модель УЖЕ загружена: подсказка не должна
    ждать полминуты, пока модель качается при первом старте."""
    if not ENABLED or not ready() or not (text or "").strip():
        return None
    vs = await asyncio.to_thread(embed_sync, [text])
    return vs[0] if vs else None


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def warm():
    """Прогрев в фоне после старта: загрузить модель, не трогая healthcheck."""
    _load()
