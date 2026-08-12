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
#   40 3 * * * /path/to/noosphere-poc/backup-prod.sh >> $HOME/noosphere-backups/cron-prod.log 2>&1
#
# Восстановление в ЧИСТУЮ базу (не в боевую!):
#   createdb noosphere_restore
#   gunzip -c noosphere-prod-2026-08-12-0340.sql.gz | psql -d noosphere_restore

set -euo pipefail

RAILWAY=$HOME/.npm-global/bin/railway
PROJECT=00000000-0000-0000-0000-000000000000
ENVIRONMENT=production
SERVICE=Postgres
KEY="$HOME/.ssh/id_ed25519"          # без пароля: у cron нет ssh-agent

DEST="${BACKUP_DIR:-$HOME/noosphere-backups}"
KEEP_DAYS="${BACKUP_KEEP_DAYS:-30}"
STAMP="$(date +%Y-%m-%d-%H%M)"
OUT="$DEST/noosphere-prod-$STAMP.sql.gz"

mkdir -p "$DEST"
chmod 700 "$DEST"          # дампы содержат хэши паролей и тексты тестеров

# Недописанный дамп не должен остаться под именем настоящего: он выглядит
# валидным и ему поверят.
trap 'rm -f "$OUT.partial"' EXIT

"$RAILWAY" ssh --project "$PROJECT" --environment "$ENVIRONMENT" \
    --service "$SERVICE" -i "$KEY" \
    'pg_dump --clean --if-exists -U $PGUSER -d $PGDATABASE' \
    2>/dev/null | gzip -9 > "$OUT.partial"

SIZE=$(stat -c %s "$OUT.partial")
if [ "$SIZE" -lt 2000 ]; then
  echo "⚠️  $(date +%F\ %T) подозрительно маленький дамп ($SIZE Б) — проверь базу" >&2
  exit 1
fi

# Целостность архива: ловит обрыв и порчу, которых проверка размера не видит.
gzip -t "$OUT.partial"

# Содержательная проверка: архив может быть целым и при этом пустым — например,
# если pg_dump отработал не по той базе. Ищем таблицу, без которой PoC не PoC.
if ! gunzip -c "$OUT.partial" | grep -q 'CREATE TABLE public.authors'; then
  echo "⚠️  $(date +%F\ %T) в дампе нет таблицы authors — это не та база" >&2
  exit 1
fi

mv "$OUT.partial" "$OUT"
chmod 600 "$OUT"
trap - EXIT

find "$DEST" -name 'noosphere-prod-*.sql.gz' -mtime "+$KEEP_DAYS" -delete
find "$DEST" -name 'noosphere-prod-*.partial' -mtime +1 -delete

echo "✅ $(date +%F\ %T) $OUT ($((SIZE / 1024)) КиБ), храним $KEEP_DAYS дней"
