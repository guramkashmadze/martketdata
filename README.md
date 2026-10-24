# marketdata

Trusted, verified data from SEC filings: proof on every value, cross-checks, published accuracy, announced changes.

SEC filings → raw store → extract / normalize / verify / cross-check → PostgreSQL → REST (`/v1`) with webhooks, MCP (`/mcp`), and a read-only web UI (`/`) with proof pages (`/proof`).

The plan, rules and epics are in [docs/PLAN.md](docs/PLAN.md); working rules for AI sessions are in [CLAUDE.md](CLAUDE.md). Parser surprises and how they were handled are logged in [docs/EDGE_CASES.md](docs/EDGE_CASES.md).

Information from public filings. Not investment advice.

## Quickstart

Needs [uv](https://docs.astral.sh/uv/) and either Docker or a local PostgreSQL 16+.

1. `cp .env.example .env`
2. Start PostgreSQL: `docker compose up -d postgres` (listens on host port **5433**: set that port in both URLs in `.env`), or use a local server on 5432 with a `marketdata` role (see below).
3. `uv sync`
4. `uv run alembic upgrade head`
5. `uv run pytest`
6. `uv run uvicorn marketdata.api.app:app --reload`, then `curl localhost:8000/health`

**Local PostgreSQL instead of Docker.** Create the role and databases once, as a superuser:

```bash
psql -h localhost -U postgres -c "create role marketdata login createdb password 'marketdata'" -c "create database marketdata owner marketdata" -c "create database marketdata_test owner marketdata"
```

## Everyday commands

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy marketdata
uv run pytest -m "not integration"   # unit tests only, no database
uv run marketdata --help
```

Integration tests use `TEST_DATABASE_URL`. Its database name must end in `_test`, because the tests drop and recreate its schema. They fail rather than skip when PostgreSQL is down.

## Troubleshooting

- **Port 5432 already in use** when starting Docker Postgres: the compose file already uses host port 5433 for this reason; point `DATABASE_URL` and `TEST_DATABASE_URL` at 5433.
- **`DATABASE_URL is missing`** on startup: copy `.env.example` to `.env`, or export the variable.
- **`uv` or `ruff` exits with code 137 at once, printing nothing**: on some managed Macs, endpoint security kills binaries signed by "OpenAI OpCo, LLC" (team ID 2DC432GLL2), which is how Astral now signs uv and ruff. Ask IT to allow them; `codesign -dv $(which uv)` shows the signer.
