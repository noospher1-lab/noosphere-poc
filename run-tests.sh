#!/usr/bin/env bash
# Прогон ВСЕХ тестов, включая те, что ходят в Postgres.
#
# Интеграционные тесты стирают базу, поэтому им нужна отдельная — и до
# 2026-08-25 их просто пропускали: у роли `noosphere` в системном Postgres нет
# прав CREATE DATABASE, а лезть туда под root ради тестов незачем. Поэтому
# здесь поднимается СВОЙ кластер в домашней папке, на своём порту: root не
# нужен, системная база не при делах, снести можно одной строкой (см. --clean).
set -u

PGDIR="${NOOSPHERE_TEST_PGDIR:-$HOME/.local/share/noosphere-pgtest}"
PGPORT="${NOOSPHERE_TEST_PGPORT:-5433}"
PGBIN="$(ls -d /usr/lib/postgresql/*/bin 2>/dev/null | sort -V | tail -1)"
[ -n "$PGBIN" ] && PATH="$PGBIN:$PATH"

if [ "${1:-}" = "--clean" ]; then
  pg_ctl -D "$PGDIR" stop >/dev/null 2>&1
  rm -rf "$PGDIR"
  echo "тестовый кластер удалён: $PGDIR"
  exit 0
fi

command -v initdb >/dev/null || { echo "нет initdb — поставь postgresql-client/server"; exit 1; }

if [ ! -d "$PGDIR" ]; then
  echo "создаю тестовый кластер в $PGDIR …"
  initdb -D "$PGDIR" -U noosphere --auth=trust --encoding=UTF8 --locale=C.UTF-8 >/dev/null || exit 1
fi

if ! pg_isready -h 127.0.0.1 -p "$PGPORT" >/dev/null 2>&1; then
  pg_ctl -D "$PGDIR" -o "-p $PGPORT -k $PGDIR" -l "$PGDIR/server.log" start >/dev/null || {
    echo "кластер не поднялся — смотри $PGDIR/server.log"; exit 1; }
  for _ in $(seq 20); do
    pg_isready -h 127.0.0.1 -p "$PGPORT" >/dev/null 2>&1 && break
    sleep 0.5
  done
fi

psql -h 127.0.0.1 -p "$PGPORT" -U noosphere -d postgres -tAc \
  "SELECT 1 FROM pg_database WHERE datname='noosphere_test'" | grep -q 1 ||
  psql -h 127.0.0.1 -p "$PGPORT" -U noosphere -d postgres -q \
    -c "CREATE DATABASE noosphere_test OWNER noosphere" || exit 1

# .env нужен не за ключом (LLM в тестах замокан), а за прочими переменными,
# которые модули читают на импорте.
[ -f .env ] && set -a && . ./.env && set +a

export TEST_DATABASE_URL="postgresql://noosphere@127.0.0.1:$PGPORT/noosphere_test"
echo "TEST_DATABASE_URL → noosphere_test на порту $PGPORT"
exec python3 -m pytest "${@:-tests/}" -q
