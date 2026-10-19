#!/usr/bin/env bash
# DEVELOPMENT TOOLING — not used by the service.
#
# Creates a read-only Postgres role for reviewing stored data during development (e.g. when Claude is asked
# to review messages), so reviews never use the service's read-write credentials. The role can:
#   - read ONLY the review_messages view (bot messages, top-level + thread; no human messages, raw
#     payloads, reactions, feedback, or workflow tables) and the skill_rcas view (finished RCAs: report,
#     outcome, triage decision, feedback counts — the same view the skill reads),
#   - write nothing (no write grants; sessions start read-only, 30s statement timeout, max 3 connections).
# Its password goes into ~/.pgpass (chmod 600), so `psql -h 127.0.0.1 -U <role> <db>` works without anyone
# handling the password.
#
#   bash dev/review_access.sh            # grant (run after `nimi_service db upgrade`; safe to re-run)
#   bash dev/review_access.sh revoke     # remove the role and its ~/.pgpass entry
#
# Settings (defaults shown; database name falls back to ~/.config/nimi/service.env):
#   NIMI_REVIEW_USER=nimi_readonly  NIMI_DB_NAME=nimi  PGPORT=5432
#
# Re-run after any migration that recreates views (dropping a view drops its grants).
set -euo pipefail

ACTION="${1:-grant}"
PGPORT="${PGPORT:-5432}"
PGHOSTADDR="127.0.0.1"
ENV_FILE="$HOME/.config/nimi/service.env"
PGPASS="$HOME/.pgpass"
VIEWS=(review_messages skill_rcas)

from_env_file() { [[ -f "$ENV_FILE" ]] && grep "^$1=" "$ENV_FILE" | tail -1 | cut -d= -f2- || true; }
DB_NAME="${NIMI_DB_NAME:-$(from_env_file NIMI_DB_NAME)}"; DB_NAME="${DB_NAME:-nimi}"
RO_USER="${NIMI_REVIEW_USER:-nimi_readonly}"
for name in "$DB_NAME" "$RO_USER"; do
  [[ "$name" =~ ^[a-z_][a-z0-9_]{0,62}$ ]] || { echo "invalid name '$name' (use lowercase letters, digits, _)"; exit 1; }
done

pgpass_entry() { awk -F: -v h="$PGHOSTADDR" -v p="$PGPORT" -v d="$DB_NAME" -v u="$RO_USER" \
  '$1==h && $2==p && $3==d && $4==u {print substr($0, index($0,$5))}' "$PGPASS" 2>/dev/null | head -1; }

if [[ "$ACTION" == "revoke" ]]; then
  sudo -u postgres psql -p "$PGPORT" -d "$DB_NAME" -v ON_ERROR_STOP=1 -v ro="$RO_USER" <<'SQL'
SELECT format('REVOKE ALL ON ALL TABLES IN SCHEMA public FROM %I', :'ro'),
       format('REVOKE USAGE ON SCHEMA public FROM %I', :'ro')
WHERE EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'ro') \gexec
SQL
  sudo -u postgres psql -p "$PGPORT" -v ON_ERROR_STOP=1 -v db="$DB_NAME" -v ro="$RO_USER" <<'SQL'
SELECT format('REVOKE ALL ON DATABASE %I FROM %I', :'db', :'ro'), format('DROP ROLE %I', :'ro')
WHERE EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'ro') \gexec
SQL
  if [[ -f "$PGPASS" ]]; then
    awk -F: -v h="$PGHOSTADDR" -v p="$PGPORT" -v d="$DB_NAME" -v u="$RO_USER" \
      '!($1==h && $2==p && $3==d && $4==u)' "$PGPASS" > "$PGPASS.tmp" && mv "$PGPASS.tmp" "$PGPASS" && chmod 600 "$PGPASS"
  fi
  echo "Removed review role ${RO_USER} and its ~/.pgpass entry."
  exit 0
fi

[[ "$ACTION" == "grant" ]] || { echo "usage: $0 [grant|revoke]"; exit 1; }

touch "$PGPASS"; chmod 600 "$PGPASS"
RO_PW="$(pgpass_entry)"
if [[ -z "$RO_PW" ]]; then
  RO_PW="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
  echo "${PGHOSTADDR}:${PGPORT}:${DB_NAME}:${RO_USER}:${RO_PW}" >> "$PGPASS"
  echo "Added ${RO_USER} to $PGPASS"
fi

sudo -u postgres psql -p "$PGPORT" -v ON_ERROR_STOP=1 -v db="$DB_NAME" -v ro="$RO_USER" -v ro_pw="$RO_PW" <<'SQL'
SELECT format('CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE CONNECTION LIMIT 3', :'ro')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'ro') \gexec
ALTER ROLE :"ro" PASSWORD :'ro_pw';
ALTER ROLE :"ro" SET default_transaction_read_only = on;
ALTER ROLE :"ro" SET statement_timeout = '30s';
GRANT CONNECT ON DATABASE :"db" TO :"ro";
SQL

for VIEW in "${VIEWS[@]}"; do
sudo -u postgres psql -p "$PGPORT" -d "$DB_NAME" -v ON_ERROR_STOP=1 -v ro="$RO_USER" -v view="$VIEW" <<'SQL'
SELECT CASE WHEN to_regclass(:'view') IS NULL
            THEN 'DO $$ BEGIN RAISE EXCEPTION ''view % not found: run `nimi_service db upgrade` first'', ' || quote_literal(:'view') || '; END $$'
       END \gexec
GRANT USAGE ON SCHEMA public TO :"ro";
GRANT SELECT ON :"view" TO :"ro";
SQL
done

echo "Review role ${RO_USER} can read ${VIEWS[*]} in ${DB_NAME}:  psql -h ${PGHOSTADDR} -p ${PGPORT} -U ${RO_USER} ${DB_NAME}"
