# marketdata

Trusted, verified data from SEC filings: proof on every value, cross-checks, published accuracy, announced changes.

SEC filings → raw store → extract / normalize / verify / cross-check → PostgreSQL → REST (`/v1`) with webhooks, MCP (`/mcp`), and a read-only web UI (`/`) with proof pages (`/proof`).

The plan, rules and epics are in [docs/PLAN.md](docs/PLAN.md); working rules for AI sessions are in [CLAUDE.md](CLAUDE.md). Parser surprises and how they were handled are logged in [docs/EDGE_CASES.md](docs/EDGE_CASES.md).

Information from public filings. Not investment advice.

## Quickstart

Needs Python 3.15, [PDM](https://pdm-project.org/) (on macOS: `brew install python@3.15 pdm`), and either Docker or a local PostgreSQL 18.

1. `cp .env.example .env`, and set `SEC_USER_AGENT` to `<app name> <contact email>` (needed only for commands that call the SEC)
2. Start PostgreSQL: `docker compose up -d postgres` (listens on host port **5433**: set that port in both URLs in `.env`), or use a local server on 5432 with a `marketdata` role (see below).
3. `pdm install` (creates `.venv` and installs exactly what `pdm.lock` pins)
4. `pdm run alembic upgrade head`
5. `pdm run pytest`
6. `pdm run uvicorn marketdata.api.app:app --reload`, then `curl localhost:8000/health`

**Local PostgreSQL instead of Docker.** Create the role and databases once, as a superuser:

```bash
psql -h localhost -U postgres -c "create role marketdata login createdb password 'marketdata'" -c "create database marketdata owner marketdata" -c "create database marketdata_test owner marketdata"
```

## Everyday commands

```bash
pdm run lint                          # black --check, isort --check-only, flake8, mypy: the CI gate
pdm run fmt                           # isort and black, rewriting files
pdm run pytest -m "not integration"   # unit tests only, no database
pdm run marketdata --help
pdm run marketdata sec get <url>      # fetch through the SEC client, keep the raw file, print its key
pdm run marketdata ingest security-master   # load tickers and the universe from the SEC (data/universe.csv)
pdm run marketdata universe check     # check data/universe.csv against the stored security master
pdm run marketdata company show <TICKER>   # CIK, name, class, SIC, fiscal year end, tickers, what data applies
pdm run marketdata worker             # run queued tasks; the one process that calls the SEC
pdm run marketdata scheduler          # enqueue the filings watch (every 10 minutes) and the daily security master
docker compose up -d api              # the API, MCP server and web UI in Docker, kept running (CONTAINER_DATABASE_URL in .env)
docker compose up -d --build api      # the same after a code change
docker compose up -d --build api worker scheduler   # all three from the one image api builds (D-137)
docker compose stop worker            # before running a worker with pdm run
pdm run marketdata backfill filings --years 5   # queue five years of filing history for the worker
pdm run marketdata tasks list         # tasks by kind and status, with errors
pdm run marketdata filings list <TICKER>   # a company's filings with plain-language event labels
pdm run python scripts/fetch_fixture.py <url> <name> [--trim]   # save a test fixture
pdm run python scripts/fetch_fixture.py --from-raw <raw key> <name>   # a fixture from the raw store, no request
pdm add <package>                     # new dependency; also list it in docs/PLAN.md §5 with its license
```

Only one process calls the SEC at a time: while the worker runs, `sec get`, `ingest security-master` and a second worker refuse to start and say who holds the SEC slot (PLAN.md D-062); the worker in Docker names itself `marketdata worker on docker-compose`. The Docker worker runs with `--wait`: started while another process holds the slot, it waits and takes over when that one stops (D-137).

Integration tests use `TEST_DATABASE_URL`. Its database name must end in `_test`, because the tests drop and recreate its schema. They fail rather than skip when PostgreSQL is down.

## Troubleshooting

- **Port 5432 already in use** when starting Docker Postgres: the compose file already uses host port 5433 for this reason; point `DATABASE_URL` and `TEST_DATABASE_URL` at 5433.
- **`DATABASE_URL is missing`** on startup: copy `.env.example` to `.env`, or export the variable.
- **`pdm install` picks the wrong Python**: run `pdm use python3.15`, then `pdm install` again.
- **`.venv` stops working after a Homebrew cleanup**: if python@3.15 was only installed as PDM's dependency, `brew autoremove` can delete it when PDM moves to a newer Python. `brew install python@3.15` keeps it. Then rebuild with `rm -rf .venv && pdm install`.
- **The Docker worker's SEC requests fail with `CERTIFICATE_VERIFY_FAILED`** ("self-signed certificate in certificate chain"): this machine's network re-signs HTTPS with its own root (TLS inspection on a managed Mac), which macOS trusts and the container's Debian store doesn't. Processes run with `pdm run` are fine: they verify through the macOS keychain. Give the worker a bundle of this Mac's roots, the proxy's included, mounted over the image's CA file by a `docker-compose.override.yml` of your own (git-ignored; Compose reads it with the main file):

  ```bash
  mkdir -p var/certs && security find-certificate -a -p /System/Library/Keychains/SystemRootCertificates.keychain /Library/Keychains/System.keychain > var/certs/ca-bundle.pem
  ```

  ```yaml
  services:
    worker:
      volumes:
        - ./var/certs/ca-bundle.pem:/etc/ssl/certs/ca-certificates.crt:ro
  ```

  Don't use `SSL_CERT_FILE` for it: with that set, the HTTP client checks certificates strictly, and a proxy root without a key-usage extension fails again ("CA cert does not include key usage extension"). Then `docker compose up -d --build api worker scheduler` (PLAN.md D-137).
- **Why not uv or ruff?** On some managed Macs, endpoint security kills binaries signed by "OpenAI OpCo, LLC" (team ID 2DC432GLL2) as soon as they start (exit code 137, no output), and Astral now signs uv and ruff that way. The project uses PDM, black, isort and flake8 instead (PLAN.md D-041). `codesign -dv <binary>` shows a binary's signer.
