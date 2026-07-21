#!/usr/bin/env bash
# Durable launcher — starts the Noosphere PoC DETACHED (its own session) so it
# survives the terminal closing, an agent turn ending, or you stepping away.
# Key comes from .env (gitignored), never hard-coded in source.
cd "$(dirname "$0")" || exit 1

[ -f .env ] && set -a && . ./.env && set +a

pkill -9 -f "uvicorn app.main" 2>/dev/null
sleep 1

setsid nohup uvicorn app.main:app --port 8000 > uvicorn.log 2>&1 < /dev/null &
disown 2>/dev/null

# 20 tries, not 6: startup (pool + schema migrations) regularly takes longer
# than 6s, and the old window reported a false "не поднялся" over a server
# that was in fact coming up fine.
for i in $(seq 20); do
  sleep 1
  curl -s -o /dev/null http://localhost:8000/ && { ok=1; break; }
done
if [ "$ok" = 1 ]; then
  echo "✅ Noosphere живёт на http://localhost:8000  (лог: uvicorn.log, стоп: ./stop.sh)"
else
  echo "⚠️  не поднялся — смотри uvicorn.log"; tail -5 uvicorn.log
fi
