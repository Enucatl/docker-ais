Provision these files from `kv/puppet`:

```sh
vault kv get -field=aisstream_api_key kv/puppet > secrets/aisstream_api_key
vault kv get -field=docker_ais_postgres_password kv/puppet > secrets/postgres_password
```

Store the VesselAPI key in `vesselapi_api_key` to enable hourly terrestrial
fallback for vessels missing AISStream updates. This file is excluded from Git.

Puppet grants the remapped app and PostgreSQL users read access through POSIX ACLs. Keep this directory at mode `700`; never commit secret files.

`../setup-grafana.sh` generates `grafana-datasource.yaml` with a separate password for the read-only `ais_grafana` database role. Puppet's `data/nodes/docker.home.arpa.yaml` grants the remapped Grafana user (host UID `100472`) access to this file. Keep it across restarts and restore it with the database backup, or rerun setup to provision a new password and apply the Puppet ACLs before restarting Grafana.
