#!/bin/sh
set -eu
cd "$(dirname "$0")"
umask 077

if [ ! -f secrets/grafana-datasource.yaml ]; then
    password=$(openssl rand -hex 32)
    sed "s/@PASSWORD@/$password/" grafana-datasource.yaml.in > secrets/grafana-datasource.yaml
fi
password=$(sed -n 's/^      password: //p' secrets/grafana-datasource.yaml)
case "$password" in
    ''|*[!0-9a-f]*) echo 'Expected a generated hexadecimal password' >&2; exit 1 ;;
esac

{
    cat <<'SQL'
BEGIN;
DO $$ BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'ais_grafana') THEN
        CREATE ROLE ais_grafana LOGIN;
    END IF;
END $$;
SQL
    printf "ALTER ROLE ais_grafana PASSWORD '%s';\n" "$password"
    cat <<'SQL'
ALTER ROLE ais_grafana SET default_transaction_read_only = on;
ALTER ROLE ais_grafana SET statement_timeout = '30s';
GRANT CONNECT ON DATABASE ais TO ais_grafana;
GRANT USAGE ON SCHEMA public TO ais_grafana;
GRANT SELECT ON public.position_reports TO ais_grafana;
COMMIT;
SQL
} | docker compose exec -T ais-postgres psql -U ais -d ais -q -v ON_ERROR_STOP=1
