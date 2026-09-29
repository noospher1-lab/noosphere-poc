"""
Сведения — слой обсуждения (vault: decisions/2026-09-28-info-sector).

Alex 28.09: «мы никого не консультируем, а собираем доступную информацию, а
люди уже ей распоряжаются как хотят». И следом: «делай сведения слоем
обсуждения». Отсюда устройство:

  - СВЕДЕНИЕ живёт под корнем обсуждения (topic_root_id), как реестр и
    масштаб: у любого обсуждения могут быть сведения, и у каждого сведения
    есть место в споре — довод ссылается на него (#id) и оспаривает его.
    Раньше был отдельный «сектор» с одним обсуждением и своей копией тех же
    фактов, что в «Масштабе», — одна мысль висела в двух местах.
  - «МАСШТАБ» проблемы — это раздел сведений с подписью «Масштаб»: строка
    «регион — цифра — источник» и сведение — одно и то же. Старые строки
    problem_scale переносятся сюда при старте (migrate_scale), карточка
    проблемы читает их отсюда же (db.get_problem).
  - Не вердикт «правда/ложь», а на что опирается. Три вида (Alex 28.09):
      норма  — текст закона или официальное разъяснение ведомства, цитата
               сверена с источником;
      сообщают — СМИ, юристы, помогающие организации: пересказ со ссылкой;
      опыт людей — то, что внесли сами люди; у опыта всегда есть страна,
               и опирается он на число независимых подтверждений.
  - РАЗДЕЛ — подпись, а не список из кода: у обсуждения о защите это
    «Продление защиты», у обсуждения о пробках — своё. Порядок — сначала
    закон и права, потом масштаб, потом остальное (Alex 28.09: «сначала
    закон… потом по каждой стране или городу»).
  - Сверка цитаты — механическая, без ИИ: сервер скачивает страницу и ищет
    выдержку дословно. ИИ здесь не судья, а редактор (разбор текста в поля).
  - Свежесть выводится: checked_at + recheck_days < сегодня. Ничего не
    копится флагами.
"""

import html
import ipaddress
import json
import re
import socket
import urllib.request
from datetime import datetime, timezone

from . import antiabuse, db, taxonomy

# Порядок разделов, если такие подписи есть в обсуждении; остальные — следом,
# по первому появлению. Первые два — «сначала закон» (Alex 28.09).
SCALE_SECTION = "Масштаб"
SECTION_ORDER = ["Закон-основание", "Права и равенство", SCALE_SECTION,
                 "Продление защиты", "Переход на вид на жительство",
                 "Оспаривание в суде"]
# что предложить в форме, если в обсуждении разделов ещё нет
DEFAULT_SECTIONS = ["Закон-основание", "Права и равенство", SCALE_SECTION, "Что делать"]
_OLD_SECTION = {"basis": "Закон-основание", "rights": "Права и равенство",
                "protection": "Продление защиты", "residence": "Переход на вид на жительство",
                "court": "Оспаривание в суде", "other": "Другое"}
KINDS = ("norm", "report", "experience")
QUOTE_STATUSES = ("verified", "mismatch", "unreachable", "unchecked", "none")

# лимиты от вбросов (Alex 28.09: «будем мониторить и ограничивать лимиты»)
FACTS_PER_DAY = 20
REPORTS_PER_DAY = 100
# ключ advisory-замка «действия одного аккаунта»: без него два параллельных
# запроса оба видят «19 из 20» и оба проходят (ревью 28.09, С-5)
AUTHOR_LOCK = 7101


class InfoError(Exception):
    """Ошибка ввода — уходит в ответ API как есть."""


SCHEMA = [
    # Осталась только как адрес старых ссылок /info.html?s=ua-eu → обсуждение.
    """
    CREATE TABLE IF NOT EXISTS info_sectors (
        id          SERIAL PRIMARY KEY,
        slug        TEXT UNIQUE NOT NULL,
        title       TEXT NOT NULL,
        intro       TEXT,
        problem_id  INTEGER REFERENCES nodes(id) ON DELETE SET NULL,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    # country NULL — сведение для всех (уровень ЕС или международный).
    # Страна — из той же таксономии, что у тем: иначе «Германия» и «ФРГ»
    # разъехались бы в две страны на карте. place_note — место, которое не
    # страна («Весь мир (консульства Украины)», «Евросоюз») — так было в
    # строках масштаба.
    """
    CREATE TABLE IF NOT EXISTS info_facts (
        id            SERIAL PRIMARY KEY,
        topic_root_id INTEGER REFERENCES nodes(id) ON DELETE CASCADE,
        sector_id     INTEGER REFERENCES info_sectors(id) ON DELETE SET NULL,
        section       TEXT NOT NULL,
        country       TEXT,
        city          TEXT,
        office        TEXT,
        place_note    TEXT,
        applies_to    TEXT,
        kind          TEXT NOT NULL,
        title         TEXT NOT NULL,
        body          TEXT,
        when_text     TEXT,
        source_url    TEXT,
        source_title  TEXT,
        source_quote  TEXT,
        quote_status  TEXT NOT NULL DEFAULT 'unchecked',
        checked_at    TIMESTAMPTZ,
        recheck_days  INTEGER NOT NULL DEFAULT 30,
        ord           INTEGER NOT NULL DEFAULT 0,
        author_id     INTEGER REFERENCES authors(id) ON DELETE SET NULL,
        created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
        deleted_at    TIMESTAMPTZ
    )
    """,
    # стенд 28.09: сведения жили в «секторе» — переезжают под обсуждение
    "ALTER TABLE info_facts ADD COLUMN IF NOT EXISTS topic_root_id INTEGER "
    "REFERENCES nodes(id) ON DELETE CASCADE",
    "ALTER TABLE info_facts ADD COLUMN IF NOT EXISTS place_note TEXT",
    "ALTER TABLE info_facts ALTER COLUMN sector_id DROP NOT NULL",
    """
    UPDATE info_facts f SET topic_root_id = s.problem_id
    FROM info_sectors s
    WHERE f.sector_id = s.id AND f.topic_root_id IS NULL AND s.problem_id IS NOT NULL
    """,
    """
    UPDATE info_facts SET section = CASE section
        WHEN 'basis' THEN 'Закон-основание' WHEN 'rights' THEN 'Права и равенство'
        WHEN 'protection' THEN 'Продление защиты'
        WHEN 'residence' THEN 'Переход на вид на жительство'
        WHEN 'court' THEN 'Оспаривание в суде' ELSE 'Другое' END
    WHERE section IN ('basis', 'rights', 'protection', 'residence', 'court', 'other')
    """,
    "CREATE INDEX IF NOT EXISTS info_facts_topic ON info_facts (topic_root_id, country)",
    # переход со старых видов (стенд 28.09): статьи и разборы — «сообщают»,
    # то, что внесли люди, — «опыт людей»
    """
    UPDATE info_facts f SET kind = CASE
        WHEN f.kind = 'practice' AND NOT COALESCE(a.is_service, FALSE) THEN 'experience'
        ELSE 'report' END
    FROM authors a WHERE a.id = f.author_id AND f.kind IN ('practice', 'unverified')
    """,
    # «у меня так же / у меня иначе». Один голос на человека и сведение —
    # повторное нажатие меняет, а не добавляет.
    """
    CREATE TABLE IF NOT EXISTS info_reports (
        id          SERIAL PRIMARY KEY,
        fact_id     INTEGER NOT NULL REFERENCES info_facts(id) ON DELETE CASCADE,
        author_id   INTEGER NOT NULL REFERENCES authors(id) ON DELETE CASCADE,
        verdict     TEXT NOT NULL CHECK (verdict IN ('same', 'differs')),
        note        TEXT,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (fact_id, author_id)
    )
    """,
    # куда переехала строка масштаба (идемпотентность переноса)
    "ALTER TABLE problem_scale ADD COLUMN IF NOT EXISTS moved_to_fact INTEGER",
]

WIPE_TABLES = "info_reports, info_facts, info_sectors"


# ------------------------------------------------------------ сверка цитаты

_QUOTES = str.maketrans({"«": '"', "»": '"', "“": '"', "”": '"', "„": '"', "‟": '"',
                         "’": "'", "‘": "'", "ʼ": "'", "`": "'", "′": "'",
                         "–": "-", "—": "-", "‑": "-", "−": "-",
                         " ": " ", " ": " ", " ": " "})


def normalize(s: str) -> str:
    """Для сравнения: без регистра, типографских кавычек и тире, мягких
    переносов и лишних пробелов. Слова и порядок остаются — это и есть
    «дословно»."""
    s = (s or "").replace("­", "").translate(_QUOTES).lower()
    s = re.sub(r"\s+", " ", s)
    # «interruption .» — пробел перед точкой остаётся от закрывающего тега
    # (</strong>.) и не меняет слов; без этого дословная цитата «не находится»
    return re.sub(r" (?=[.,;:!?)\]])", "", s).strip()


def html_to_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style|noscript|svg)\b.*?</\1>", " ", raw)
    raw = re.sub(r"(?s)<!--.*?-->", " ", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    return html.unescape(raw)


_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_NAT64 = ipaddress.ip_network("64:ff9b::/96")


def _bad_ip(ip) -> bool:
    ip = ipaddress.ip_address(ip)
    if ip.version == 6:
        if ip.ipv4_mapped is not None:
            return _bad_ip(ip.ipv4_mapped)
        if ip in _NAT64:
            return _bad_ip(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
    return (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
            or ip.is_multicast or ip.is_unspecified or ip in _CGNAT)


def _public_host(url: str) -> bool:
    """Только внешние адреса: сервер не должен ходить по ссылке пользователя
    во внутреннюю сеть (Railway, localhost, метаданные облака)."""
    from urllib.parse import urlparse
    try:
        u = urlparse(url)
        host, port = u.hostname, u.port
    except ValueError:
        return False
    if u.scheme not in ("http", "https") or not host:
        return False
    try:
        infos = socket.getaddrinfo(host, port or (443 if u.scheme == "https" else 80))
    except OSError:
        return False
    return not any(_bad_ip(info[4][0]) for info in infos)


class _PeerGuard(Exception):
    pass


def _check_peer(sock):
    """Второй замок — после соединения. Имя резолвится дважды (проверка и
    соединение), и злонамеренный DNS может во второй раз вернуть внутренний
    адрес (DNS-rebinding, ревью 28.09, С-1). Здесь проверяется адрес, к
    которому соединились на самом деле."""
    if _bad_ip(sock.getpeername()[0]):
        sock.close()
        raise _PeerGuard("соединение с внутренним адресом")


import http.client  # noqa: E402


class _GuardHTTP(http.client.HTTPConnection):
    def connect(self):
        super().connect()
        _check_peer(self.sock)


class _GuardHTTPS(http.client.HTTPSConnection):
    def connect(self):
        super().connect()
        _check_peer(self.sock)


class _GuardHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_GuardHTTP, req)


class _GuardHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_GuardHTTPS, req, context=self._context)


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    """Переадресация проверяется так же, как исходная ссылка: иначе внешний
    адрес мог бы отправить сервер во внутреннюю сеть одним ответом 302."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _public_host(newurl):
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_SafeRedirect, _GuardHTTPHandler, _GuardHTTPSHandler)

# Весь запрос к чужому сайту — не дольше этого. timeout сокета ограничивает
# одну операцию, и медленный сайт, отдающий по байту, держал бы поток
# минутами (ревью 28.09, Б-2).
FETCH_DEADLINE = 20


def _read(r, limit, deadline):
    import time
    chunks, size = [], 0
    while size < limit:
        if time.monotonic() > deadline:
            return None
        c = r.read(65536)
        if not c:
            break
        chunks.append(c)
        size += len(c)
    return b"".join(chunks)


def fetch_text(url: str, limit=3_000_000, timeout=10) -> str | None:
    """Текст страницы или None, если не достать (закрыта для роботов, PDF,
    нет сети). Синхронно — звать через asyncio.to_thread."""
    if not _public_host(url):
        return None
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; NoosphereQuoteCheck/1.0; +https://noosphere.live)",
        "Accept-Language": "uk,ru,en,de,pl,cs;q=0.8"})
    import time
    deadline = time.monotonic() + FETCH_DEADLINE
    try:
        with _OPENER.open(req, timeout=timeout) as r:
            ctype = r.headers.get("content-type", "")
            if "pdf" in ctype or url.lower().split("?")[0].endswith(".pdf"):
                # законы ЕС, ЕСПЧ и ООН часто есть только в PDF
                data = _read(r, limit * 5, deadline)
                return _pdf_text(data) if data else None
            if "html" not in ctype and "text" not in ctype and "xml" not in ctype:
                return None
            raw = _read(r, limit, deadline)
            if raw is None:
                return None
            charset = r.headers.get_content_charset() or "utf-8"
    except Exception:
        return None
    try:
        return html_to_text(raw.decode(charset, errors="replace"))
    except LookupError:
        return html_to_text(raw.decode("utf-8", errors="replace"))


def _pdf_text(data: bytes) -> str | None:
    """Текст PDF через pdftotext (poppler). Нет утилиты — None, то есть
    «сверить не удалось», а не «цитаты нет»."""
    import shutil
    import subprocess
    import tempfile
    exe = shutil.which("pdftotext")
    if not exe or not data.startswith(b"%PDF"):
        return None
    with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
        f.write(data)
        f.flush()
        try:
            out = subprocess.run([exe, "-q", "-enc", "UTF-8", f.name, "-"],
                                 capture_output=True, timeout=30).stdout.decode("utf-8", "replace")
        except Exception:
            return None
    # перенос слова по слогам в конце строки: «mili-\ntary» → «military»
    return re.sub(r"(\w)-\n(\w)", r"\1\2", out)


def check_quote(url: str | None, quote: str | None) -> str:
    """verified / mismatch / unreachable / none. Синхронно."""
    if not url or not (quote or "").strip():
        return "none"
    text = fetch_text(url)
    if text is None:
        return "unreachable"
    page = normalize(text)
    # EUR-Lex и подобные отдают роботу пустую заглушку с проверкой браузера:
    # «цитаты нет» на пустой странице было бы ложным обвинением источника
    if len(page) < 300:
        return "unreachable"
    q = normalize(quote)
    # многоточие в выдержке — пропуск: каждая часть должна найтись по порядку
    parts = [p.strip() for p in re.split(r"\s*(?:\.\.\.|…|\[\.\.\.\])\s*", q) if p.strip()]
    pos = 0
    for p in parts:
        i = page.find(p, pos)
        if i < 0:
            return "mismatch"
        pos = i + len(p)
    return "verified"


# ------------------------------------------------------------ запись
def _clean(s, n):
    s = " ".join(str(s or "").split()) if n <= 300 else str(s or "").strip()
    return s[:n] or None


def validate_fact(d: dict) -> dict:
    section = _clean(d.get("section"), 60)
    section = _OLD_SECTION.get(section, section) or "Другое"
    if len(section) < 2:
        raise InfoError("Раздел — хотя бы пара слов")
    kind = d.get("kind") or "experience"
    if kind not in KINDS:
        raise InfoError("Такого вида сведения нет — выберите из списка")
    country = _clean(d.get("country"), 80)
    if country and country not in taxonomy.COUNTRIES:
        raise InfoError("Такой страны нет в списке — выберите из списка")
    city = _clean(d.get("city"), 80)
    if city and not country:
        raise InfoError("Указан город — выберите и страну")
    # у опыта всегда есть страна: иначе случай из одного города встаёт в
    # «общее для всех» рядом с законами (UX 28.09, Б2)
    if kind == "experience" and not country:
        raise InfoError("Опыт людей всегда в какой-то стране — выберите страну")
    title = _clean(d.get("title"), 200)
    if not title:
        raise InfoError("Нужно короткое утверждение — что именно известно")
    url = _clean(d.get("source_url"), 1000)
    if url:
        from urllib.parse import urlparse
        try:
            ok = urlparse(url).scheme in ("http", "https") and bool(urlparse(url).hostname) \
                and not re.search(r"\s", url)
        except ValueError:
            ok = False
        if not ok:
            # кривая ссылка роняла страницу у всех посетителей (ревью 28.09, Б-1)
            raise InfoError("Ссылка не похожа на адрес страницы — проверьте её")
    quote = _clean(d.get("source_quote"), 2000)
    # «норма» — это текст закона; без источника она неотличима от пересказа
    if kind == "norm" and not (url and quote):
        raise InfoError("Норма — это текст закона или ведомства: нужна ссылка и дословная выдержка")
    if kind == "report" and not url:
        raise InfoError("«Сообщают» — пересказ источника: нужна ссылка на него")
    return {
        "section": section, "kind": kind, "country": country, "city": city,
        "office": _clean(d.get("office"), 200),
        "place_note": _clean(d.get("place_note"), 200),
        "applies_to": _clean(d.get("applies_to"), 200),
        "title": title, "body": _clean(d.get("body"), 4000),
        "when_text": _clean(d.get("when_text"), 100),
        "source_url": url, "source_title": _clean(d.get("source_title"), 300),
        "source_quote": quote,
        "ord": int(d.get("ord") or 0),
    }


async def topic_of(root_or_slug):
    """Корень обсуждения по номеру или по старому адресу раздела (?s=ua-eu)."""
    pool = db._pool_or_raise()
    async with pool.acquire() as conn:
        if str(root_or_slug).isdigit():
            row = await conn.fetchrow(
                "SELECT id, title, text, kind FROM nodes WHERE id = $1 "
                "AND id = topic_root_id AND deleted_at IS NULL", int(root_or_slug))
        else:
            row = await conn.fetchrow(
                "SELECT n.id, n.title, n.text, n.kind FROM info_sectors s "
                "JOIN nodes n ON n.id = s.problem_id WHERE s.slug = $1 "
                "AND n.deleted_at IS NULL", str(root_or_slug))
    if not row:
        raise InfoError("Такого обсуждения нет")
    return dict(row)


async def _check_fact_limit(conn, author_id):
    n = await conn.fetchval(
        "SELECT count(*) FROM info_facts WHERE author_id = $1 "
        "AND created_at > now() - interval '1 day'", author_id)
    if n >= FACTS_PER_DAY:
        raise InfoError(f"Не больше {FACTS_PER_DAY} сведений в сутки. Продолжить можно завтра.")


async def fact_limit_left(author_id):
    """Проверка лимита ДО сверки ссылки: сверка — сетевой запрос к чужому
    сайту, и без этой проверки аккаунт сверх лимита всё равно занимал бы
    потоки сервера (ревью 28.09, Б-2)."""
    pool = db._pool_or_raise()
    async with pool.acquire() as conn:
        await _check_fact_limit(conn, author_id)


async def _insert_fact(conn, root, f, author_id, quote_status, checked_at, recheck_days,
                       created_at=None):
    return await conn.fetchval(
        """
        INSERT INTO info_facts (topic_root_id, section, country, city, office, place_note,
            applies_to, kind, title, body, when_text, source_url, source_title,
            source_quote, quote_status, checked_at, recheck_days, ord, author_id,
            created_at)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,
                COALESCE($20, now()))
        RETURNING id
        """, root, f["section"], f["country"], f["city"], f["office"], f.get("place_note"),
        f["applies_to"], f["kind"], f["title"], f["body"], f["when_text"],
        f["source_url"], f["source_title"], f["source_quote"],
        quote_status, checked_at, recheck_days, f["ord"], author_id, created_at)


async def add_fact(root, data: dict, author_id, quote_status="unchecked",
                   checked_at=None, recheck_days=30, limit=True):
    f = validate_fact(data)
    pool = db._pool_or_raise()
    async with pool.acquire() as conn:
        async with conn.transaction():
            if not await conn.fetchval(
                    "SELECT 1 FROM nodes WHERE id = $1 AND id = topic_root_id", root):
                raise InfoError("Такого обсуждения нет")
            if limit and author_id is not None:
                await conn.execute("SELECT pg_advisory_xact_lock($1, $2)", AUTHOR_LOCK, author_id)
                await _check_fact_limit(conn, author_id)
            fid = await _insert_fact(conn, root, f, author_id, quote_status, checked_at,
                                     recheck_days)
            await db._log(conn, "info_fact_added",
                          {"id": fid, "topic_root_id": root, **f,
                           "quote_status": quote_status}, author_id)
    return fid


async def find_fact(root, title, country):
    """Сведение с тем же утверждением в той же стране — для повторного посева."""
    pool = db._pool_or_raise()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT id FROM info_facts WHERE topic_root_id = $1 AND title = $2 "
            "AND country IS NOT DISTINCT FROM $3 AND deleted_at IS NULL",
            root, title, country)


async def set_quote_status(fact_id, status):
    if status not in QUOTE_STATUSES:
        raise InfoError("неизвестный статус сверки")
    pool = db._pool_or_raise()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "UPDATE info_facts SET quote_status = $2, checked_at = now() WHERE id = $1",
                fact_id, status)
            await db._log(conn, "info_quote_checked", {"id": fact_id, "status": status})


async def remove_fact(fact_id, author_id, admin=False):
    pool = db._pool_or_raise()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT author_id, deleted_at FROM info_facts WHERE id = $1", fact_id)
            if not row or row["deleted_at"]:
                raise InfoError("Такого сведения нет — возможно, его сняли")
            if not admin and row["author_id"] != author_id:
                raise InfoError("Снять сведение может только тот, кто его внёс")
            await conn.execute("UPDATE info_facts SET deleted_at = now() WHERE id = $1", fact_id)
            await db._log(conn, "info_fact_removed", {"id": fact_id}, author_id)


async def set_report(fact_id, author_id, verdict, note=None):
    """verdict: same | differs | None (снять свою отметку)."""
    if verdict not in ("same", "differs", None):
        raise InfoError("отметка: same или differs")
    note = _clean(note, 500)
    pool = db._pool_or_raise()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock($1, $2)", AUTHOR_LOCK, author_id)
            row = await conn.fetchrow(
                "SELECT author_id, kind FROM info_facts WHERE id = $1 AND deleted_at IS NULL",
                fact_id)
            if row is None:
                raise InfoError("Такого сведения нет — возможно, его сняли")
            if row["author_id"] == author_id:
                raise InfoError("Ваше сведение отмечают другие люди")
            # у текста закона нечего подтверждать «у меня так же» (Alex 28.09)
            if row["kind"] == "norm" and verdict is not None:
                raise InfoError("Норму не отмечают — это текст закона или ведомства")
            if verdict is None:
                await conn.execute(
                    "DELETE FROM info_reports WHERE fact_id = $1 AND author_id = $2",
                    fact_id, author_id)
            else:
                n = await conn.fetchval(
                    "SELECT count(*) FROM info_reports WHERE author_id = $1 "
                    "AND created_at > now() - interval '1 day'", author_id)
                if n >= REPORTS_PER_DAY:
                    raise InfoError(f"Не больше {REPORTS_PER_DAY} отметок в сутки. Продолжить можно завтра.")
                await conn.execute(
                    """
                    INSERT INTO info_reports (fact_id, author_id, verdict, note)
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (fact_id, author_id)
                    DO UPDATE SET verdict = EXCLUDED.verdict, note = EXCLUDED.note,
                                  created_at = now()
                    """, fact_id, author_id, verdict, note)
            await db._log(conn, "info_report_set",
                          {"fact_id": fact_id, "verdict": verdict, "note": note}, author_id)


# ------------------------------------------------------------ масштаб = раздел сведений

def _country_of_region(region):
    """«Дания (вне решения ЕС)» → Дания; «Евросоюз», «Весь мир (…)» → None."""
    r = (region or "").strip()
    best = None
    for c in taxonomy.COUNTRIES:
        if r == c or r.startswith(c + " ") or r.startswith(c + ","):
            if best is None or len(c) > len(best):
                best = c
    return best


async def migrate_scale(conn):
    """Строки масштаба (problem_scale) переезжают в сведения раздела «Масштаб».
    Идемпотентно: перенесённая строка помечается moved_to_fact. Если такое же
    сведение (та же ссылка и выдержка) уже есть — второе не заводим: одна
    мысль — один узел (Alex 11.09)."""
    rows = await conn.fetch(
        "SELECT * FROM problem_scale WHERE deleted_at IS NULL AND moved_to_fact IS NULL "
        "ORDER BY id")
    if not rows:
        return 0
    existing = {}
    for r in await conn.fetch(
            "SELECT id, source_url, source_quote FROM info_facts "
            "WHERE deleted_at IS NULL AND source_url IS NOT NULL"):
        existing[(r["source_url"], normalize(r["source_quote"] or ""))] = r["id"]
    moved = 0
    for r in rows:
        key = (r["source_url"], normalize(r["source_excerpt"] or ""))
        fid = existing.get(key) if r["source_url"] else None
        if fid is None:
            country = _country_of_region(r["region"])
            fig = (r["figure"] or "").strip()
            f = {"section": SCALE_SECTION, "kind": "report", "country": country,
                 "city": None, "office": None,
                 "place_note": None if r["region"] == country else r["region"],
                 "applies_to": None, "title": fig[:200],
                 "body": fig if len(fig) > 200 else None, "when_text": None,
                 "source_url": r["source_url"], "source_title": None,
                 "source_quote": r["source_excerpt"], "ord": 0}
            fid = await _insert_fact(conn, r["topic_root_id"], f, r["author_id"],
                                     "unchecked", None, 30, created_at=r["created_at"])
            await db._log(conn, "info_fact_from_scale",
                          {"id": fid, "scale_id": r["id"], "topic_root_id": r["topic_root_id"]},
                          r["author_id"])
            if r["source_url"]:
                existing[key] = fid
            moved += 1
        await conn.execute("UPDATE problem_scale SET moved_to_fact = $2 WHERE id = $1",
                           r["id"], fid)
    return moved


def scale_row_of(r):
    """Сведение раздела «Масштаб» в прежнем виде строки масштаба — для карточки
    проблемы и контекста ИИ, которые читают «регион — цифра — источник»."""
    where = r["place_note"] or ", ".join(x for x in (r["country"], r["city"]) if x) or "—"
    return {"id": r["id"], "topic_root_id": r["topic_root_id"], "region": where,
            "figure": r["body"] or r["title"], "source_url": r["source_url"],
            "source_excerpt": r["source_quote"],
            "retrieved_at": r["checked_at"].isoformat() if r["checked_at"] else None,
            "author_id": r["author_id"], "created_at": r["created_at"].isoformat()}


async def scale_rows(conn, root):
    rows = await conn.fetch(
        "SELECT * FROM info_facts WHERE topic_root_id = $1 AND section = $2 "
        "AND deleted_at IS NULL ORDER BY id", root, SCALE_SECTION)
    return [scale_row_of(r) for r in rows]


# ------------------------------------------------------------ чтение

def _fact_out(r, reports, rep_counts, me=None):
    now = datetime.now(timezone.utc)
    checked = r["checked_at"]
    stale = bool(checked and (now - checked).days > r["recheck_days"])
    mine = next((x["verdict"] for x in reports if x["author_id"] == me), None) if me else None
    return {
        "id": r["id"], "section": r["section"], "country": r["country"],
        "city": r["city"], "office": r["office"], "place_note": r["place_note"],
        "applies_to": r["applies_to"],
        "kind": r["kind"], "title": r["title"], "body": r["body"],
        "when_text": r["when_text"], "source_url": r["source_url"],
        "source_title": r["source_title"], "source_quote": r["source_quote"],
        "quote_status": r["quote_status"],
        "checked_at": checked.isoformat() if checked else None,
        "stale": stale, "created_at": r["created_at"].isoformat(),
        "author": {"id": r["author_id"], "name": r["author_name"],
                   "is_service": r["author_is_service"]},
        "same": rep_counts["same"], "same_independent": rep_counts["same_ind"],
        "differs": rep_counts["differs"], "differs_independent": rep_counts["differs_ind"],
        "notes": [{"verdict": x["verdict"], "note": x["note"],
                   "at": x["created_at"].isoformat()}
                  for x in reports if x["note"]][-5:],
        "mine": mine,
    }


async def topic_facts(root, me=None, country=None):
    pool = db._pool_or_raise()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT f.*, a.name AS author_name, a.is_service AS author_is_service
            FROM info_facts f LEFT JOIN authors a ON a.id = f.author_id
            WHERE f.topic_root_id = $1 AND f.deleted_at IS NULL
              AND ($2::text IS NULL OR f.country = $2)
            ORDER BY f.country NULLS FIRST, f.city NULLS FIRST, f.ord, f.id
            """, root, country)
        reps = await conn.fetch(
            "SELECT r.* FROM info_reports r JOIN info_facts f ON f.id = r.fact_id "
            "WHERE f.topic_root_id = $1 AND f.deleted_at IS NULL ORDER BY r.created_at",
            root)
    by_fact: dict[int, list] = {}
    for x in reps:
        by_fact.setdefault(x["fact_id"], []).append(x)
    # одна склейка на всех авторов обсуждения, а не запрос на каждое сведение
    rep = await antiabuse.clusters([x["author_id"] for x in reps] +
                                   [r["author_id"] for r in rows if r["author_id"]])
    out = []
    for r in rows:
        rs = by_fact.get(r["id"], [])
        owner = rep.get(r["author_id"]) if r["author_id"] else None
        counts = {}
        for v in ("same", "differs"):
            ids = [x["author_id"] for x in rs if x["verdict"] == v]
            counts[v] = len(ids)
            counts[v + "_ind"] = len({rep.get(a, a) for a in ids} - {owner})
        out.append(_fact_out(r, rs, counts, me))
    return out


def section_order(facts):
    """Подписи разделов обсуждения в порядке чтения: закон и права — первыми."""
    seen = []
    for f in facts:
        if f["section"] not in seen:
            seen.append(f["section"])
    known = [s for s in SECTION_ORDER if s in seen]
    return known + [s for s in seen if s not in known]


def _geo_of(country):
    return sorted(taxonomy.GEO_PARENTS.get(country, {}).get("regions", set()))


async def counts_for(roots):
    """Сколько сведений у обсуждений — для кнопки «Сведения» и связанных."""
    if not roots:
        return {}
    pool = db._pool_or_raise()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT topic_root_id, count(*) AS n FROM info_facts "
            "WHERE topic_root_id = ANY($1::int[]) AND deleted_at IS NULL GROUP BY 1",
            list(roots))
    return {r["topic_root_id"]: r["n"] for r in rows}


async def topic_view(root_or_slug, me=None):
    t = await topic_of(root_or_slug)
    facts = await topic_facts(t["id"], me=me)
    common = [f for f in facts if not f["country"]]
    countries: dict[str, dict] = {}
    for f in facts:
        if not f["country"]:
            continue
        c = countries.setdefault(f["country"], {"country": f["country"], "facts": 0,
                                                "norm": 0, "report": 0,
                                                "experience": 0, "cities": set(),
                                                "regions": _geo_of(f["country"])})
        c["facts"] += 1
        c[f["kind"]] += 1
        if f["city"]:
            c["cities"].add(f["city"])
    for c in countries.values():
        c["cities"] = sorted(c["cities"])
    # связанные обсуждения («порождает») — там могут быть свои сведения
    links = await db.problem_links_of(t["id"])
    rel = [{"id": l["cause_id"], "title": l["problem_title"], "rel": "cause"}
           for l in links.get("causes", [])] + \
          [{"id": l["effect_id"], "title": l["problem_title"], "rel": "effect"}
           for l in links.get("effects", [])]
    n = await counts_for([r["id"] for r in rel])
    for r in rel:
        r["facts"] = n.get(r["id"], 0)
    sections = section_order(facts)
    return {
        "topic": {"id": t["id"], "title": t["title"] or (t["text"] or "")[:120],
                  "kind": t["kind"]},
        "sections": sections,
        "suggested_sections": sections + [s for s in DEFAULT_SECTIONS if s not in sections],
        "common": common,
        "countries": sorted(countries.values(), key=lambda c: c["country"]),
        "related": rel,
        "total": len(facts),
    }


async def country_view(root_or_slug, country, me=None):
    t = await topic_of(root_or_slug)
    if country not in taxonomy.COUNTRIES:
        raise InfoError("Такой страны нет в списке")
    facts = await topic_facts(t["id"], me=me, country=country)
    return {"topic": {"id": t["id"]}, "country": country,
            "national": [f for f in facts if not f["city"]],
            "cities": [{"city": c, "facts": [f for f in facts if f["city"] == c]}
                       for c in sorted({f["city"] for f in facts if f["city"]})]}


async def stale_facts(limit=200):
    """Что пора перепроверить: давно не сверялось или никогда."""
    pool = db._pool_or_raise()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, source_url, source_quote FROM info_facts
            WHERE deleted_at IS NULL AND source_url IS NOT NULL AND source_quote IS NOT NULL
              AND (checked_at IS NULL OR checked_at < now() - make_interval(days => recheck_days))
            ORDER BY checked_at NULLS FIRST LIMIT $1
            """, limit)
    return [dict(r) for r in rows]


async def search(root, question, country=None, limit=12):
    """Кандидаты для ответа: слова вопроса против утверждения, пояснения и
    выдержки + всё по стране человека. Без эмбеддингов — стенд их не грузит,
    а сведений в обсуждении сотни, не миллионы."""
    words = [w for w in re.findall(r"\w{4,}", (question or "").lower())][:12]
    pool = db._pool_or_raise()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT f.id, f.section, f.country, f.city, f.kind, f.title, f.body,
                   f.when_text, f.source_url, f.source_quote, f.quote_status,
                   (SELECT count(*) FROM unnest($3::text[]) w
                     WHERE lower(coalesce(f.title,'') || ' ' || coalesce(f.body,'') || ' ' ||
                                 coalesce(f.source_quote,'') || ' ' || coalesce(f.city,''))
                           LIKE '%' || left(w, greatest(4, length(w) - 2)) || '%') AS hits
            FROM info_facts f
            WHERE f.topic_root_id = $1 AND f.deleted_at IS NULL
              AND (f.country IS NULL OR $2::text IS NULL OR f.country = $2)
            """, root, country, words)
    ranked = sorted((dict(r) for r in rows),
                    key=lambda r: (-(r["hits"] + (2 if country and r["country"] == country else 0)),
                                   r["id"]))
    # без совпадений — пусто, а не «первые 12 подряд»: иначе сведения, не
    # связанные с вопросом, выдавались за подходящие (QA 28.09)
    return [r for r in ranked if r["hits"] or (country and r["country"] == country)][:limit]


async def fact_place(fact_id):
    """Где лежит сведение — чтобы ссылка #f29 открыла нужное обсуждение и страну."""
    pool = db._pool_or_raise()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, country, topic_root_id FROM info_facts "
            "WHERE id = $1 AND deleted_at IS NULL", fact_id)
    return dict(row) if row else None


# ------------------------------------------------------------ сведения в споре
# Довод ссылается на сведение как «#с123» (кириллическая «с», допустимы и
# латинские c/s): просто «#123» уже значит узел обсуждения, а номера узлов и
# сведений — из разных последовательностей (vault: decisions/2026-09-28-info-sector).
FACT_REF = re.compile(r"#[сСcCsS](\d{1,9})\b")
PROMPT_FACTS_MAX = 40          # сколько сведений обсуждения показать ИИ строкой
PROMPT_FACT_FULL_MAX = 8       # сколько упомянутых в черновике — целиком


def fact_refs(text):
    seen = []
    for m in FACT_REF.finditer(text or ""):
        i = int(m.group(1))
        if i not in seen:
            seen.append(i)
    return seen


async def facts_brief(ids):
    """Короткие карточки сведений по номерам — для блока под доводом."""
    ids = [int(i) for i in ids][:20]
    if not ids:
        return []
    pool = db._pool_or_raise()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, topic_root_id, kind, section, title, country, city, place_note, "
            "source_url, source_title, source_quote, quote_status "
            "FROM info_facts WHERE id = ANY($1::int[]) AND deleted_at IS NULL", ids)
    by_id = {r["id"]: dict(r) for r in rows}
    return [by_id[i] for i in ids if i in by_id]


def _prompt_line(f, full=False):
    where = ", ".join(x for x in (f.get("country"), f.get("city"), f.get("place_note")) if x) \
        or "general"
    line = f'[#с{f["id"]}] ({f["kind"]}; {where}; {f["section"]}) {f["title"]}'
    if full:
        if f.get("body"):
            line += " — " + f["body"][:400]
        if f.get("source_quote"):
            line += f' | source quote ({f.get("quote_status")}): «{f["source_quote"][:400]}»'
    return line


async def prompt_facts(root, text=""):
    """Сведения обсуждения для запроса разбора и компаньона: строкой каждое
    (до PROMPT_FACTS_MAX, законы и общее — первыми), а те, на которые ссылается
    черновик, — целиком, с выдержкой. Пусто — блока нет."""
    pool = db._pool_or_raise()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, kind, section, title, body, country, city, place_note,
                   source_quote, quote_status
            FROM info_facts WHERE topic_root_id = $1 AND deleted_at IS NULL
            ORDER BY (country IS NOT NULL), (kind <> 'norm'), id
            """, root)
    if not rows:
        return None
    facts = [dict(r) for r in rows]
    refs = set(fact_refs(text))
    full = [f for f in facts if f["id"] in refs][:PROMPT_FACT_FULL_MAX]
    brief = [f for f in facts if f["id"] not in refs][:PROMPT_FACTS_MAX]
    return {"total": len(facts),
            "cited": [_prompt_line(f, full=True) for f in full],
            "list": [_prompt_line(f) for f in brief]}
