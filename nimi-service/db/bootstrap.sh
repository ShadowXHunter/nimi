#!/usr/bin/env bash
# Optional helper: set up the NIMI database on a local PostgreSQL cluster.
# (Using managed Postgres or infra-as-code? Create the database and a login role your own way and just set
#  the NIMI_DB_* settings — this script isn't needed.)
# Run as your normal user; it calls `sudo -u postgres` only for the CREATE ROLE / CREATE DATABASE steps.
#
#   bash nimi-service/db/bootstrap.sh
#
# Names are configurable (defaults shown); use the same values the service will use:
#   NIMI_DB_NAME=nimi  NIMI_DB_USER=nimi_service  NIMI_DB_SKILL_USER=nimi_skill_reader  PGPORT=5432
#   e.g.  NIMI_DB_NAME=rca NIMI_DB_USER=rca_svc bash db/bootstrap.sh
#
# Where the settings are written (default ~/.config/nimi/service.env):
#   NIMI_ENV_FILE_OUT=~/nimi/nimi-service/.env bash db/bootstrap.sh
# The service reads both locations (nimi-service/.env wins). Re-runs read names and passwords back from the
# same file, so pass the same NIMI_ENV_FILE_OUT every time.
#
# What it does:
# - Writes the DB settings to that file (chmod 600) as separate values
#   (NIMI_DB_HOST/PORT/NAME/USER/PASSWORD, NIMI_DB_SKILL_USER/PASSWORD) with generated passwords, adding
#   any that are missing. Passwords are never printed. (Prefer a secrets manager? Remove the passwords from
#   the file afterwards and inject them as NIMI_DB_PASSWORD / NIMI_DB_SKILL_PASSWORD or the *_FILE settings.)
# - Creates the service role (LOGIN, no superuser/createdb/createrole) and the database owned by it,
#   and restricts the database so only NIMI's roles can connect and only the service role can create objects.
# - Creates the skill's read-only role (used by the skill's `nimi-sql` for RCA history lookups): read-only
#   sessions, 5s statement timeout, max 5 connections, no grants yet — `db upgrade` grants it SELECT on the
#   skill_messages and skill_rcas views only.
# - Safe to re-run: existing roles/database are left alone (passwords are re-synced from the file).
# - Passwords reach psql on standard input, never as command-line arguments (which `ps` would show).
set -euo pipefail

SERVICE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PGPORT="${PGPORT:-5432}"
PGHOSTADDR="127.0.0.1"
DEFAULT_ENV_FILE="$HOME/.config/nimi/service.env"
ENV_FILE="${NIMI_ENV_FILE_OUT:-$DEFAULT_ENV_FILE}"
ENV_FILE="${ENV_FILE/#\~/$HOME}"                     # a quoted "~/..." is expanded too
ENV_DIR="$(dirname "$ENV_FILE")"

# Names: from the environment, else from an existing settings file, else defaults.
from_env_file() { [[ -f "$ENV_FILE" ]] && grep "^$1=" "$ENV_FILE" | tail -1 | cut -d= -f2- || true; }
DB_NAME="${NIMI_DB_NAME:-$(from_env_file NIMI_DB_NAME)}"; DB_NAME="${DB_NAME:-nimi}"
DB_USER="${NIMI_DB_USER:-$(from_env_file NIMI_DB_USER)}"; DB_USER="${DB_USER:-nimi_service}"
SKILL_USER="${NIMI_DB_SKILL_USER:-$(from_env_file NIMI_DB_SKILL_USER)}"; SKILL_USER="${SKILL_USER:-nimi_skill_reader}"
for name in "$DB_NAME" "$DB_USER" "$SKILL_USER"; do
  [[ "$name" =~ ^[a-z_][a-z0-9_]{0,62}$ ]] || { echo "invalid name '$name' (use lowercase letters, digits, _)"; exit 1; }
done

mkdir -p "$ENV_DIR"
[[ "$ENV_FILE" == "$DEFAULT_ENV_FILE" ]] && chmod 700 "$ENV_DIR"   # only NIMI's own config dir
umask 077

if [[ ! -f "$ENV_FILE" ]]; then
  PW="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
  cat > "$ENV_FILE" <<EOF
# nimi-service settings (private). Loaded by the service at startup. See nimi-service/env.example.
NIMI_DB_HOST=${PGHOSTADDR}
NIMI_DB_PORT=${PGPORT}
NIMI_DB_NAME=${DB_NAME}
NIMI_DB_USER=${DB_USER}
NIMI_DB_PASSWORD=${PW}
EOF
  echo "Created $ENV_FILE"
fi
chmod 600 "$ENV_FILE"
PW="$(from_env_file NIMI_DB_PASSWORD)"
if [[ -z "$PW" ]]; then
  echo "NIMI_DB_PASSWORD is not in $ENV_FILE (injected elsewhere?). Provide it to re-sync the role password:"
  echo "  NIMI_DB_PASSWORD=... bash $0"
  PW="${NIMI_DB_PASSWORD:?NIMI_DB_PASSWORD needed to set the $DB_USER password}"
fi

# Skill reader settings: add them to an existing file if missing.
if ! grep -q "^NIMI_DB_SKILL_USER=" "$ENV_FILE" && ! grep -q "^NIMI_DB_SKILL_PASSWORD" "$ENV_FILE" \
   && [[ -z "${NIMI_DB_SKILL_PASSWORD:-}" ]]; then
  SKILL_PW_NEW="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
  printf 'NIMI_DB_SKILL_USER=%s\nNIMI_DB_SKILL_PASSWORD=%s\n' "$SKILL_USER" "$SKILL_PW_NEW" >> "$ENV_FILE"
  echo "Added the skill reader role settings to $ENV_FILE"
fi
SKILL_PW="${NIMI_DB_SKILL_PASSWORD:-$(from_env_file NIMI_DB_SKILL_PASSWORD)}"
if [[ -z "$SKILL_PW" ]]; then
  echo "NIMI_DB_SKILL_PASSWORD is not in $ENV_FILE (injected elsewhere?). Provide it to re-sync the role password:"
  echo "  NIMI_DB_SKILL_PASSWORD=... bash $0"
  exit 1
fi

# psql with the psql variable `pw` set from standard input (not argv). The SQL comes from this function's stdin.
psql_with_pw() {
  local pw="$1"; shift
  [[ "$pw" != *$'\n'* ]] || { echo "passwords must not contain newlines"; exit 1; }
  local esc="${pw//\\/\\\\}"; esc="${esc//\'/\'\'}"   # psql single-quoted value: \ -> \\, ' -> ''
  { printf "\\set pw '%s'\n" "$esc"; cat; } | sudo -u postgres psql "$@"
}

psql_with_pw "$PW" -p "$PGPORT" -v ON_ERROR_STOP=1 -v db="$DB_NAME" -v svc="$DB_USER" <<'SQL'
SELECT format('CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE', :'svc')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'svc') \gexec
ALTER ROLE :"svc" PASSWORD :'pw';
SELECT format('CREATE DATABASE %I OWNER %I', :'db', :'svc')
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'db') \gexec
REVOKE ALL ON DATABASE :"db" FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE :"db" TO :"svc";
SQL

# The skill's read-only role. No table/view grants here: `db upgrade` grants SELECT on the skill views.
psql_with_pw "$SKILL_PW" -p "$PGPORT" -v ON_ERROR_STOP=1 -v db="$DB_NAME" -v ro="$SKILL_USER" <<'SQL'
SELECT format('CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT CONNECTION LIMIT 5', :'ro')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'ro') \gexec
ALTER ROLE :"ro" PASSWORD :'pw';
ALTER ROLE :"ro" SET default_transaction_read_only = on;
ALTER ROLE :"ro" SET statement_timeout = '5s';
ALTER ROLE :"ro" SET idle_in_transaction_session_timeout = '60s';
GRANT CONNECT ON DATABASE :"db" TO :"ro";
SQL

# Lock down the public schema inside the database so only its owner can create objects there.
sudo -u postgres psql -p "$PGPORT" -d "$DB_NAME" -v ON_ERROR_STOP=1 -v svc="$DB_USER" <<'SQL'
ALTER SCHEMA public OWNER TO :"svc";
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
SQL

echo "Done. Roles ${DB_USER} and ${SKILL_USER} and database ${DB_NAME} are ready on port ${PGPORT}."
echo "Settings: ${ENV_FILE}"
if [[ "$ENV_FILE" != "$SERVICE_DIR/.env" && -f "$SERVICE_DIR/.env" ]]; then
  echo "Note: ${SERVICE_DIR}/.env exists and overrides these settings where both set a value."
fi
echo "Next: create the tables with:  cd ${SERVICE_DIR} && .venv/bin/python -m nimi_service db upgrade"
