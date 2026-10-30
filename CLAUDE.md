# marketdata

Trusted, verified data from SEC filings: proof on every value, cross-checks, published accuracy, announced changes.
SEC filings → raw store → extract / normalize / verify / cross-check → PostgreSQL → REST (/v1) with webhooks, MCP (/mcp), and a read-only web UI (/) with proof pages (/proof).
The full plan, rules and epics are in docs/PLAN.md. Before writing code, read §1–§11 and the epic you are working on.

## Commands
- Start database: docker compose up -d postgres (PostgreSQL 18 on host port 5433), or use a local PostgreSQL 18 on 5432
- Install: pdm install
- Migrate: pdm run alembic upgrade head
- Tests: pdm run pytest
- Lint and types: pdm run lint (black --check, isort --check-only, flake8, mypy)
- Format: pdm run fmt
- API: pdm run uvicorn marketdata.api.app:app --reload
- Worker: pdm run marketdata worker
- Scheduler: pdm run marketdata scheduler
- Extraction hit rate: pdm run marketdata extract report
- Rebuild metrics from stored facts: pdm run marketdata normalize --all
- Quality report: pdm run marketdata quality report
- Accuracy report: pdm run marketdata quality accuracy
- API keys: pdm run marketdata clients create NAME, then pdm run marketdata keys create NAME (shown once)
- Webhooks: pdm run marketdata webhooks create CLIENT URL (the secret is shown once), then webhooks test ID; the worker delivers

## Rules
- Layers: api/, mcp_server/, web/ and webhooks/ → service/ → db/repositories/; ingest/ → db/repositories/ (it writes webhook events in the transaction that changes the data). Never skip a layer. SQL only in db/repositories/.
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
- New dependency: add it to docs/PLAN.md §5 and check its license (no AGPL). Add it with pdm add and commit pdm.lock.
- Don't use uv or ruff: the dev machine's endpoint policy blocks them (D-041). Never work around the block.
- Record every edge case you hit in docs/EDGE_CASES.md.
- Don't diverge from docs/PLAN.md silently: ask, or record the decision in §15.

## End of every session
Update docs/PLAN.md: §2 Status, §15 Decision log, §16 Follow-ups. Append to docs/EDGE_CASES.md. Then commit.
