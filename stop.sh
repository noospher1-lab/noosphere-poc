#!/usr/bin/env bash
pkill -9 -f "uvicorn app.main" && echo "🛑 остановлен" || echo "не запущен"
