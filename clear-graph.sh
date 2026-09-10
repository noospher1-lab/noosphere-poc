#!/usr/bin/env bash
# Очистить СОДЕРЖИМОЕ боевого графа, сохранив людей.
#
# Зачем отдельно от seed-prod.sh: тот зовёт db.wipe(), а wipe TRUNCATE'ит
# authors — то есть уносит аккаунты тестеров вместе с почтой, балансом и
# паролем. Здесь наоборот: уходит всё написанное, остаются те, кто пишет.
#
# Что удаляется: узлы и рёбра, позиции и голоса за них, состояние проблем,
# накопитель попыток, строки масштаба, рубрики, рабочие деревья, голосования,
# разговоры при голосовании, тренажёрные диалоги, лог разговоров с ИИ и журнал
# событий. Плюс ПОСЕВНЫЕ персоны (username IS NULL): войти под ними нельзя, и
# без своих текстов они остаются пустыми именами.
#
# Что остаётся: аккаунты с логином, их сессии, коды приглашений, балансы,
# подтверждения почты, счётчики трат.
#
# Почему отдельным скриптом, а не строкой в терминале: это разрушающая
# операция на проде, и в одноразовой команде забываются ровно те три вещи,
# ради которых скрипт и написан, — свежий бэкап, подтверждение вслух и
# проверка результата после.
#
# Запуск:  ./clear-graph.sh          (спросит подтверждение)
#          ./clear-graph.sh --yes    (без вопроса)

set -euo pipefail

cd "$(dirname "$0")"

# Идентификаторы прода живут в deploy.env — файле рядом со скриптом, которого
# нет в гите. Образец значений — deploy.env.example.
HERE="$(cd "$(dirname "$0")" && pwd)"
[ -f "$HERE/deploy.env" ] || {
  echo "⛔ нет $HERE/deploy.env — скопируй deploy.env.example и впиши свои значения" >&2
  exit 1; }
# shellcheck source=/dev/null
. "$HERE/deploy.env"

RAILWAY="${RAILWAY_BIN:?RAILWAY_BIN не задан в deploy.env}"
PROJECT="${RAILWAY_PROJECT:?RAILWAY_PROJECT не задан в deploy.env}"
ENVIRONMENT="${RAILWAY_ENVIRONMENT:-production}"
SERVICE="${RAILWAY_SERVICE_DB:?RAILWAY_SERVICE_DB не задан в deploy.env}"
KEY="${RAILWAY_SSH_KEY:-$HOME/.ssh/id_ed25519}"
BACKUP_DIR="${BACKUP_DIR:-$HOME/backups/db}"

# 1. Бэкап не старше суток. Не «есть ли вообще копия», а именно свежая: копия
#    недельной давности не вернёт того, что написали за неделю.
FRESH=$(find "$BACKUP_DIR" -name 'noosphere-prod-*.sql.gz' -mtime -1 | sort | tail -1)
if [ -z "$FRESH" ]; then
  echo "⛔ нет бэкапа прода свежее суток в $BACKUP_DIR — сначала ./backup-prod.sh" >&2
  exit 1
fi
FOUND=$(gunzip -c "$FRESH" | grep -c 'CREATE TABLE public.authors' || true)
if [ "$FOUND" -eq 0 ]; then
  echo "⛔ бэкап $FRESH не похож на дамп этой базы — очистка отменена" >&2
  exit 1
fi
echo "— бэкап: $FRESH"

# 2. Подтверждение вслух.
if [ "${1:-}" != "--yes" ]; then
  echo
  echo "Будет стёрто ВСЁ содержимое боевого графа: узлы, позиции, реестр,"
  echo "рубрики, голосования и посевные персоны. Аккаунты с логином, их сессии"
  echo "и коды приглашений останутся."
  printf 'Напиши СТЕРЕТЬ, чтобы продолжить: '
  read -r ANSWER
  [ "$ANSWER" = "СТЕРЕТЬ" ] || { echo "отменено"; exit 1; }
fi

# 3. Сама очистка. Таблицы содержимого перечислены ЯВНО; CASCADE стоит только
#    чтобы не воевать с порядком внешних ключей между ними, а не чтобы он сам
#    решал, что ещё унести. authors, sessions и invites в списке нет.
#    Посевные персоны удаляются отдельной строкой после.
SQL="BEGIN;
TRUNCATE position_reactions, position_links, positions, author_topic_poi,
         reactions, node_addenda, node_topics, edges, interventions,
         problem_scale, problems, topic_facets, topic_geo, topic_tags,
         workspace, decision_revisions, decision_options, votes,
         vote_dialogues, decisions, dialogues, companion_log, nodes, events
         RESTART IDENTITY CASCADE;
DELETE FROM authors WHERE username IS NULL AND NOT is_service;
COMMIT;
SELECT 'узлов осталось: '||count(*) FROM nodes;
SELECT 'аккаунтов с логином: '||count(*) FROM authors WHERE username IS NOT NULL;
SELECT 'свободных кодов: '||count(*) FROM invites WHERE used_by IS NULL;"

B64=$(printf '%s' "$SQL" | base64 -w0)

"$RAILWAY" ssh --project "$PROJECT" --environment "$ENVIRONMENT" \
    --service "$SERVICE" -i "$KEY" \
    "sh -c 'echo $B64 | base64 -d | psql -v ON_ERROR_STOP=1 -U \$PGUSER -d \$PGDATABASE'"

echo
echo "✅ граф пуст, люди на месте"
