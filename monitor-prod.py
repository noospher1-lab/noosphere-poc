#!/usr/bin/env python3
"""Следит за боевым PoC и пишет письмо, когда он падает или возвращается.

Зачем: /healthz был, а смотрел в него никто. Падение прода обнаруживал бы
тестер, а не Alex — то есть в лучшем случае через день, в худшем никогда.

Почему письмом, а не «в консоль»: единственный сигнал, который догоняет
человека вне ноутбука. Отправка идёт через тот же app/mail.py, что и письма
платформы, — один ключ Resend, одна проверенная дорога.

Уведомление шлётся ТОЛЬКО на смене состояния (жив→упал, упал→жив). Иначе
десятиминутная авария превратилась бы в двенадцать одинаковых писем, и на
третью аварию Alex перестал бы их открывать.

Одиночный сбой сети не повод будить: падением считается PROBES подряд
неудачных проверок.

Установка (каждые 5 минут):
  crontab -e
  */5 * * * * /path/to/noosphere-poc/monitor-prod.py >> $HOME/noosphere-backups/monitor.log 2>&1
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

BASE = os.environ.get("MONITOR_URL", "https://graph.noosphere.live")
STATE = Path(os.environ.get("MONITOR_STATE",
                            Path.home() / ".noosphere-monitor.json"))
TIMEOUT = 20
PROBES = 3            # подряд неудачных, прежде чем считать это падением
GAP = 5               # секунд между попытками

# Проверяем и корень, и живой API: процесс, который отвечает 200 на статику,
# но потерял Postgres, для читателя мёртв ровно так же.
CHECKS = (("/healthz", "ok"), ("/api/topics", None))


def probe():
    """(живой?, что именно сломалось)"""
    for path, needle in CHECKS:
        req = urllib.request.Request(BASE + path,
                                     headers={"user-agent": "noosphere-monitor/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                body = r.read(2000).decode("utf-8", "replace")
                if r.status != 200:
                    return False, f"{path} → HTTP {r.status}"
                if needle and needle not in body:
                    return False, f"{path} → в ответе нет «{needle}»: {body[:120]}"
        except urllib.error.HTTPError as e:
            return False, f"{path} → HTTP {e.code}"
        except Exception as e:
            return False, f"{path} → {type(e).__name__}: {e}"
    return True, ""


def load_state():
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {"alive": True, "since": None}


def notify(subject, text):
    """Письмо Alex. Ключи берём из окружения; у cron его нет, поэтому .env
    читаем сами — иначе мониторинг молчал бы именно тогда, когда нужен."""
    env = Path(__file__).parent / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())
    to = os.environ.get("ADMIN_EMAIL") or os.environ.get("MONITOR_TO")
    if not to:
        print("некому писать: нет ADMIN_EMAIL", flush=True)
        return
    from app import mail
    if not mail.send(to, subject, text):
        print("письмо не ушло:", subject, flush=True)


def main():
    alive, why = probe()
    if not alive:
        # не будим из-за одного мигнувшего пакета
        for _ in range(PROBES - 1):
            time.sleep(GAP)
            alive, why = probe()
            if alive:
                break

    state = load_state()
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y-%m-%d %H:%M UTC")

    if alive == state.get("alive"):
        print(f"{stamp} {'жив' if alive else 'лежит'} — без изменений", flush=True)
        return

    if not alive:
        notify("⚠️ Noosphere не отвечает",
               f"Прод перестал отвечать: {why}\n\n"
               f"Адрес: {BASE}\nВремя: {stamp}\n\n"
               "Проверено три раза подряд. Логи: railway logs --service graph")
        print(f"{stamp} УПАЛ: {why}", flush=True)
    else:
        was = state.get("since") or "неизвестно когда"
        notify("✅ Noosphere снова отвечает",
               f"Прод восстановился.\n\nАдрес: {BASE}\nЛежал с: {was}\n"
               f"Поднялся: {stamp}")
        print(f"{stamp} поднялся (лежал с {was})", flush=True)

    STATE.write_text(json.dumps({"alive": alive, "since": stamp}))


if __name__ == "__main__":
    main()
