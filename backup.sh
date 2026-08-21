#!/usr/bin/env bash
# Nightly backup of the PoC database.
#
# Why this exists: db.wipe() aside, a dead disk or a bad migration takes every
# tester's account, texts and PoI with it, and there is no way to reconstruct
# them. This is the only thing standing between a hardware fault and losing
# the whole test.
#
# Install (runs at 04:17 daily, staggered off the hour):
#   crontab -e
#   17 4 * * * /path/to/noosphere-poc/backup.sh >> $HOME/backups/db/cron.log 2>&1
#
# Restore:
#   gunzip -c noosphere-2026-07-21.sql.gz | psql "$DATABASE_URL"

set -euo pipefail

cd "$(dirname "$0")"
[ -f .env ] && set -a && . ./.env && set +a

DEST="${BACKUP_DIR:-$HOME/backups/db}"
KEEP_DAYS="${BACKUP_KEEP_DAYS:-30}"
STAMP="$(date +%Y-%m-%d-%H%M)"
OUT="$DEST/noosphere-$STAMP.sql.gz"

mkdir -p "$DEST"
chmod 700 "$DEST"          # dumps contain password hashes and API keys

# --clean --if-exists so the dump can be replayed over an existing database
pg_dump --clean --if-exists "$DATABASE_URL" | gzip -9 > "$OUT.partial"

# Only become the real file once the dump finished. A truncated backup that
# looks valid is worse than no backup, because it is trusted.
mv "$OUT.partial" "$OUT"
chmod 600 "$OUT"

SIZE=$(stat -c %s "$OUT")
if [ "$SIZE" -lt 2000 ]; then
  echo "⚠️  $(date +%F\ %T) подозрительно маленький дамп ($SIZE Б) — проверь базу" >&2
  exit 1
fi

# Prove it is restorable, not merely present: gzip -t catches truncation and
# corruption that a size check misses.
gzip -t "$OUT"

find "$DEST" -name 'noosphere-*.sql.gz' -mtime "+$KEEP_DAYS" -delete
find "$DEST" -name '*.partial' -mtime +1 -delete

echo "✅ $(date +%F\ %T) $OUT ($((SIZE / 1024)) КиБ), храним $KEEP_DAYS дней"

# The local copy dies with the machine. Set BACKUP_REMOTE to an rclone/scp
# target to get a copy off the box — until then this is a single-machine
# backup and a stolen or dead server still loses everything.
if [ -n "${BACKUP_REMOTE:-}" ]; then
  scp -q "$OUT" "$BACKUP_REMOTE/" && echo "   → скопировано в $BACKUP_REMOTE"
fi
