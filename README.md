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
pdm run python scripts/fetch_fixture.py <url> <name> [--trim]   # save a test fixture
pdm add <package>                     # new dependency; also list it in docs/PLAN.md §5 with its license
```

Integration tests use `TEST_DATABASE_URL`. Its database name must end in `_test`, because the tests drop and recreate its schema. They fail rather than skip when PostgreSQL is down.

## Troubleshooting

- **Port 5432 already in use** when starting Docker Postgres: the compose file already uses host port 5433 for this reason; point `DATABASE_URL` and `TEST_DATABASE_URL` at 5433.
- **`DATABASE_URL is missing`** on startup: copy `.env.example` to `.env`, or export the variable.
- **`pdm install` picks the wrong Python**: run `pdm use python3.15`, then `pdm install` again.
- **`.venv` stops working after a Homebrew cleanup**: if python@3.15 was only installed as PDM's dependency, `brew autoremove` can delete it when PDM moves to a newer Python. `brew install python@3.15` keeps it. Then rebuild with `rm -rf .venv && pdm install`.
- **Why not uv or ruff?** On some managed Macs, endpoint security kills binaries signed by "OpenAI OpCo, LLC" (team ID 2DC432GLL2) as soon as they start (exit code 137, no output), and Astral now signs uv and ruff that way. The project uses PDM, black, isort and flake8 instead (PLAN.md D-041). `codesign -dv <binary>` shows a binary's signer.
