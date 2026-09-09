#!/usr/bin/env bash
# Показать коды приглашений боевой базы: какие свободны, какие потрачены.
#
# Зачем отдельно от админского API: ADMIN_TOKEN на проде свой, и локальная копия
# .env его не знает. А коды нужны ровно тогда, когда зовёшь тестеров — то есть
# в момент, когда лезть за токеном в панель Railway особенно некстати.
#
# Только чтение: запрос фиксирован в тексте скрипта и ничего не меняет.
#
# Запуск:  ./prod-invites.sh

set -euo pipefail

cd "$(dirname "$0")"

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

SQL='SELECT code,
            coalesce(note, "") AS note,
            CASE WHEN used_at IS NULL THEN "свободен" ELSE "потрачен" END AS state
       FROM invites ORDER BY used_at NULLS FIRST, created_at;'

# Двойные кавычки в SQL выше — чтобы не воевать с одинарными по дороге через две
# оболочки; psql принимает их как идентификаторы, поэтому меняем на одинарные
# уже здесь, одной заменой, вместо экранирования на каждом уровне.
SQL=${SQL//\"/\'}
B64=$(printf '%s' "$SQL" | base64 -w0)

"$RAILWAY" ssh --project "$PROJECT" --environment "$ENVIRONMENT" \
    --service "$SERVICE" -i "$KEY" \
    "sh -c 'echo $B64 | base64 -d | psql -v ON_ERROR_STOP=1 -U \$PGUSER -d \$PGDATABASE'"
