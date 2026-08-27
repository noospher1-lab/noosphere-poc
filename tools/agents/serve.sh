#!/usr/bin/env bash
# Стенд для агентов: тот же сервер, но с окружением прогона.
#
#  RESEND_API_KEY не пробрасывается — иначе на выдуманные адреса агентов
#     полетят настоящие письма и подпортят репутацию домена; ссылка уходит в лог.
#  DEV_TOOLS не включается: под ним проверка гранта обходится (см. spend_llm),
#     а лимит $3 на агента должен быть настоящим.
#  INVITE_REQUIRED=0 — агенты регистрируются сами, без кодов.
cd "$(dirname "$0")/../.." || exit 1

set -a; [ -f .env ] && . ./.env; set +a
unset RESEND_API_KEY DEV_TOOLS
export INVITE_REQUIRED=0
export DEFAULT_BALANCE_USD="${AGENT_BUDGET_USD:-3}"
export LLM_BUDGET_PER_MIN="${LLM_BUDGET_PER_MIN:-30}"
# разговоры с компаньоном сохраняются на время тестов — включая те,
# что ведут живые участники в браузере (агентские пишет ещё и раннер)
export COMPANION_LOG="${COMPANION_LOG:-$PWD/companion-log.jsonl}"

pkill -9 -f "uvicorn app.main" 2>/dev/null
sleep 1
setsid nohup uvicorn app.main:app --port "${PORT:-8000}" > uvicorn-agents.log 2>&1 < /dev/null &
disown 2>/dev/null

for _ in $(seq 40); do
  sleep 1
  curl -s -o /dev/null "http://localhost:${PORT:-8000}/" && { ok=1; break; }
done
if [ "$ok" = 1 ]; then
  echo "стенд поднят: http://localhost:${PORT:-8000} (лог: uvicorn-agents.log)"
  echo "грант на аккаунт: \$${DEFAULT_BALANCE_USD}, инвайты выключены, письма не уходят"
else
  echo "не поднялся — смотри uvicorn-agents.log"; tail -5 uvicorn-agents.log; exit 1
fi
