# docker-ais

Minimal Compose deployment of [AIS Vessel Tracker](https://github.com/dbeckerAC/ais-vessel-tracker) for Resilient Lady (MMSI `311001056`). It runs the app and PostgreSQL/PostGIS under the local Compose security baseline. The UI is available at `http://localhost:8000`.

## Setup

The Compose file uses `/opt/docker/compose-security-baseline/hardening.yml`, as on this host. Compose settings live in `docker-compose.yml`; `.env` and `.env.example` are intentionally empty.

Build the app image locally from the pinned upstream revision and local overrides:

```sh
docker compose build
```

The Dockerfile clones upstream commit `6fc5464871ccb891f0e149b62df19d4ba6c33c9b` during the image build, then copies `src/` over it. Overrides follow the upstream paths (`src/backend/app/config.py` and `src/config/vessels.json`). The settings override inherits the original settings and reads the mounted secrets. Upstream source stays in Docker's build cache. The database uses the published `postgis/postgis:17-3.5` image.

Create both secret files from Vault:

```sh
install -d -m 700 secrets
vault kv get -field=aisstream_api_key kv/puppet > secrets/aisstream_api_key
vault kv get -field=docker_ais_postgres_password kv/puppet > secrets/postgres_password
```

Puppet manages the host ACLs for the app and database users on `secrets/` and each file.

Start the app and database after building:

```sh
docker compose -f docker-compose.yml up -d
```

Check status with `docker compose ps` and `http://localhost:8000/api/v1/status`. PostgreSQL data persists in the `ais_postgres_data` volume. `docker compose down` preserves it; `docker compose down -v` deletes it.

The app samples history every 10 seconds. Open Waters is disabled, so this setup uses AISStream only.

Secret values and `.env` are excluded from Git.
