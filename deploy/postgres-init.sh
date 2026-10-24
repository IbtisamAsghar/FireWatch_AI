#!/bin/sh
set -eu
# Entrypoint runs this only for a new PGDATA. Application never gets the bootstrap password.
psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --set=ON_ERROR_STOP=1 \
  --set=app_password="$APP_DATABASE_PASSWORD" --set=app_db="$POSTGRES_DB" <<'SQL'
CREATE ROLE firewatch LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD :'app_password';
ALTER DATABASE :"app_db" OWNER TO firewatch;
REVOKE ALL ON DATABASE :"app_db" FROM PUBLIC;
SQL
