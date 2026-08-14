#!/bin/bash
# Creates one database + one dedicated user per service (§8, §20).
# Runs once, on first container init, via Postgres's docker-entrypoint-initdb.d hook.
# Credentials come from the container's environment, sourced from the repo's .env file.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
  CREATE USER "$EVENT_DB_USER" WITH PASSWORD '$EVENT_DB_PASSWORD';
  CREATE DATABASE "$EVENT_DB_NAME" OWNER "$EVENT_DB_USER";

  CREATE USER "$BOOKING_DB_USER" WITH PASSWORD '$BOOKING_DB_PASSWORD';
  CREATE DATABASE "$BOOKING_DB_NAME" OWNER "$BOOKING_DB_USER";

  CREATE USER "$PAYMENT_DB_USER" WITH PASSWORD '$PAYMENT_DB_PASSWORD';
  CREATE DATABASE "$PAYMENT_DB_NAME" OWNER "$PAYMENT_DB_USER";
EOSQL
