#!/usr/bin/env bash
# Пересев БОЕВОЙ базы: стереть и засеять заново (app/seed.py).
#
# Это разрушающая операция. Она уносит аккаунты, тексты и оценки — всё, кроме
# неиспользованных кодов приглашения (их бережёт db.wipe(), иначе после сброса
# в закрытый по инвайтам инстанс не смог бы войти даже владелец).
#
# Почему отдельным скриптом, а не строчкой в терминале: пересев прода нельзя
# делать «на память». Здесь три вещи, которые в одноразовой команде забываются
# ровно тогда, когда они нужны: свежий бэкап, подтверждение вслух и проверка
# результата снаружи.
#
# Сид запускается ВНУТРИ контейнера (railway ssh): база доступна только по
# приватной сети Railway, наружу её открывать незачем.
#
# Запуск:  ./seed-prod.sh          (спросит подтверждение)
#          ./seed-prod.sh --yes    (без вопроса — для случая, когда решение уже принято)

set -euo pipefail

cd "$(dirname "$0")"

RAILWAY=$HOME/.npm-global/bin/railway
PROJECT=00000000-0000-0000-0000-000000000000
ENVIRONMENT=production
SERVICE=graph                       # приложение, не Postgres: сид — код приложения
KEY="$HOME/.ssh/id_ed25519"
BACKUP_DIR="${BACKUP_DIR:-$HOME/backups/db}"
PUBLIC_URL="${PUBLIC_URL:-https://graph.noosphere.live}"

# 1. Бэкап не старше суток. Не «есть ли вообще копия», а именно свежая: копия
#    недельной давности не вернёт то, что написали за неделю.
FRESH=$(find "$BACKUP_DIR" -name 'noosphere-prod-*.sql.gz' -mtime -1 | sort | tail -1)
if [ -z "$FRESH" ]; then
  echo "⛔ нет бэкапа прода свежее суток в $BACKUP_DIR — сначала ./backup-prod.sh" >&2
  exit 1
fi
FOUND=$(gunzip -c "$FRESH" | grep -c 'CREATE TABLE public.authors' || true)
if [ "$FOUND" -eq 0 ]; then
  echo "⛔ бэкап $FRESH не похож на дамп этой базы — пересев отменён" >&2
  exit 1
fi
echo "✅ бэкап: $FRESH ($(stat -c %s "$FRESH") Б)"

# 2. Что именно сейчас будет стёрто — числами, до того как это случится.
echo "— сейчас на проде:"
curl -s --max-time 20 "$PUBLIC_URL/api/stats" || true
echo
curl -s --max-time 20 "$PUBLIC_URL/api/topics" |
  python3 -c 'import json,sys; d=json.load(sys.stdin); print(f"— корней: {len(d)}"); [print("   ", t.get("title") or t["text"][:60]) for t in d]' || true

if [ "${1:-}" != "--yes" ]; then
  printf '\nСтереть боевую базу и засеять заново? (yes/нет): '
  read -r ANSWER
  [ "$ANSWER" = "yes" ] || { echo "отменено"; exit 1; }
fi

# 3. Сам пересев. NOOSPHERE_ALLOW_WIPE=1 — осознанное снятие гарда, который
#    иначе отказывается стирать базу с зарегистрированными аккаунтами.
#
#    `python` без пути брать НЕЛЬЗЯ: в контейнере Nixpacks системный питон стоит
#    раньше в PATH, а зависимости лежат в собственном venv сборки. Первый заход
#    так и умер на `ModuleNotFoundError: asyncpg` — по счастью, на импорте, то
#    есть до wipe(). Ищем интерпретатор, который ВИДИТ asyncpg, и только им.
REMOTE_SEED='
for PY in /opt/venv/bin/python /app/.venv/bin/python "$(command -v python3)" "$(command -v python)"; do
  [ -x "$PY" ] || continue
  if "$PY" -c "import asyncpg" 2>/dev/null; then
    echo "интерпретатор: $PY"
    NOOSPHERE_ALLOW_WIPE=1 "$PY" -m app.seed
    exit $?
  fi
done
echo "не нашёл питон с asyncpg — пересев не выполнен" >&2
exit 1
'
"$RAILWAY" ssh --project "$PROJECT" --environment "$ENVIRONMENT" \
    --service "$SERVICE" -i "$KEY" "sh -c '$REMOTE_SEED'"

# 4. Проверка снаружи: то, что сид напечатал внутри контейнера, ничего не
#    говорит о том, что видит читатель.
echo "— после пересева:"
curl -s --max-time 20 "$PUBLIC_URL/api/topics" |
  python3 -c '
import json, sys
d = json.load(sys.stdin)
print(f"— корней: {len(d)}")
for t in d:
    print("   ", t["kind"], "|", t.get("title"), "| PoI:", t["poi_score"], "|", t["author"])
bad = [t for t in d if t["poi_score"] is not None]
sys.exit(1 if bad or len(d) != 4 else 0)'
echo "✅ пересев прошёл: 4 проблемы, ни одного числа PoI на корнях"
