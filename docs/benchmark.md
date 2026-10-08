# Resilient Lady AIS tracker comparison

## Recommendation

For collecting Resilient Lady positions at a 10-second history interval, use **dbeckerAC/ais-vessel-tracker**. It recorded the same six position rows as thanderoy, supports `HISTORY_SAMPLE_SECONDS=10` natively, and used about one quarter of the combined Docker image footprint. Average CPU and RAM were nearly the same. Its measured combined RAM peak was higher, but remained below 185 MiB with the 512 MiB per-container limits.

## Run

- Vessel: Resilient Lady, MMSI `311001056`.
- Window: 2026-10-08 11:48:09–12:48:30 UTC (60m21s from readiness through shutdown).
- Docker stats: 325 samples per container from 11:48:27–12:48:19 UTC, about 11 seconds apart.
- Both stacks used the AISStream key from Vault, mounted from `secrets/aisstream_api_key`, and the shared Compose security baseline. Each service had a 512 MiB memory limit.
- dbeckerAC used `HISTORY_SAMPLE_SECONDS=10`; Open Waters was disabled so both stacks collected from AISStream only.
- Both databases started with zero Resilient Lady position rows.
- Source revisions: `thanderoy/ais-tracker` `123254d7ebbaeb613f7fa38628757f1b9b86d5a9`; `dbeckerAC/ais-vessel-tracker` `6fc5464871ccb891f0e149b62df19d4ba6c33c9b`.

## Results

| Stack | Position rows | Database directory | App + database image virtual size | Average CPU, app + DB | Peak CPU, app + DB | Average RAM, app + DB | Peak RAM, app + DB |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| thanderoy/ais-tracker | 6 | 97.2 MiB | 3.11 GiB | 1.02% | 6.06% | 104.0 MiB | 117.6 MiB |
| dbeckerAC/ais-vessel-tracker | 6 | 70.4 MiB | 0.77 GiB | 1.00% | 5.68% | 102.8 MiB | 184.8 MiB |

Per-container averages and peaks:

| Container | CPU average / peak | RAM average / peak |
| --- | ---: | ---: |
| thanderoy app | 0.53% / 3.41% | 11.0 / 12.3 MiB |
| thanderoy database | 0.49% / 3.71% | 93.0 / 106.4 MiB |
| dbeckerAC app | 0.17% / 0.32% | 45.2 / 50.5 MiB |
| dbeckerAC database | 0.83% / 5.46% | 57.6 / 137.3 MiB |

thanderoy also stored 9 raw AIS messages for the target, of which 6 were position rows. Its current source has no `HISTORY_SAMPLE_SECONDS` setting, so the value passed during this run was ignored; it stores each received position. Its tracker was locally configured to filter AISStream to this MMSI because the repository does not expose a target MMSI setting in its environment configuration. dbeckerAC stores historical positions according to `HISTORY_SAMPLE_SECONDS`; its README documents that setting and the sampling behavior ([configuration](https://github.com/dbeckerAC/ais-vessel-tracker#configuration), [data behavior](https://github.com/dbeckerAC/ais-vessel-tracker#data-behavior)). thanderoy describes a Go tracker backed by Postgres/TimescaleDB with additional analytics ([project README](https://github.com/thanderoy/ais-tracker)).

The six reports in each database make the row counts directly comparable for this hour: the vessel produced only six position rows, so 10-second thinning did not reduce dbeckerAC’s count. The repository configurations still differ: dbeckerAC applied the requested sampling natively; thanderoy did not.

The reported image sizes are sums of Docker’s image virtual sizes, which can differ from actual on-disk layer use. Database directory size is measured separately from each PostgreSQL data directory.

## Whole-stack view

| Stack | Long-running services in the repository Compose setup | One-shot / optional services | Included in this run? |
| --- | --- | --- | --- |
| thanderoy | Go tracker and PostgreSQL 16 based on TimescaleDB HA | One-shot migration container; Postgres exporter for Prometheus | Tracker, database, and migration were included; exporter was not |
| dbeckerAC | Python FastAPI app and PostgreSQL 17 with PostGIS | Adminer database web UI | App and database were included; Adminer was not |

Both apps serve their web UI from the app image, so neither needs a separate frontend container. In the timed comparison, the migration container finished before sampling started. Its image has the same 15 filesystem layers as the thanderoy app image, so it adds no duplicate image layers on this Docker host. The CPU/RAM totals above include only the two long-running services per stack; they do not include the optional exporter or Adminer.

The databases differ more than the app processes: thanderoy uses a larger TimescaleDB HA image and enables TimescaleDB, PostGIS, pgvector, pg_trgm, postgres_fdw, and btree_gin; dbeckerAC uses a standard PostgreSQL 17 image with PostGIS. That explains much of the image-size and persistent-directory difference. The exporter adds database metrics for an external Prometheus server; Adminer is a browser-based SQL/database administration tool. Neither is required to collect positions.

dbeckerAC also enables Open Waters by default as a second feed. It was disabled during this comparison to isolate AISStream. That setting adds no container, but it can add another stream connection and more position observations in a normal deployment.

## Operational notes

At the live health check near startup, both databases were healthy, thanderoy was healthy and ready, and dbeckerAC was ready with AISStream in `streaming` state. None of the four containers restarted during the timed window. thanderoy logged optional CDC initialization and sanctions-worker warnings. dbeckerAC’s app received exit code 137 during shutdown, but Docker reported `OOMKilled=false`; its position count, database size, and resource samples were collected before shutdown. The runner now allows 45 seconds for shutdown on later runs.
