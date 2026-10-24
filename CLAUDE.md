# marketdata

Trusted, verified data from SEC filings: proof on every value, cross-checks, published accuracy, announced changes.
SEC filings → raw store → extract / normalize / verify / cross-check → PostgreSQL → REST (/v1) with webhooks, MCP (/mcp), and a read-only web UI (/) with proof pages (/proof).
The full plan, rules and epics are in docs/PLAN.md. Before writing code, read §1–§11 and the epic you are working on.

## Commands
- Start database: docker compose up -d postgres
- Install: uv sync
- Migrate: uv run alembic upgrade head
- Tests: uv run pytest
- Lint and types: uv run ruff check . && uv run ruff format --check . && uv run mypy marketdata
- API: uv run uvicorn marketdata.api.app:app --reload
- Worker: uv run marketdata worker
- Scheduler: uv run marketdata scheduler
- Extraction hit rate: uv run marketdata extract report
- Accuracy report: uv run marketdata quality accuracy

## Rules
- Layers: api/, mcp_server/ and web/ → service/ → db/repositories/; ingest/ and webhooks/ → db/repositories/. Never skip a layer. SQL only in db/repositories/.
- extract/, verify/ and normalize/ (and content/ in E7b) are pure: no I/O. verify/ never imports extract/.
- The API, MCP server, web UI, proof pages and webhooks read clean tables only. They never call the SEC or the price vendor, and never read raw files.
- All SEC calls go through marketdata/sec/client.py (User-Agent, max 8 requests/second).
- Save the raw file before parsing it.
- Every served value carries a proof object (filing, source, checks); earnings facts also carry the exact source quote. Missing or unverified data is null plus a flag, never an estimate.
- A stored value never changes silently: write a value_changes row (old, new, reason) in the same transaction.
- Every extracted value records method and extractor_version.
- No LLM calls outside E12/E13. AI reads, it doesn't write.
- We store no end-user data: no accounts, trackers or third-party scripts in the web UI. We never contact a client's users.
- No licensed data (GICS, consensus estimates, news, transcripts) unless a license is recorded in docs/PLAN.md §15. Prices come only from the licensed vendor and appear only in channels its license allows.
- Financial values are Decimal / numeric, never float.
- Every write is an idempotent upsert.
- Unit tests use fixtures in tests/fixtures/; no network in unit tests; no live LLM calls in CI.
- No investment-advice wording anywhere: no buy/sell/hold, no targets, no rankings, scores or signals, no "you should".
- New dependency: add it to docs/PLAN.md §5 and check its license (no AGPL).
- Record every edge case you hit in docs/EDGE_CASES.md.
- Don't diverge from docs/PLAN.md silently: ask, or record the decision in §15.

## End of every session
Update docs/PLAN.md: §2 Status, §15 Decision log, §16 Follow-ups. Append to docs/EDGE_CASES.md. Then commit.
