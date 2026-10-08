Provision these files from `kv/puppet`:

```sh
vault kv get -field=aisstream_api_key kv/puppet > secrets/aisstream_api_key
vault kv get -field=docker_ais_postgres_password kv/puppet > secrets/postgres_password
```

Puppet grants the remapped app and PostgreSQL users read access through POSIX ACLs. Keep this directory at mode `700`; never commit secret files.
