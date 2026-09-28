"""
Мультиаккаунты: сделать второй аккаунт БЕСПОЛЕЗНЫМ, а не невозможным
(vault: drafts/2026-09-28-multiaccounting, Alex 28.09: «закроем сразу всеми
возможными средствами, но чтоб это не ограничивало нормальных пользователей»).

Что здесь есть:
  1. Невидимое на входе — одноразовые почты и капча Cloudflare Turnstile
     (включается переменными; без них регистрация работает как раньше).
  2. Связанность вместо банов. Сырые IP НЕ хранятся: только HMAC-отпечаток
     адреса и только 30 дней. Для этой аудитории (украинцы в ЕС, воинский
     статус) «кто откуда заходил» — опасные данные, а для склейки аккаунтов
     достаточно знать, что два адреса РАВНЫ.
     Связь двух аккаунтов пишется ПАРОЙ без адреса (account_links) и живёт
     дольше отпечатков — иначе через 30 дней ферма снова считалась бы «разными
     людьми».
  3. Связанные аккаунты никого не лишают прав: они считаются одним голосом
     в «независимых подтверждениях». Общежитие беженцев с одним Wi-Fi — не
     ферма, поэтому показываются ОБА числа: «5 подтверждений, независимых 2».

Отпечаток браузера не снимаем сознательно: он следит за всеми, спорен по GDPR
и противоречит духу проекта (черновик 28.09, раздел «Чего не делать»).
"""

import hashlib
import hmac
import ipaddress
import json
import logging
import os
import secrets
import urllib.parse
import urllib.request

from . import db

log = logging.getLogger("noosphere.antiabuse")

# Сколько живут отпечатки адресов. Дольше незачем: склейка ловит регистрации
# «пачкой» и действия подряд, а старые адреса только копят опасные данные.
SIGNAL_DAYS = int(os.environ.get("NOOSPHERE_SIGNAL_DAYS", "30"))

# Сколько живёт связь пары. Без срока соседи по общежитию, однажды
# зарегистрированные подряд, оставались бы «одним человеком» навсегда
# (ревью 28.09, С-2; Alex: «делай как предлагаешь» — 90 дней).
LINK_DAYS = int(os.environ.get("NOOSPHERE_LINK_DAYS", "90"))

# Секрет отпечатков меняется сам раз в столько дней: из базового секрета и
# номера периода выводится ключ периода. Тот, у кого окажется дамп базы,
# не переберёт адреса, пока не знает базового секрета — а на проде он живёт в
# переменных Railway, не в базе (ревью 28.09, С-3).
ROTATE_DAYS = 30

# Регистрации с одного адреса ближе этого окна — один владелец. Шире нельзя:
# соседи по общежитию регистрируются в разные дни, ферма — пачкой.
REGISTER_WINDOW_HOURS = 2

TURNSTILE_SECRET = (os.environ.get("TURNSTILE_SECRET") or "").strip()
TURNSTILE_SITEKEY = (os.environ.get("TURNSTILE_SITEKEY") or "").strip()
TURNSTILE_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"

# Самые ходовые одноразовые ящики. Полный список — тысячи доменов и живёт
# своей жизнью; этих хватает, чтобы ферма не заводила аккаунты бесплатно.
DISPOSABLE_DOMAINS = frozenset("""
mailinator.com guerrillamail.com guerrillamail.net guerrillamail.org sharklasers.com
grr.la 10minutemail.com 10minutemail.net temp-mail.org temp-mail.io tempmail.com
tempmail.net tempmailo.com tempr.email throwawaymail.com yopmail.com yopmail.net
yopmail.fr getnada.com nada.email dispostable.com trashmail.com trashmail.de
mailnesia.com maildrop.cc mintemail.com fakeinbox.com emailondeck.com mohmal.com
spamgourmet.com mailcatch.com moakt.com tmail.ws tmpmail.org tmpmail.net
burnermail.io inboxkitten.com mail.tm mail.gw emailfake.com fakemail.net
luxusmail.org minuteinbox.com 33mail.com spambox.us discard.email
""".split())

SCHEMA = [
    # Секрет отпечатков. Хранится в базе, а не выдумывается при старте: иначе
    # после перезапуска те же адреса давали бы другие отпечатки и склейка
    # разваливалась бы на каждом деплое. Переменная окружения сильнее.
    """
    CREATE TABLE IF NOT EXISTS instance_secrets (
        name  TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS account_signals (
        id         BIGSERIAL PRIMARY KEY,
        author_id  INTEGER NOT NULL REFERENCES authors(id) ON DELETE CASCADE,
        kind       TEXT NOT NULL,           -- register | action
        net_hash   TEXT NOT NULL,           -- HMAC адреса, сам адрес не пишется
        context    TEXT,                    -- на что было действие (fact:12)
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS account_signals_hash ON account_signals (net_hash, created_at)",
    "CREATE INDEX IF NOT EXISTS account_signals_author ON account_signals (author_id)",
    # Пара «эти два аккаунта, похоже, один владелец». Без адреса — только
    # причина. a < b, чтобы пара не записалась дважды.
    """
    CREATE TABLE IF NOT EXISTS account_links (
        a          INTEGER NOT NULL REFERENCES authors(id) ON DELETE CASCADE,
        b          INTEGER NOT NULL REFERENCES authors(id) ON DELETE CASCADE,
        reason     TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (a, b),
        CHECK (a < b)
    )
    """,
]

WIPE_TABLES = "account_signals, account_links"

_secret: bytes | None = None


async def _get_secret(conn) -> bytes:
    """Ключ ТЕКУЩЕГО периода. Базовый секрет — из NOOSPHERE_SIGNAL_SECRET;
    без переменной (стенд, тесты) — из базы, с предупреждением в лог."""
    import time
    base = await _base_secret(conn)
    period = int(time.time() // (ROTATE_DAYS * 86400))
    return hmac.new(base, f"period:{period}".encode(), hashlib.sha256).digest()


async def _base_secret(conn) -> bytes:
    global _secret
    if _secret is not None:
        return _secret
    env = (os.environ.get("NOOSPHERE_SIGNAL_SECRET") or "").strip()
    if env:
        _secret = env.encode()
        return _secret
    log.warning("NOOSPHERE_SIGNAL_SECRET не задан — секрет отпечатков лежит в базе; "
                "на проде задайте переменную")
    val = await conn.fetchval(
        "SELECT value FROM instance_secrets WHERE name = 'signal'")
    if val is None:
        await conn.execute(
            "INSERT INTO instance_secrets (name, value) VALUES ('signal', $1) "
            "ON CONFLICT (name) DO NOTHING", secrets.token_hex(32))
        val = await conn.fetchval(
            "SELECT value FROM instance_secrets WHERE name = 'signal'")
    _secret = val.encode()
    return _secret


def client_ip(request) -> str:
    """Адрес клиента. Берём ПОСЛЕДНИЙ адрес X-Forwarded-For — его дописал наш
    прокси (Railway). Первый присылает сам клиент, и ферма подставляла бы
    туда что угодно, обходя склейку (ревью 28.09, С-4). Если прокси
    заменяет заголовок целиком, последний = единственный — тоже верно."""
    xff = [x.strip() for x in request.headers.get("x-forwarded-for", "").split(",") if x.strip()]
    return xff[-1] if xff else (request.client.host if request.client else "")


def _net(ip: str) -> str:
    """IPv4 — целиком; IPv6 — сеть /64: у одного подключения адрес внутри /64
    меняется сам (privacy extensions), и без этого один человек выглядел бы
    сотней."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ip or "?"
    if addr.version == 6:
        if addr.ipv4_mapped is not None:
            return str(addr.ipv4_mapped)
        return str(ipaddress.ip_network(f"{ip}/64", strict=False))
    return str(addr)


async def _hash(conn, ip: str) -> str:
    key = await _get_secret(conn)
    return hmac.new(key, _net(ip).encode(), hashlib.sha256).hexdigest()[:32]


async def _link(conn, x: int, y: int, reason: str):
    if x == y:
        return
    a, b = min(x, y), max(x, y)
    await conn.execute(
        "INSERT INTO account_links (a, b, reason) VALUES ($1, $2, $3) "
        "ON CONFLICT (a, b) DO NOTHING", a, b, reason)


async def record(author_id: int, ip: str, kind: str, context: str | None = None):
    """Записать отпечаток адреса и сразу вывести связи.

    register — регистрации с одного адреса ближе REGISTER_WINDOW_HOURS.
    action   — два аккаунта с одного адреса сделали одно и то же дело
               (подтвердили одно сведение) в пределах суток.
    Никогда не бросает: защита не должна ломать регистрацию или ответ.
    """
    if not ip:
        return
    try:
        pool = db._pool_or_raise()
        async with pool.acquire() as conn:
            h = await _hash(conn, ip)
            await _purge(conn)
            await conn.execute(
                "INSERT INTO account_signals (author_id, kind, net_hash, context) "
                "VALUES ($1, $2, $3, $4)", author_id, kind, h, context)
            if kind == "register":
                rows = await conn.fetch(
                    "SELECT DISTINCT author_id FROM account_signals "
                    "WHERE net_hash = $1 AND kind = 'register' AND author_id <> $2 "
                    "AND created_at > now() - make_interval(hours => $3)",
                    h, author_id, REGISTER_WINDOW_HOURS)
                for r in rows:
                    await _link(conn, author_id, r["author_id"], "регистрации с одного адреса подряд")
            elif context:
                rows = await conn.fetch(
                    "SELECT DISTINCT author_id FROM account_signals "
                    "WHERE net_hash = $1 AND context = $2 AND author_id <> $3 "
                    "AND created_at > now() - interval '1 day'",
                    h, context, author_id)
                for r in rows:
                    await _link(conn, author_id, r["author_id"],
                                f"одно действие с одного адреса ({context})")
    except Exception:
        log.warning("отпечаток не записан", exc_info=True)


async def _purge(conn):
    await conn.execute(
        "DELETE FROM account_signals WHERE created_at < now() - make_interval(days => $1)",
        SIGNAL_DAYS)
    await conn.execute(
        "DELETE FROM account_links WHERE created_at < now() - make_interval(days => $1)",
        LINK_DAYS)


async def purge():
    """Чистка по сроку — на старте сервера, а не только при новых действиях:
    иначе в тихий месяц отпечатки жили бы дольше обещанных 30 дней."""
    try:
        pool = db._pool_or_raise()
        async with pool.acquire() as conn:
            await _purge(conn)
    except Exception:
        log.warning("чистка отпечатков не удалась", exc_info=True)


async def clusters(author_ids) -> dict[int, int]:
    """Автор → представитель его группы связанных аккаунтов (union-find по
    account_links, включая связи через третьих лиц)."""
    ids = list({int(a) for a in author_ids if a is not None})
    if not ids:
        return {}
    pool = db._pool_or_raise()
    async with pool.acquire() as conn:
        # компонента связности целиком — рекурсивно от заданных авторов
        rows = await conn.fetch(
            """
            WITH RECURSIVE comp(id) AS (
                SELECT unnest($1::int[])
                UNION
                SELECT CASE WHEN l.a = c.id THEN l.b ELSE l.a END
                FROM account_links l JOIN comp c ON c.id IN (l.a, l.b)
                WHERE l.created_at > now() - make_interval(days => $2)
            )
            SELECT l.a, l.b FROM account_links l
            WHERE (l.a IN (SELECT id FROM comp) OR l.b IN (SELECT id FROM comp))
              AND l.created_at > now() - make_interval(days => $2)
            """, ids, LINK_DAYS)
    parent: dict[int, int] = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for r in rows:
        ra, rb = find(r["a"]), find(r["b"])
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)
    return {a: find(a) for a in ids}


async def independent_count(author_ids, exclude=()) -> int:
    """Сколько РАЗНЫХ владельцев среди авторов. exclude — чьи группы не
    считаются вовсе (автор сведения не подтверждает сам себя, в том числе
    вторым аккаунтом)."""
    ids = [a for a in author_ids if a is not None]
    if not ids:
        return 0
    rep = await clusters(ids + [e for e in exclude if e is not None])
    banned = {rep[e] for e in exclude if e is not None and e in rep}
    return len({rep[a] for a in ids} - banned)


async def link_groups(min_size=2):
    """Группы связанных аккаунтов — для очереди Alex. Без адресов: логины,
    причины и время."""
    pool = db._pool_or_raise()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT l.a, l.b, l.reason, l.created_at FROM account_links l "
            "ORDER BY l.created_at DESC LIMIT 5000")
        names = {r["id"]: r["username"] or r["name"] for r in await conn.fetch(
            "SELECT id, username, name FROM authors")}
    rep = await clusters([r["a"] for r in rows] + [r["b"] for r in rows])
    groups: dict[int, dict] = {}
    for r in rows:
        g = groups.setdefault(rep[r["a"]], {"members": set(), "reasons": [], "last": None})
        g["members"] |= {r["a"], r["b"]}
        g["reasons"].append(r["reason"])
        g["last"] = max(g["last"] or r["created_at"], r["created_at"])
    out = []
    for g in groups.values():
        if len(g["members"]) < min_size:
            continue
        out.append({"members": [{"id": m, "login": names.get(m)} for m in sorted(g["members"])],
                    "size": len(g["members"]),
                    "reasons": sorted(set(g["reasons"])),
                    "last": g["last"].isoformat()})
    out.sort(key=lambda g: -g["size"])
    return out


def disposable(email: str) -> bool:
    domain = (email or "").rsplit("@", 1)[-1].strip().lower()
    return domain in DISPOSABLE_DOMAINS


def captcha_enabled() -> bool:
    # только оба ключа: секрет без sitekey закрыл бы регистрацию всем —
    # форма не показала бы капчу, а сервер её требовал (ревью 28.09, С-7)
    return bool(TURNSTILE_SECRET and TURNSTILE_SITEKEY)


def verify_captcha(token: str | None, ip: str) -> bool:
    """Проверка Turnstile. Без секрета — выключена (стенд, тесты). Сеть
    Cloudflare легла — пропускаем: капча первый слой, не единственный, и
    закрывать регистрацию из-за чужого сбоя хуже, чем пропустить бота."""
    if not captcha_enabled():
        return True
    if not token:
        return False
    data = urllib.parse.urlencode({"secret": TURNSTILE_SECRET, "response": token,
                                   "remoteip": ip}).encode()
    try:
        with urllib.request.urlopen(TURNSTILE_URL, data=data, timeout=8) as r:
            return bool(json.loads(r.read().decode()).get("success"))
    except Exception:
        log.warning("Turnstile недоступен — регистрация пропущена без капчи", exc_info=True)
        return True
