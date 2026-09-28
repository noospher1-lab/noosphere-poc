"""
Информационный сектор: сведения, подтверждения людей, сверка цитат
(vault: drafts/2026-09-28-info-sector).

Alex 28.09: «мы никого не консультируем, а собираем доступную информацию, а
люди уже ей распоряжаются как хотят». Отсюда устройство:

  - СВЕДЕНИЕ — не вердикт «правда/ложь», а утверждение и то, на что оно
    опирается. Три вида (Alex 28.09, «делай как предлагаешь»):
      норма  — текст закона или официальное разъяснение ведомства, цитата
               сверена с источником;
      сообщают — СМИ, юристы, помогающие организации: пересказ со ссылкой;
      опыт людей — то, что внесли сами люди; у опыта всегда есть страна,
               и опирается он на число независимых подтверждений.
    Раньше «практикой» называли и статьи СМИ, и опыт людей, а легенда
    обещала «так было с людьми» — агенты студии 28.09 поймали расхождение.
  - Порядок чтения — сначала суть для всех (закон-основание, законы о правах
    и равенстве, общие пути), потом страна, потом город, если в городе своя
    практика (Alex 28.09).
  - Сверка цитаты — механическая, без ИИ: сервер скачивает страницу и ищет
    выдержку дословно. ИИ здесь не судья, а редактор (разбор текста в поля).
  - Свежесть выводится: checked_at + recheck_days < сегодня → «пора
    перепроверить». Ничего не копится флагами.
"""

import html
import ipaddress
import json
import re
import socket
import urllib.request
from datetime import datetime, timezone

from . import antiabuse, db, taxonomy

SECTIONS = ("basis", "rights", "protection", "residence", "court", "other")
SECTION_NAMES = {
    "basis": "Закон-основание",
    "rights": "Права и равенство",
    "protection": "Продление защиты",
    "residence": "Переход на вид на жительство",
    "court": "Оспаривание в суде",
    "other": "Другое",
}
KINDS = ("norm", "report", "experience")
QUOTE_STATUSES = ("verified", "mismatch", "unreachable", "unchecked", "none")

# лимиты от вбросов (Alex 28.09: «будем мониторить и ограничивать лимиты»)
FACTS_PER_DAY = 20
REPORTS_PER_DAY = 100
# ключ advisory-замка «действия одного аккаунта в секторе»: без него два
# параллельных запроса оба видят «19 из 20» и оба проходят (ревью 28.09, С-5)
AUTHOR_LOCK = 7101


class InfoError(Exception):
    """Ошибка ввода — уходит в ответ API как есть."""


SCHEMA = [
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
    # разъехались бы в две страны на карте.
    """
    CREATE TABLE IF NOT EXISTS info_facts (
        id            SERIAL PRIMARY KEY,
        sector_id     INTEGER NOT NULL REFERENCES info_sectors(id) ON DELETE CASCADE,
        section       TEXT NOT NULL,
        country       TEXT,
        city          TEXT,
        office        TEXT,
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
    "CREATE INDEX IF NOT EXISTS info_facts_sector ON info_facts (sector_id, country, section)",
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
    section = d.get("section") or "other"
    if section not in SECTIONS:
        raise InfoError("Такого раздела нет — выберите из списка")
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
    # «Суть — для всех» рядом с законами ЕС (UX 28.09, Б2)
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
        "applies_to": _clean(d.get("applies_to"), 200),
        "title": title, "body": _clean(d.get("body"), 4000),
        "when_text": _clean(d.get("when_text"), 100),
        "source_url": url, "source_title": _clean(d.get("source_title"), 300),
        "source_quote": quote,
        "ord": int(d.get("ord") or 0),
    }


async def get_sector(slug_or_id):
    pool = db._pool_or_raise()
    async with pool.acquire() as conn:
        if str(slug_or_id).isdigit():
            row = await conn.fetchrow("SELECT * FROM info_sectors WHERE id = $1", int(slug_or_id))
        else:
            row = await conn.fetchrow("SELECT * FROM info_sectors WHERE slug = $1", str(slug_or_id))
    return dict(row) if row else None


async def ensure_sector(slug, title, intro=None, problem_id=None):
    pool = db._pool_or_raise()
    async with pool.acquire() as conn:
        async with conn.transaction():
            sid = await conn.fetchval("SELECT id FROM info_sectors WHERE slug = $1", slug)
            if sid:
                await conn.execute(
                    "UPDATE info_sectors SET title = $2, intro = $3, "
                    "problem_id = COALESCE($4, problem_id) WHERE id = $1",
                    sid, title, intro, problem_id)
                return sid
            sid = await conn.fetchval(
                "INSERT INTO info_sectors (slug, title, intro, problem_id) "
                "VALUES ($1, $2, $3, $4) RETURNING id", slug, title, intro, problem_id)
            await db._log(conn, "info_sector_added", {"id": sid, "slug": slug, "title": title})
    return sid


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


async def add_fact(sector_id, data: dict, author_id, quote_status="unchecked",
                   checked_at=None, recheck_days=30, limit=True):
    f = validate_fact(data)
    pool = db._pool_or_raise()
    async with pool.acquire() as conn:
        async with conn.transaction():
            if limit and author_id is not None:
                await conn.execute("SELECT pg_advisory_xact_lock($1, $2)", AUTHOR_LOCK, author_id)
                await _check_fact_limit(conn, author_id)
            fid = await conn.fetchval(
                """
                INSERT INTO info_facts (sector_id, section, country, city, office,
                    applies_to, kind, title, body, when_text, source_url,
                    source_title, source_quote, quote_status, checked_at,
                    recheck_days, ord, author_id)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18)
                RETURNING id
                """, sector_id, f["section"], f["country"], f["city"], f["office"],
                f["applies_to"], f["kind"], f["title"], f["body"], f["when_text"],
                f["source_url"], f["source_title"], f["source_quote"],
                quote_status, checked_at, recheck_days, f["ord"], author_id)
            await db._log(conn, "info_fact_added",
                          {"id": fid, "sector_id": sector_id, **f,
                           "quote_status": quote_status}, author_id)
    return fid


async def find_fact(sector_id, title, country):
    """Сведение с тем же утверждением в той же стране — для повторного посева."""
    pool = db._pool_or_raise()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT id FROM info_facts WHERE sector_id = $1 AND title = $2 "
            "AND country IS NOT DISTINCT FROM $3 AND deleted_at IS NULL",
            sector_id, title, country)


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


# ------------------------------------------------------------ чтение

def _fact_out(r, reports, rep_counts, me=None):
    now = datetime.now(timezone.utc)
    checked = r["checked_at"]
    stale = bool(checked and (now - checked).days > r["recheck_days"])
    mine = next((x["verdict"] for x in reports if x["author_id"] == me), None) if me else None
    return {
        "id": r["id"], "section": r["section"], "country": r["country"],
        "city": r["city"], "office": r["office"], "applies_to": r["applies_to"],
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


async def sector_facts(sector_id, me=None, country=None):
    pool = db._pool_or_raise()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT f.*, a.name AS author_name, a.is_service AS author_is_service
            FROM info_facts f LEFT JOIN authors a ON a.id = f.author_id
            WHERE f.sector_id = $1 AND f.deleted_at IS NULL
              AND ($2::text IS NULL OR f.country = $2)
            ORDER BY f.country NULLS FIRST, f.city NULLS FIRST, f.ord, f.id
            """, sector_id, country)
        reps = await conn.fetch(
            "SELECT r.* FROM info_reports r JOIN info_facts f ON f.id = r.fact_id "
            "WHERE f.sector_id = $1 AND f.deleted_at IS NULL ORDER BY r.created_at",
            sector_id)
    by_fact: dict[int, list] = {}
    for x in reps:
        by_fact.setdefault(x["fact_id"], []).append(x)
    # одна склейка на всех авторов сектора, а не запрос на каждое сведение
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


def _geo_of(country):
    return sorted(taxonomy.GEO_PARENTS.get(country, {}).get("regions", set()))


async def sector_view(slug_or_id, me=None):
    s = await get_sector(slug_or_id)
    if not s:
        raise InfoError("Такого раздела сведений нет")
    facts = await sector_facts(s["id"], me=me)
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
    return {
        "sector": {"id": s["id"], "slug": s["slug"], "title": s["title"],
                   "intro": s["intro"], "problem_id": s["problem_id"]},
        "sections": [{"id": k, "name": SECTION_NAMES[k]} for k in SECTIONS],
        "common": common,
        "countries": sorted(countries.values(), key=lambda c: c["country"]),
        "total": len(facts),
    }


async def country_view(slug_or_id, country, me=None):
    s = await get_sector(slug_or_id)
    if not s:
        raise InfoError("Такого раздела сведений нет")
    if country not in taxonomy.COUNTRIES:
        raise InfoError("Такой страны нет в списке")
    facts = await sector_facts(s["id"], me=me, country=country)
    return {"sector": {"id": s["id"], "slug": s["slug"], "title": s["title"]},
            "country": country,
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


async def search(sector_id, question, country=None, limit=12):
    """Кандидаты для ответа: слова вопроса против утверждения, пояснения и
    выдержки + всё по стране человека. Без эмбеддингов — стенд их не грузит,
    а сведений в секторе сотни, не миллионы."""
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
            WHERE f.sector_id = $1 AND f.deleted_at IS NULL
              AND (f.country IS NULL OR $2::text IS NULL OR f.country = $2)
            """, sector_id, country, words)
    ranked = sorted((dict(r) for r in rows),
                    key=lambda r: (-(r["hits"] + (2 if country and r["country"] == country else 0)),
                                   r["id"]))
    # без совпадений — пусто, а не «первые 12 подряд»: иначе сведения, не
    # связанные с вопросом, выдавались за подходящие (QA 28.09)
    return [r for r in ranked if r["hits"] or (country and r["country"] == country)][:limit]


async def fact_place(fact_id):
    """Где лежит сведение — чтобы ссылка /info.html#f29 открыла нужную страну."""
    pool = db._pool_or_raise()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT f.id, f.country, s.slug FROM info_facts f "
            "JOIN info_sectors s ON s.id = f.sector_id "
            "WHERE f.id = $1 AND f.deleted_at IS NULL", fact_id)
    return dict(row) if row else None
