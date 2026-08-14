"""
Отправка писем (app/mail.py).

Проверяется не доставка, а форма запроса к провайдеру: перед Resend стоит
Cloudflare, и запрос без User-Agent он отбивает своим 403 (код 1010), не
доводя до Resend. Именно из-за этого подтверждение адреса на проде молча не
работало с самого запуска — письма не уходили ни одного.
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app import mail  # noqa: E402


class _FakeResponse:
    def read(self):
        return b'{"id": "fake"}'

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_send_carries_a_user_agent(monkeypatch):
    """Без этого заголовка Cloudflare рубит запрос до Resend — и письма
    исчезают молча, потому что send() фейлится мягко."""
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["headers"] = {k.lower(): v for k, v in req.header_items()}
        seen["payload"] = json.loads(req.data.decode())
        return _FakeResponse()

    monkeypatch.setenv("RESEND_API_KEY", "test-key")
    monkeypatch.setattr(mail.urllib.request, "urlopen", fake_urlopen)

    assert mail.send("someone@example.com", "Тема", "Текст") is True
    ua = seen["headers"].get("user-agent", "")
    assert ua and "python-urllib" not in ua.lower(), (
        "запрос уходит с агентом по умолчанию — Cloudflare ответит 1010")
    assert seen["headers"]["authorization"] == "Bearer test-key"
    assert seen["payload"]["to"] == ["someone@example.com"]


def test_send_without_key_does_not_raise(monkeypatch):
    """Fail-open по замыслу: упавшая регистрация теряет человека навсегда,
    неотправленное письмо — одну кнопку «выслать ещё раз»."""
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    assert mail.send("someone@example.com", "Тема", "Текст") is False


def test_provider_error_is_swallowed(monkeypatch):
    import urllib.error

    def boom(req, timeout=None):
        raise urllib.error.HTTPError(mail.RESEND_URL, 403, "Forbidden", {}, None)

    monkeypatch.setenv("RESEND_API_KEY", "test-key")
    monkeypatch.setattr(mail.urllib.request, "urlopen", boom)
    assert mail.send("someone@example.com", "Тема", "Текст") is False
