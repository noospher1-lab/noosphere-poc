#!/usr/bin/env bash
# Вернуть ОДИН аккаунт из дампа в боевую базу, не трогая остальное.
#
# Зачем: пересев (seed-prod.sh) уносит authors целиком, а владелец инстанса
# должен войти обратно тем же логином и с тем же балансом. Восстанавливать ради
# этого весь дамп нельзя — он вернёт и стёртый граф.
#
# Что переносится: логин, хэш пароля, имя, цвет, почта с отметкой
# подтверждения, баланс, дата регистрации. Что НЕ переносится: id (он занят
# посевными персонами — аккаунт получает новый), сессии (войти надо заново),
# api_key и связи со стёртыми узлами.
#
# Запуск:  ./restore-account.sh root [путь-к-дампу.sql.gz]
#          без второго аргумента берётся самый свежий дамп прода.

set -euo pipefail

cd "$(dirname "$0")"

RAILWAY=$HOME/.npm-global/bin/railway
PROJECT=00000000-0000-0000-0000-000000000000
ENVIRONMENT=production
SERVICE=Postgres
KEY="$HOME/.ssh/id_ed25519"
BACKUP_DIR="${BACKUP_DIR:-$HOME/backups/db}"

USERNAME="${1:?укажи логин: ./restore-account.sh root}"
DUMP="${2:-$(find "$BACKUP_DIR" -name 'noosphere-prod-*.sql.gz' | sort | tail -1)}"
[ -f "$DUMP" ] || { echo "⛔ дамп не найден: $DUMP" >&2; exit 1; }
echo "— из дампа: $DUMP"

# SQL собирается питоном, а не sed/awk: в хэше scrypt есть '$', а в COPY-формате
# — экранированные табы и \N. Ручная сборка строки здесь ошибается молча.
# Извлекатель кладём во временный файл, а не в `python3 -` с heredoc: heredoc
# занял бы stdin, и до питона доехал бы он, а не дамп.
EXTRACT="$(mktemp --suffix=.py)"
trap 'rm -f "$EXTRACT"' EXIT
cat > "$EXTRACT" <<'PY'
import re, sys

username = sys.argv[1]
dump = sys.stdin.read()
start = dump.index("COPY public.authors")
head, body = dump[start:].split("\n", 1)
cols = re.search(r"\((.*?)\)", head).group(1).split(", ")
row = None
for line in body.split("\n"):
    if line == "\\.":
        break
    values = line.split("\t")
    if dict(zip(cols, values)).get("username") == username:
        row = dict(zip(cols, values))
        break
if row is None:
    sys.exit(f"в дампе нет аккаунта {username!r}")

# id не переносим: он занят посевом. Остальное — как было.
keep = [c for c in cols if c != "id"]
def lit(v):
    if v == "\\N":
        return "NULL"
    return "'" + v.replace("\\t", "\t").replace("'", "''") + "'"

print(
    "INSERT INTO authors (" + ", ".join(keep) + ")\n"
    "VALUES (" + ", ".join(lit(row[c]) for c in keep) + ")\n"
    "ON CONFLICT (username) DO NOTHING;\n"
    "SELECT id, username, name, email, balance_usd, email_verified, is_service\n"
    "  FROM authors WHERE username = " + lit(row["username"]) + ";"
)
PY

SQL=$(gunzip -c "$DUMP" | python3 "$EXTRACT" "$USERNAME")

# base64: строка едет через две оболочки (локальную и удалённую), и '$' из хэша
# в любой из них превратился бы в подстановку переменной. base64 состоит из
# букв и цифр — ломать в нём нечего.
B64=$(printf '%s' "$SQL" | base64 -w0)

"$RAILWAY" ssh --project "$PROJECT" --environment "$ENVIRONMENT" \
    --service "$SERVICE" -i "$KEY" \
    "sh -c 'echo $B64 | base64 -d | psql -v ON_ERROR_STOP=1 -U \$PGUSER -d \$PGDATABASE'"
