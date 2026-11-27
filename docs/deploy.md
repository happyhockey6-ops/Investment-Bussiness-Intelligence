# Install Trusty on your own computer

Use an always-on computer you control, Docker Engine 28+ with Compose v2
(or current Docker Desktop), Git, OpenSSL, and Tailscale on both the host
and your phone. On Windows use Docker Desktop's WSL2 integration and run
the shell commands inside WSL2. Install Tailscale on the host that accepts
the loopback connection. The procedure uses your own disk and a personal
tailnet. It needs no cloud host, AI subscription or domain.

## Prepare a private checkout

Run these commands from the repository root on Linux, macOS or WSL2.
Use a clean checkout of the reviewed revision. Keep the project directory
and Compose project name stable so upgrades reuse the same volumes.

```sh
umask 077
cp deploy/.env.example .env
chmod 600 .env
openssl rand -hex 32
git rev-parse HEAD
```

Edit the local `.env`. Paste the generated hex password into
`POSTGRES_PASSWORD` and the full commit into `TRUSTY_CODE_COMMIT`.
Replace `<CONTACT_EMAIL>` with a monitored address and supply your
organization name. Choose an absolute `TRUSTY_BACKUP_DIR` **outside the
repository**. Keep the password with your private recovery records. It
initializes PostgreSQL on first start. Editing it later requires a matching
database password change. Compose builds the container database URL using
the password and service name `db`. Keep the raw mount at
`/var/lib/trusty/raw` because the database records this path.

Create the host backup directory before using Compose. For example on Linux
or WSL2 with standard Docker, replace the example path below with the exact
value in your .env. Give the recovery container (UID 10001) private write
access. With rootless Docker use the corresponding mapped host UID. On
Docker Desktop use a shared host folder accessible by the container.

```sh
sudo install -d -m 0700 -o 10001 -g 10001 /absolute/path/outside/checkout/trusty-backups
docker compose config --quiet
docker compose build app migrate listings ingest backup restore
docker compose up -d db
docker compose run --rm migrate
docker compose run --rm listings
docker compose run --rm ingest --ticker AAPL
docker compose up -d app
docker compose ps
curl --fail http://127.0.0.1:8000/healthz
```

Visit http://127.0.0.1:8000 and search for AAPL. The host port can be changed
with `IBI_API_PORT`; substitute that port in the local and Tailscale commands.
All operator services require the tools profile or an explicit service name.
Ordinary startup runs only the database and app. The app checks the installed
Alembic revision and exits before listening if it differs. Check
`docker compose logs app` if startup fails. Only the explicit listings and
ingest commands contact the SEC. Repeat `--ticker` to ingest more companies.

## Connect privately

Sign in to the same private tailnet on both devices. Use a one-user tailnet
or configure Tailscale grants so only your identity can reach this host on
443. Remove broader allow rules that would grant other members access.
This application has no login of its own. Check the host's existing Serve
configuration before assigning the HTTPS endpoint.

```sh
tailscale serve --bg 8000
tailscale serve status
tailscale funnel status
```

Open the reported `https://<machine>.<tailnet>.ts.net` URL on the connected
phone. **Do not use `tailscale funnel`.** Keep router forwarding and public
reverse proxies disabled. With the phone off Wi-Fi and Tailscale disconnected,
the site should be unreachable. Reconnect Tailscale and confirm it opens.

`docker compose ps` should show only `127.0.0.1:8000->8000/tcp` for
the app and no host mapping for PostgreSQL. Check listeners with
`ss -ltnp` (Linux), `netstat -an` (macOS), or `netstat -ano` (Windows).
Neither 8000 nor 5432 should listen on a LAN or wildcard address for Trusty.
The app's internal wildcard bind is confined to its Compose network.
Keep the Docker host, its users and containers trusted. PostgreSQL and the
snapshot directory persist in project-scoped named volumes across restarts.

## Back up, then upgrade

```sh
docker compose run --rm backup
```

Record the printed timestamp. Inside that folder are a custom-format
PostgreSQL dump, a compressed tar of the exact snapshot bytes, and
`manifest.json` with both SHA-256 digests, Alembic revision and the code
commit supplied at build. Build all services from the same clean commit so this identifier matches the
running code. The app can serve during backup. All Compose
writers, migrations and recovery tools take the same exclusive lock.
Avoid manual SQL and volume writes during these operations. A partial
backup is never published as a finished set. Legacy rows missing a hash or
references outside this deployment's raw path fail verification.

Keep a second private copy of the whole set on another disk. Checksums detect
corruption; only restore backups you created and protected against tampering.
The dumps are unencrypted. Use encrypted storage and restrict host access.
Run a scheduled backup, for example with this user crontab (adjust paths,
including the Docker CLI path). Check the log and available space regularly.

```cron
15 2 * * * cd /absolute/path/to/checkout && /usr/bin/docker compose run --rm -T backup >> "$HOME/trusty-backup.log" 2>&1
```

For upgrades: run backup and record the set ID, stop the app with
`docker compose stop app`, then `git pull --ff-only` the reviewed revision.
Update `TRUSTY_CODE_COMMIT` in .env from `git rev-parse HEAD`, run
`docker compose build app migrate listings ingest backup restore`, then
`docker compose up -d db`, `docker compose run --rm migrate` and
`docker compose up -d app`. Check health and an ingested company. Keep
the pre-upgrade backup and corresponding checkout available until validated.

## Restore and rehearse monthly

Every month rehearse on the **separate project** below, from the checkout
matching the backup commit. Set `SET` to the timestamp printed by backup.
The separate project has its own PostgreSQL and raw volumes. Start only its
database. The rehearsal uses the host backup directory read-only.

```sh
SET=20261116T020000000000Z  # replace with your set name
docker compose -p trusty-restore-test build restore
docker compose -p trusty-restore-test up -d db
docker compose -p trusty-restore-test run --rm restore "$SET"
docker compose -p trusty-restore-test exec -T db psql -U ibi -d ibi -c 'SELECT count(*) FROM source_documents'
docker compose -p trusty-restore-test down -v
```

Check the reported verified row count, expected company data and manifest
revision. The final command deletes only the rehearsal project's volumes.
Never use `down -v` on your live project.

For actual recovery, stop the app, pause scheduled jobs and disconnect other
clients. Start the target database alone. Use `docker compose run --rm
restore "$SET"` on a fresh target. Replacing an occupied target requires a
separate explicit decision and `docker compose run --rm restore "$SET"
--replace`. Restore verifies both hashes, loads a staging database, verifies
every archived source against its stored SHA-256 and checks the restored
revision. It only adds immutable files, refusing byte conflicts. After those
checks, a single PostgreSQL transaction switches databases. On failure the
original database remains selected; newly copied unreferenced files can remain.
Budget disk space for both databases, staged snapshots and the backup.
The prior database is retained under the name printed at success. Keep it
until recovery is confirmed. Start the app using the matching code revision,
check health and open a company page. Migrate explicitly only when upgrading.
