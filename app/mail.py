"""Transactional mail (Resend).

Only two kinds of letter exist here, and both are the difference between an
account being recoverable or lost: confirm your address, and reset your
password. Nothing marketing-shaped belongs in this module.

Failure policy is fail-open, on purpose. A registration that 500s because the
mail provider is down loses a person forever; a registration that succeeds with
an unsent letter loses one letter, and the resend button fixes it. So send()
never raises — it returns False and logs loudly.

Without RESEND_API_KEY the letter is written to the log instead of the network.
That keeps local development working with no account anywhere, and it is also
the honest degradation on prod: before this module existed, reset links went to
the log and Alex passed them on by hand. Losing the key returns to that, rather
than silently swallowing the link.

Blocking urllib on purpose, matching app/poi.py: callers are async and wrap the
call in asyncio.to_thread, so the single worker does not stall.
"""

import json
import logging
import os
import urllib.error
import urllib.request

log = logging.getLogger("noosphere.mail")

RESEND_URL = "https://api.resend.com/emails"

# Must be a domain verified in Resend, or the provider rejects the send. The
# subdomain is deliberate: mail from noreply@noosphere.live shares reputation
# with nothing else we might send later.
MAIL_FROM = os.environ.get("MAIL_FROM", "Noosphere <noreply@noosphere.live>")

# Where a confused person's reply lands. Without it, answers to an automated
# letter vanish into a mailbox nobody reads.
MAIL_REPLY_TO = os.environ.get("MAIL_REPLY_TO") or None


def enabled() -> bool:
    """Whether letters actually leave the machine. Used by callers that want to
    tell the person 'check your inbox' only when that is true."""
    return bool(os.environ.get("RESEND_API_KEY"))


def send(to: str, subject: str, text: str, html: str | None = None,
         timeout: int = 20) -> bool:
    """Send one letter. True if the provider accepted it.

    Both text and html are sent when html is given: some clients show the
    plain part, and a text alternative measurably helps not landing in spam.
    """
    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        # Not an error worth failing on — see the module docstring. WARNING and
        # not INFO so it is visible in the log of a prod that lost its key.
        log.warning("MAIL (no RESEND_API_KEY, not sent) to=%s subject=%s\n%s",
                    to, subject, text)
        return False

    payload = {
        "from": MAIL_FROM,
        "to": [to],
        "subject": subject,
        "text": text,
    }
    if html:
        payload["html"] = html
    if MAIL_REPLY_TO:
        payload["reply_to"] = [MAIL_REPLY_TO]

    req = urllib.request.Request(
        RESEND_URL,
        data=json.dumps(payload).encode(),
        headers={
            "content-type": "application/json",
            "authorization": f"Bearer {api_key}",
            # Обязателен. Перед Resend стоит Cloudflare, и запрос с дефолтным
            # агентом urllib («Python-urllib/3.x») он отбивает своим 403 с кодом
            # 1010 — до Resend такой запрос вообще не доходит. Проверено
            # 2026-08-14: с этим заголовком 200, без него 403 на любой вызов,
            # то есть НИ ОДНО письмо с прода не уходило.
            "user-agent": "noosphere/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            json.loads(resp.read().decode())
        return True
    except urllib.error.HTTPError as e:
        # The body carries the actual reason (unverified domain, bad address),
        # and without it the log says only "400" and debugging is guesswork.
        body = ""
        try:
            body = e.read().decode()[:500]
        except Exception:
            pass
        log.error("MAIL failed to=%s subject=%s http=%s %s",
                  to, subject, e.code, body)
        return False
    except Exception as e:
        log.error("MAIL failed to=%s subject=%s %s: %s",
                  to, subject, type(e).__name__, e)
        return False


# ---- letters -------------------------------------------------------------
#
# Kept as functions rather than templates in files: there are two of them, both
# are short, and a link that has to survive a template engine is a link that
# can break silently.


def _wrap(title: str, body_html: str) -> str:
    """Minimal, table-free HTML. Mail clients mangle modern CSS, so this stays
    deliberately plain — the letter must be legible even when styling is
    stripped entirely."""
    return f"""<!doctype html>
<html><body style="margin:0;padding:24px;background:#f6f6f4;
  font:16px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
  color:#1a1a1a">
  <div style="max-width:520px;margin:0 auto;background:#fff;padding:28px;
    border-radius:10px">
    <h1 style="margin:0 0 16px;font-size:20px">{title}</h1>
    {body_html}
    <p style="margin-top:28px;color:#777;font-size:13px">
      Если вы этого не запрашивали — просто не отвечайте на письмо,
      ничего не произойдёт.
    </p>
  </div>
</body></html>"""


def send_verification(to: str, link: str) -> bool:
    text = (
        "Подтвердите адрес, чтобы публиковать в Noosphere.\n\n"
        f"{link}\n\n"
        "Ссылка действует 48 часов. Читать можно и без подтверждения.\n"
        "Если вы не регистрировались — просто не отвечайте на письмо."
    )
    html = _wrap("Подтвердите адрес", f"""
    <p>Читать Noosphere можно сразу. Чтобы <b>публиковать и голосовать</b>,
       подтвердите, что адрес ваш.</p>
    <p><a href="{link}" style="display:inline-block;padding:12px 20px;
       background:#1a1a1a;color:#fff;text-decoration:none;border-radius:6px">
       Подтвердить адрес</a></p>
    <p style="color:#777;font-size:14px">Ссылка действует 48 часов.
       Если кнопка не работает, откройте:<br>
       <span style="word-break:break-all">{link}</span></p>""")
    return send(to, "Подтвердите адрес — Noosphere", text, html)


def send_email_change(to: str, link: str) -> bool:
    """Письмо на НОВЫЙ адрес. Смена состоится только после перехода по ссылке:
    иначе опечатка в адресе отрезала бы человека от собственного аккаунта."""
    text = (
        "Подтвердите новый адрес для входа в Noosphere.\n\n"
        f"{link}\n\n"
        "Пока вы не перешли по ссылке, аккаунт остаётся на прежнем адресе.\n"
        "Ссылка действует 48 часов.\n"
        "Если смену запрашивали не вы — просто не отвечайте на письмо."
    )
    html = _wrap("Новый адрес", f"""
    <p>Кто-то (надеемся, вы) меняет адрес аккаунта в Noosphere на этот.</p>
    <p><a href="{link}" style="display:inline-block;padding:12px 20px;
       background:#1a1a1a;color:#fff;text-decoration:none;border-radius:6px">
       Подтвердить новый адрес</a></p>
    <p style="color:#777;font-size:14px">Пока ссылка не открыта, аккаунт
       остаётся на прежнем адресе. Ссылка действует 48 часов.<br>
       <span style="word-break:break-all">{link}</span></p>""")
    return send(to, "Подтвердите новый адрес — Noosphere", text, html)


def send_email_change_notice(to: str, new_email: str) -> bool:
    """Письмо на СТАРЫЙ адрес: единственный способ узнать об угоне вовремя.
    Ссылки в нём нет намеренно — сообщение, а не действие."""
    text = (
        "С вашего аккаунта в Noosphere запросили смену адреса "
        f"на {new_email}.\n\n"
        "Если это вы — ничего делать не нужно, подтвердите смену письмом на "
        "новый адрес.\n"
        "Если это не вы — смените пароль прямо сейчас: пока адрес не "
        "подтверждён, аккаунт остаётся за этим ящиком."
    )
    html = _wrap("Запрошена смена адреса", f"""
    <p>С вашего аккаунта запросили смену адреса на
       <b>{new_email}</b>.</p>
    <p>Если это вы — подтвердите смену письмом, которое ушло на новый адрес.</p>
    <p style="color:#777;font-size:14px">Если это не вы, смените пароль:
       пока новый адрес не подтверждён, аккаунт остаётся за этим ящиком.</p>""")
    return send(to, "Запрошена смена адреса — Noosphere", text, html)


def send_password_reset(to: str, link: str) -> bool:
    text = (
        "Восстановление доступа к Noosphere.\n\n"
        f"{link}\n\n"
        "Ссылка действует 48 часов и сработает один раз.\n"
        "Если вы не запрашивали сброс — просто не отвечайте на письмо, "
        "пароль останется прежним."
    )
    html = _wrap("Восстановление доступа", f"""
    <p>Кто-то запросил сброс пароля для этого адреса.</p>
    <p><a href="{link}" style="display:inline-block;padding:12px 20px;
       background:#1a1a1a;color:#fff;text-decoration:none;border-radius:6px">
       Задать новый пароль</a></p>
    <p style="color:#777;font-size:14px">Ссылка действует 48 часов и сработает
       один раз.<br>
       <span style="word-break:break-all">{link}</span></p>""")
    return send(to, "Восстановление доступа — Noosphere", text, html)


def send_balance_request(to: str, person: dict, profile_link: str) -> bool:
    """Tell Alex someone asked for a starting balance.

    Carries the profile with it rather than just a name: the decision is
    "is this a person or a throwaway", and that is answered by how long they
    have been here and whether they have written anything — not by a login.
    """
    def row(k, v):
        return f"<tr><td style='padding:3px 12px 3px 0;color:#777'>{k}</td>" \
               f"<td style='padding:3px 0'><b>{v}</b></td></tr>"

    note = (person.get("note") or "").strip()
    lines = [
        f"логин: {person.get('username')}",
        f"имя: {person.get('name')}",
        f"почта: {person.get('email')} "
        f"({'подтверждена' if person.get('email_verified') else 'НЕ подтверждена'})",
        f"зарегистрирован: {person.get('registered_at')}",
        f"опубликовано узлов: {person.get('nodes_count', 0)}",
        f"PoI диалога: {person.get('dialogue_poi')}",
        f"текущий баланс: ${person.get('balance_usd')}",
    ]
    if note:
        lines.append(f"\nсообщение от него:\n{note}")
    text = ("Запрос стартового баланса в Noosphere.\n\n"
            + "\n".join(lines) + f"\n\nПрофиль: {profile_link}\n")

    html = _wrap("Запрос стартового баланса", f"""
    <table style="border-collapse:collapse;font-size:15px">
      {row('логин', person.get('username'))}
      {row('имя', person.get('name'))}
      {row('почта', person.get('email'))}
      {row('адрес подтверждён',
           'да' if person.get('email_verified') else 'НЕТ')}
      {row('зарегистрирован', person.get('registered_at'))}
      {row('опубликовано узлов', person.get('nodes_count', 0))}
      {row('PoI диалога', person.get('dialogue_poi'))}
      {row('текущий баланс', '$' + str(person.get('balance_usd')))}
    </table>
    {'<p style="margin-top:16px;padding:12px;background:#f6f6f4;border-radius:6px">'
     + note + '</p>' if note else ''}
    <p style="margin-top:20px"><a href="{profile_link}">Открыть профиль</a></p>""")
    return send(to, f"Запрос баланса: {person.get('username')} — Noosphere",
                text, html)


def send_already_registered(to: str, reset_link: str) -> bool:
    """Sent when someone tries to register with an address that already has an
    account.

    This letter exists so that registration can answer identically whether or
    not the address is taken. Without it, the 409 'this email is taken' told
    any stranger whether a given person has an account here — on a site about
    political argument, that is not a small leak.
    """
    text = (
        "На этот адрес уже зарегистрирован аккаунт в Noosphere, "
        "и только что кто-то пробовал зарегистрироваться заново.\n\n"
        "Если это были вы и вы забыли пароль — задайте новый:\n"
        f"{reset_link}\n\n"
        "Если это были не вы — ничего делать не нужно, "
        "новый аккаунт создан не был."
    )
    html = _wrap("У вас уже есть аккаунт", f"""
    <p>На этот адрес уже зарегистрирован аккаунт, и только что кто-то
       пробовал зарегистрироваться заново. Новый аккаунт <b>создан не был</b>.</p>
    <p>Если это были вы и забыли пароль:</p>
    <p><a href="{reset_link}" style="display:inline-block;padding:12px 20px;
       background:#1a1a1a;color:#fff;text-decoration:none;border-radius:6px">
       Задать новый пароль</a></p>""")
    return send(to, "У вас уже есть аккаунт — Noosphere", text, html)
