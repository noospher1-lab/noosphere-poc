"""
Защита от XSS на фронте (задание безопасности 05.10.2026).

Два слоя: экранирование при выводе (static/safe.js) и заголовок CSP. Здесь —
проверки, которые не дают слою тихо развалиться. Это НЕ полный линтер «${…} в
HTML-строке без экранирования»: на 11 тысячах строк фронта такое правило даёт
ложные срабатывания на числах и константах, и его бы начали обходить. Берём
только правила, у которых ложных срабатываний нет.
"""

import os
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

STATIC = Path(__file__).parent.parent / "static"
HTML = sorted(STATIC.glob("*.html"))
JS = sorted(p for p in STATIC.glob("*.js") if p.name != "info-map.js")

TEST_DB = os.environ.get("TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not TEST_DB, reason="TEST_DATABASE_URL not set")


@pytest.mark.parametrize("page", HTML, ids=lambda p: p.name)
def test_no_inline_script_or_handler(page):
    """CSP script-src 'self': встроенный <script> и on…= просто не исполнятся,
    и страница молча сломается. Скрипт — в отдельный .js, обработчик — из кода."""
    html = page.read_text(encoding="utf-8")
    inline = [m for m in re.finditer(r"<script(?![^>]*\bsrc=)[^>]*>", html)
              if "application/json" not in m.group(0)]
    assert not inline, f"{page.name}: встроенный <script> — вынеси в .js"
    handlers = re.findall(r"<[a-z][^>]*\son[a-z]+\s*=", html, re.I)
    assert not handlers, f"{page.name}: обработчик on…= в разметке: {handlers[:1]}"


@pytest.mark.parametrize("page", HTML, ids=lambda p: p.name)
def test_safe_js_is_loaded_before_other_scripts(page):
    html = page.read_text(encoding="utf-8")
    srcs = re.findall(r'<script[^>]*\ssrc="([^"]+)"', html)
    if srcs:
        assert srcs[0] == "/safe.js", f"{page.name}: safe.js должен идти первым, а не {srcs[0]}"


@pytest.mark.parametrize("src", JS + HTML, ids=lambda p: p.name)
def test_error_text_never_goes_raw_into_markup(src):
    """Текст ошибки сервера (e.message, detail) бывает с куском запроса —
    в разметку только через escHtml/esc."""
    text = src.read_text(encoding="utf-8")
    raw = re.findall(r"\$\{\s*(?:e|err|error)\.(?:message|detail)\s*\}", text)
    raw += re.findall(r'innerHTML\s*=[^;]*\+\s*(?:e|err)\.message', text)
    assert not raw, f"{src.name}: e.message в разметке без экранирования: {raw[:1]}"


@pytest.mark.parametrize("src", JS, ids=lambda p: p.name)
def test_user_links_go_through_safe_url(src):
    """source_url / scale_url — ссылки от людей: javascript:-адрес в href
    исполняется по клику. Ставить только через safeUrl()."""
    text = src.read_text(encoding="utf-8")
    for m in re.finditer(r"\.href\s*=\s*([^;]+);", text):
        rhs = m.group(1)
        if re.search(r"\b(?:source_url|scale_url)\b", rhs):
            assert "safeUrl(" in rhs, f"{src.name}: href из ссылки людей без safeUrl: {m.group(0)[:80]}"
    for m in re.finditer(r'href="\$\{([^}]*(?:source_url|scale_url)[^}]*)\}"', text):
        assert "safeUrl(" in m.group(1), f"{src.name}: href из ссылки людей без safeUrl"


def test_safe_url_rule_is_meaningful():
    """Страховка от пустого теста: правило выше должно видеть такие места."""
    text = (STATIC / "tree.js").read_text(encoding="utf-8")
    assert len(re.findall(r"\.href\s*=\s*safeUrl\(", text)) >= 3


@pytest.fixture
def client():
    from tests.test_concessions import _client
    c = _client("csp_bot", True)
    yield c
    c.__exit__(None, None, None)


@needs_db
def test_html_carries_csp_and_static_does_not(client):
    csp = client.get("/").headers.get("content-security-policy", "")
    assert "script-src 'self' https://challenges.cloudflare.com" in csp
    assert "unsafe-inline" not in csp.split("style-src")[0], "для скриптов unsafe-inline недопустим"
    script_src = re.search(r"script-src ([^;]+)", csp).group(1)
    assert "unsafe-inline" not in script_src and "unsafe-eval" not in script_src
    for d in ("object-src 'none'", "base-uri 'self'", "frame-ancestors 'none'",
              "frame-src https://challenges.cloudflare.com"):
        assert d in csp, d
    assert "content-security-policy" not in client.get("/tree.js").headers
    assert "content-security-policy" in client.get("/graph.html").headers
    assert "content-security-policy" in client.get(f"/n/{client.ids['root']}").headers
