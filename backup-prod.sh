#!/usr/bin/env bash
# Ночной бэкап БОЕВОЙ базы (Railway) на этот ноутбук.
#
# Зачем отдельно от backup.sh: тот дампит локальную базу разработки. Этот —
# прод. Второй слой к PITR на Railway: PITR лежит в том же аккаунте, что и сама
# база, и от потери аккаунта не спасает. Эта копия живёт вне Railway.
#
# Дамп снимается ВНУТРИ контейнера через `railway ssh`, а не локальным pg_dump:
#   - база доступна только по приватной сети, наружу её открывать не нужно;
#   - на сервере Postgres 18, локально pg_dump 16 — он отказался бы работать
#     («server version is newer than pg_dump version»). Внутри версии совпадают
#     всегда, даже когда Railway обновит мажор.
#
# Установка (03:40 ежедневно, вразнобой с чужими кронами):
#   crontab -e
#   40 3 * * * /path/to/noosphere-poc/backup-prod.sh >> $HOME/backups/db/cron-prod.log 2>&1
#
# Восстановление в ЧИСТУЮ базу (не в боевую!):
#   createdb noosphere_restore
#   gunzip -c noosphere-prod-2026-08-12-0340.sql.gz | psql -d noosphere_restore

set -euo pipefail

# Идентификаторы прода живут в deploy.env — файле рядом со скриптом, которого
# нет в гите. Пока они стояли прямо здесь, публичный репозиторий раздавал карту
# инфраструктуры: какой проект, какое окружение, какой сервис и каким ключом к
# нему ходит крон. Образец значений — deploy.env.example.
HERE="$(cd "$(dirname "$0")" && pwd)"
[ -f "$HERE/deploy.env" ] || {
  echo "⛔ нет $HERE/deploy.env — скопируй deploy.env.example и впиши свои значения" >&2
  exit 1; }
# shellcheck source=/dev/null
. "$HERE/deploy.env"

RAILWAY="${RAILWAY_BIN:?RAILWAY_BIN не задан в deploy.env}"
PROJECT="${RAILWAY_PROJECT:?RAILWAY_PROJECT не задан в deploy.env}"
ENVIRONMENT="${RAILWAY_ENVIRONMENT:-production}"
KEY="${RAILWAY_SSH_KEY:-$HOME/.ssh/id_ed25519}"
SERVICE="${RAILWAY_SERVICE_DB:?RAILWAY_SERVICE_DB не задан в deploy.env}"

DEST="${BACKUP_DIR:-$HOME/backups/db}"
KEEP_DAYS="${BACKUP_KEEP_DAYS:-30}"
STAMP="$(date +%Y-%m-%d-%H%M)"
OUT="$DEST/noosphere-prod-$STAMP.sql.gz"

mkdir -p "$DEST"
chmod 700 "$DEST"          # дампы содержат хэши паролей и тексты тестеров

# Недописанный дамп не должен остаться под именем настоящего: он выглядит
# валидным и ему поверят (trap ставится ниже, вместе с журналом ошибок).

# stderr НЕ в /dev/null: когда дамп не получился, единственное объяснение
# приходит именно оттуда (нет ключа, не поднялся сервис, не та база). Глушить
# его — значит остаться с сообщением «это не та база» и без причины. Держим в
# файле и печатаем только при неудаче, чтобы успешный крон молчал.
ERRLOG="$(mktemp)"
trap 'rm -f "$OUT.partial" "$ERRLOG"' EXIT

"$RAILWAY" ssh --project "$PROJECT" --environment "$ENVIRONMENT" \
    --service "$SERVICE" -i "$KEY" \
    'pg_dump --clean --if-exists -U $PGUSER -d $PGDATABASE' \
    2>"$ERRLOG" | gzip -9 > "$OUT.partial"

fail() {
    echo "⚠️  $(date +%F\ %T) $1" >&2
    if [ -s "$ERRLOG" ]; then
        echo "--- stderr railway ssh ---" >&2
        head -20 "$ERRLOG" >&2
    fi
    exit 1
}

SIZE=$(stat -c %s "$OUT.partial")
if [ "$SIZE" -lt 2000 ]; then
  fail "подозрительно маленький дамп ($SIZE Б) — проверь базу"
fi

# Целостность архива: ловит обрыв и порчу, которых проверка размера не видит.
gzip -t "$OUT.partial"

# Содержательная проверка: архив может быть целым и при этом пустым — например,
# если pg_dump отработал не по той базе. Ищем таблицу, без которой PoC не PoC.
# grep -c, а НЕ grep -q: с -q grep закрывает поток на первом совпадении,
# gunzip получает SIGPIPE, и под `set -o pipefail` весь конвейер считается
# упавшим — проверка сообщала «это не та база» о совершенно нормальном дампе,
# где таблица есть. -c дочитывает вход до конца и SIGPIPE не создаёт.
FOUND=$(gunzip -c "$OUT.partial" | grep -c 'CREATE TABLE public.authors' || true)
if [ "$FOUND" -eq 0 ]; then
  echo "--- что нашлось в дампе ---" >&2
  gunzip -c "$OUT.partial" | grep -E '^CREATE (TABLE|SCHEMA)' >&2 || true
  fail "в дампе нет таблицы authors — это не та база"
fi

mv "$OUT.partial" "$OUT"
chmod 600 "$OUT"
rm -f "$ERRLOG"
trap - EXIT

find "$DEST" -name 'noosphere-prod-*.sql.gz' -mtime "+$KEEP_DAYS" -delete
find "$DEST" -name 'noosphere-prod-*.partial' -mtime +1 -delete

echo "✅ $(date +%F\ %T) $OUT ($((SIZE / 1024)) КиБ), храним $KEEP_DAYS дней"
