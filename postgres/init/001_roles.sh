#!/bin/sh
set -eu
# Runs only on first initialization of this PostgreSQL named volume.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set=migration_password="$(tr -d '\r\n' < /run/secrets/pg_migrator)" \
  --set=reader_password="$(tr -d '\r\n' < /run/secrets/pg_reader)" \
  --set=writer_password="$(tr -d '\r\n' < /run/secrets/pg_writer)" <<'SQL'
CREATE ROLE canopy_migrator LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD :'migration_password';
CREATE ROLE canopy_reader LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD :'reader_password';
CREATE ROLE canopy_writer LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD :'writer_password';
REVOKE ALL ON DATABASE canopy FROM PUBLIC;
GRANT CONNECT ON DATABASE canopy TO canopy_migrator, canopy_reader, canopy_writer;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT CREATE ON DATABASE canopy TO canopy_migrator;
SQL
