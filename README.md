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

The app samples history every 10 seconds. Open Waters is disabled. AISStream
remains the primary feed. VesselAPI checks active vessels every minute and requests
their latest terrestrial position when AISStream has supplied no position for an
hour (including when the stream remains connected but lacks coverage). Each vessel
is queried at most once per hour, including failed requests; restarting the app
resets that request timer. Missing keys disable the fallback. Authentication and
quota errors pause requests for at least an hour, respecting a longer Retry-After.

Place the VesselAPI key in `secrets/vesselapi_api_key`; Puppet grants the remapped
app user read access. Satellite lookups are disabled. One continuously missing
vessel uses roughly 720–744 requests per month. Responses retain VesselAPI's
original position timestamp, and repeated or older positions are rejected by the
shared ingestor. `/api/v1/status` includes a `vesselapi` section with request,
accepted/rejected position, and error counters. `vesselapi.patch` wires the extra
provider into the pinned upstream code during image builds.

Run the fallback tests against the built upstream application:

```sh
ais_test_dir=$(mktemp -d)
docker compose cp app:/app/app "$ais_test_dir/app"
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$ais_test_dir" uv run --no-project \
  --with pytest --with asyncpg==0.30.0 --with pydantic-settings==2.10.1 \
  --with fastapi==0.116.1 --with websockets==15.0.1 \
  pytest -q -p no:cacheprovider tests
```

Secret values and `.env` are excluded from Git.

## Grafana

The dashboard at `https://grafana.docker.home.arpa/d/ais-resilient-lady` shows Resilient Lady's update interval and ground displacement histograms, hourly update counts as points, and positions on an OpenStreetMap. The time picker controls all four panels; the default is the last 48 hours. Hours with no stored updates appear as zero, and the first and last hours may be partial.

These plots describe **stored position history**, sampled at most once every 10 seconds, rather than every incoming AIS message. Times are UTC reception times for AISStream and original observation times for VesselAPI. Displacement is the geodesic distance between successive stored coordinates, including reception gaps. Both positions in each histogram pair must fall inside the selected range. Map tiles load in the browser from OpenStreetMap.

The `ais-postgres` service and Grafana share the internal `ais_grafana` network. The app uses the `db` alias on its own default network; Grafana connects to the unique name `ais-postgres:5432`, avoiding the other database's DNS names. PostgreSQL has no published host port.

After starting AIS, provision the reader and secret data source:

```sh
./setup-grafana.sh
docker compose -f ../grafana-loki/docker-compose.yml up -d --no-deps grafana
```

The setup script creates a dedicated `ais_grafana` role with SELECT access only to `position_reports`. Its generated password is stored in the ignored `secrets/grafana-datasource.yaml`, mounted as a Grafana provisioning file. Keep this file when restarting; rerunning setup reuses the password. Apply the ACLs from `../puppet-control-repo/data/nodes/docker.home.arpa.yaml` before starting Grafana; they grant host UID `100472` access, matching this host's remapped Grafana UID `472`. New monthly partitions are queried through the parent table and need no extra grants.

The Grafana Compose file mounts `grafana-dashboard.json` into its existing dashboard provider and declares `ais_grafana` as an external network. Start AIS first so the network exists. Verify the panel queries and reader permissions with:

```sh
node check-grafana.cjs
```
