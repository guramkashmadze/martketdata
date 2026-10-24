# marketdata — Build Plan and AI Session Starter

Single source of truth for building the MVP with Claude Code. Every session starts here.

- Owner: Guram
- Created: 2026-10-12
- Last revised: 2026-10-23, third revision — the product is a buyer-agnostic, trusted-data engine with three interfaces (REST API with webhooks, MCP, our own web UI); the market is chosen later (D-030 to D-035). Earlier revisions on 2026-10-19 set the trust features, rule-based extraction with no LLM, and the licensed-data rule (D-010 to D-029).
- Architecture doc (background, diagrams): "Market Data Platform — MVP Architecture" in claude.ai. Where this file and that doc differ, **this file wins** (see §15 for the deliberate deviations).

---

## 0. How to use this file

1. Save this file as `docs/PLAN.md` in the repository. Copy Appendix A into `CLAUDE.md` at the repo root. Claude Code loads `CLAUDE.md` automatically; it points here.
2. **One Claude Code session = one epic.** E4, E5 and E7 are split into parts (E4a/E4b, E5a/E5b, E7a/E7b/E7c) and E11 and E15 take two sessions each, because they are too big for one context window.
3. Start each session with the prompt in Appendix B, filling in the epic ID.
4. Every session reads **§1–§11** plus its own epic in §12. Everything a session needs to know should be in those sections; if it isn't, the session asks, or records an assumption in §15.
5. At the end of every session, run the checklist in Appendix C: update §2 Status, append to §15 Decision log, add leftovers to §16 Follow-ups, and append edge cases to `docs/EDGE_CASES.md`.
6. Nobody (human or AI) silently diverges from this plan. Changes are made here first, with a decision log entry.
7. Epics are listed in §2 and §12 in **build order**, not in ID order. IDs stay stable because commits and the decision log use them.

---

## 1. Product context

**What we are building.** A trusted-data engine for US public-company filings. It collects filings from the SEC, extracts and unifies the data, verifies every value, and exposes it through three interfaces:

- a **REST API with webhooks and a change feed**, for any client that builds the data into its own product,
- an **MCP server**, so AI chats (Claude, ChatGPT, Gemini and others) can query the same data, and
- **our own web UI**, a simple read-only site where people can look up a company and see where every number came from.

**Who it is for (D-030).** The engine is built buyer-agnostic. The market is chosen after M1 and a round of discovery conversations (OD-12, §17). Candidates include banks and brokers with trading apps, fintechs, builders of AI tools, and retail investors directly. Nothing in the engine depends on that choice.

**What makes it different.** We don't compete on breadth: providers such as Financial Datasets already sell 40+ US datasets cheaply and are listed in Claude's and ChatGPT's connector directories (D-010). We compete on trust, which every buyer values and can check for themselves (D-024):

- **Proof per number:** the filing, the exact source quote or XBRL concept, and the checks it passed.
- **Independent cross-checks:** each earnings-release number is compared with the audited 10-Q/10-K when it arrives; the match rate is published (D-028).
- **Measured accuracy:** golden sets, an accuracy report per metric, and earnings-day latency, all from live data.
- **Changes are announced, never silent:** if a served value changes, clients get the old value, the new value and the reason (D-027).
- **Honest gaps:** null plus a reason, never a guess.

**How it is used.**

- **Systems** use the REST API, webhooks and change feed. When a client (a bank, a broker, an app) shows our data to its own users, that client owns the channel: we never contact its users (D-012, D-033).
- **AI chats** use the MCP server with an API key we issue. A public directory listing with OAuth is post-MVP.
- **People** use our web UI: read-only, no accounts, no trackers (D-034).
- **AI reads, it doesn't write.** No LLM in M1–M4. Later an LLM may help *find* numbers (code still verifies them); LLM-written text is a separate decision (D-014).
- **Optional layers** are built when needed: Georgian localization and client templates (E7b), LLM-assisted extraction (E12), company profiles (E13), ETF holdings (E14).

**The pipeline is the product.** Anyone can prompt a model. What a client would not want to own is SEC parsing and amendments, coverage upkeep, the verification and cross-check layer, on-call during four earnings seasons a year (most US results land around 00:00–01:00 Tbilisi time), and maintenance. We do that once and share the cost across clients. `docs/EDGE_CASES.md` is the evidence (D-021).

**Personas.**

| Persona | Who | What they need |
| --- | --- | --- |
| API integrator | Developer at a client: a bank, broker, fintech or app | Stable, documented REST API, webhooks and change feed with clear auth, errors and SLAs |
| Data evaluator | Person at a client who must trust a new source (data, risk or compliance) | Proof for any number, published accuracy, corrections that are announced |
| AI chat user | Person using an AI chat with our MCP server connected | Answers with numbers and source links, no advice |
| UI visitor | Investor or analyst using our web UI | A fast company page, numbers they can verify, no clutter, no advice |
| End user of a client | Retail investor in a client's app | Accurate information (sees us only through the client) |
| Operator | Guram | Runs the platform, monitors data quality and the track record, manages clients, keys and webhooks |

---

## 2. Status board

Sessions update this table. Status values: `Not started`, `In progress`, `Done`, `Blocked`. Rows are in build order (D-032).

| Epic | Title | Session | Depends on | Status | Notes |
| --- | --- | --- | --- | --- | --- |
| E0 | Foundation | 1 | — | In progress | Code, migration 0001, tests and CI workflow done (2026-10-24); tests and migrations pass on PostgreSQL 18 (Docker and local, D-040). Open: `uv.lock`, a ruff run and the first CI run on main, blocked because this Mac's endpoint policy kills uv and ruff (§16) |
| E1 | SEC client and raw store | 2 | E0 | Not started | |
| E2 | Security master and universe | 3 | E1, OD-3 | Not started | Classifies domestic filers, foreign issuers and funds |
| E3 | Task queue and filings watcher | 4 | E2 | Not started | Includes 13D/13G labels |
| E7a | Earnings release extraction and verification (rule-based) | 5 | E3 | Not started | No LLM (D-014) |
| E7c | Live run and track record | 6 | E7a, OD-1 | Not started | Starts the track record during the Q3 2026 season |
| E4a | XBRL facts and reported metrics | 7 | E3 | Not started | Includes the universe growth rule (D-035) |
| E4b | Derived metrics, quality checks, cross-checks | 8 | E4a, E7a | Not started | Release-vs-filing cross-check (D-028) |
| E5a | Data API | 9 | E4b | Not started | Clients, keys, envelope, all M1 data endpoints |
| E5b | Webhooks, change feed, proof pages and accuracy report | 10 | E5a | Not started | |
| E6 | MCP server | 11 | E5a | Not started | Milestone M1 |
| E10 | Prices, end-of-day and licensed | 12 | E5a, OD-4 | Not started | D-031 |
| E15 | Web UI | 13–14 | E5b, E6; E10 for price fields | Not started | Milestone M2 |
| E8 | Insider trades | 15 | E5a, E6, E15 | Not started | |
| E9 | 13F fund portfolios | 16 | E5a, E6, E15 | Not started | Milestone M3 |
| E11 | Operations and production readiness | 17–18 | E15, E9 | Not started | Milestone M4 |
| E7b | Georgian localization and content controls | on demand | E5a, OD-6, OD-8, OD-9 | Not started | When a client or our UI needs Georgian (D-026) |
| E12 | LLM fallback extractor and guidance direction | later | E7a report, OD-2, OD-10 | Not started | Only if the hit rate needs it |
| E13 | Georgian company profiles and filing sections | later | E6, E7b, OD-2, a §15 decision on LLM text | Not started | LLM drafts for editors |
| E14 | ETF holdings (N-PORT) | later | E2, E5a | Not started | |

---

## 3. Scope, non-goals and guardrails

**MVP scope**

- **Universe:** start with about 30 domestic US filers chosen for variety, plus a few ETFs and foreign issuers to exercise classification (OD-3, §9.1). Grow toward the roughly 500 largest domestic filers by public float as reported on their 10-K cover pages, which needs no license (D-035). Data is produced for domestic SEC filers (10-K/10-Q); ETFs and foreign private issuers resolve as securities with a clear "not applicable" reason (D-018).
- **M1 — trusted data engine:**
  - verified earnings-release facts (revenue, operating income, net income, diluted EPS and their year-over-year change), live during earnings season, with a measured track record;
  - verified financials for the core metric set (D-020), with each release number cross-checked against the later 10-Q/10-K (D-028);
  - the filings feed with plain-language 8-K and 13D/13G event labels;
  - the REST API, webhooks, change feed, proof pages and a published accuracy report;
  - the MCP server.
- **M2 — our own UI:** licensed end-of-day prices (E10) and the read-only web UI (E15).
- **M3 — breadth:** insider trades (Form 4) and curated 13F fund portfolios, in all three interfaces.
- **M4 — production readiness:** hardened operations, integration, security and privacy docs.
- **On demand:** Georgian localization and content controls (E7b), LLM fallback extraction (E12), company profiles (E13), ETF holdings (E14).
- **Interfaces:** REST `/v1` with webhooks, MCP `/mcp`, web UI `/` with proof pages `/proof`, admin CLI.

**Non-goals for the MVP**

- Congressional trading data. The Ethics in Government Act bans commercial use of these disclosure reports except by news media; not worth the legal risk now.
- A broad US data catalogue (D-010): no as-reported statements, segments, screener, macro data, news, IPO feed or natural-language search.
- Generated free text of any kind (D-014). LLM drafts come later (E13) and need a decision recorded in §15.
- Rankings, scores, "top movers", signals or anything that reads as a recommendation, even under an "intelligence" label (D-029).
- Licensed data without a recorded license (D-017): prices outside the license recorded for E10, GICS sectors, consensus estimates, analyst ratings, news articles, earnings call transcripts.
- Real-time or intraday prices (D-031).
- Fundamentals for ETFs and foreign private issuers. They resolve as securities; foreign issuers' earnings (6-K) and ETF holdings (E14) come later.
- Non-US companies, including Georgian companies listed in London (post-MVP).
- Full financial statements (only the metric catalog in §9.3).
- User accounts, watchlists, portfolios, alerts or personalization in our UI, and collecting end-user data of any kind (D-033, D-034).
- Public listing of the MCP server in AI chat directories, and OAuth (post-MVP).
- Investment recommendations of any kind.

**Guardrails (apply to every epic)**

1. **Information, not advice.** No buy/sell/hold ratings, price targets, scores, or "you should" wording anywhere, including UI labels, docs and sample material. Every UI page and API response carries the disclaimer.
2. **No end-user data.** API and MCP requests carry tickers and parameters only. The web UI has no accounts, no trackers and no cookies beyond what access control needs. We store no identities, holdings or behaviour.
3. **Proof on everything.** Every served value carries the `accession_no` of the filing it came from and a proof object (§9.10); earnings-release facts also carry the exact source quote. In the UI, every number links to its proof page.
4. **Never fabricate.** Missing data is returned as `null` plus a quality flag, never estimated. A value that fails verification is not served.
5. **Changes are never silent (D-027).** A stored value that changes is logged with its old value, new value and reason, and announced.
6. **SEC fair access.** Declared User-Agent; at most 8 requests per second across the whole platform.
7. **Licenses.** Check every new dependency's license; no AGPL code in the served product. No licensed data (D-017) without a license recorded in §15. Prices are shown only in channels the vendor license allows (D-031).
8. **Clients own their channel (D-012, D-033).** When a client shows our data to its users, we never contact those users. Links we provide point to sec.gov or to our proof pages.
9. **AI reads, it doesn't write (D-014).** No LLM calls outside E12 and E13. Any LLM-written text meant for readers needs a decision recorded in §15, and the approval of any client that shows it.

---

## 4. Architecture

```
 SEC APIs · EDGAR archive · SEC bulk datasets · OpenFIGI · price vendor (E10)     (LLM API: later, E12/E13 only)
        │
        ▼
 scheduler ──► filings_watcher ──► tasks (Postgres queue) ──► worker
                                                                │ 1. save original file
                                                                ▼
                                                 raw store (S3-compatible / local folder)
                                                                │ 2. extract or parse, normalize, verify, cross-check
                                                                ▼
                                    PostgreSQL (clean tables + proof + value changes + webhook outbox)
                                                                │
                                                                ▼
                                                 service/ (shared query functions)
                     │                     │                     │                     │
                     ▼                     ▼                     ▼                     ▼
               REST /v1            webhooks (outbox)         MCP /mcp        web UI / and /proof (E15)
                     │                     │                     │                     │
              client systems        client systems           AI chats         people in a browser
```

**Processes** (Docker Compose services)

| Service | What it runs |
| --- | --- |
| `api` | FastAPI app serving `/health`, `/v1/*`, `/proof/*`, `/mcp` and, from E15, the web UI at `/` (uvicorn) |
| `worker` | Claims tasks from the queue and runs their handlers, including webhook delivery and price loads. **One process** with async concurrency in the MVP, so the SEC rate limit lives in one place (D-007) |
| `scheduler` | APScheduler; only enqueues tasks, never does the work itself |
| `postgres` | PostgreSQL 18 (D-040) |
| `caddy` | Reverse proxy with automatic TLS (live VM and production) |

**Flow for one earnings release**

1. `filings_watcher` reads a company's submissions JSON, finds an unseen 8-K with item 2.02, inserts a `filings` row (`ON CONFLICT DO NOTHING`) and enqueues `earnings_extract`.
2. The worker downloads the filing index and the press release exhibit, **writes them to the raw store first**, extracts the income-statement table, verifies every value, and stores `earnings_releases` and `earnings_facts`.
3. The release is served immediately with its proof in the API, MCP and UI, and (from E5b) `earnings.verified` is sent to subscribed clients.
4. Weeks later the 10-Q or 10-K arrives: its XBRL values are loaded (E4a) and each release value gets a cross-check status (E4b, D-028). A mismatch is flagged and counted in the accuracy report.
5. If a stored value changes (a corrected rule, an amended release, a restatement), the change is logged in `value_changes` and `data.changed` is sent (D-027).
6. The API, MCP server, web UI, proof pages and webhooks read **only** clean tables. They never read raw files and never call the SEC or the price vendor.

**Task routing**

| Form or trigger | Tasks enqueued | From |
| --- | --- | --- |
| 10-K, 10-Q, 10-K/A, 10-Q/A | `facts_refresh` (key: cik) | E4a |
| 8-K or 8-K/A with item 2.02 | `earnings_extract` (key: accession_no) | E7a |
| Other 8-K | none (items are stored on the `filings` row) | E3 |
| SC 13D, SC 13G and their structured successors | none (form-level event labels, §9.6) | E3 |
| 4, 4/A | `form4_parse` (key: accession_no) | E8 |
| 13F-HR, 13F-HR/A from a curated manager | `f13_parse` (key: accession_no) | E9 |
| Scheduler, after the US close | `prices_eod` (key: trade date) | E10 |
| (internal) | `webhook_deliver` (key: `event_id:dest_id`) | E5b |

Only tasks whose handler is registered are enqueued, so early epics don't fill the queue with work nobody can do yet.

---

## 5. Tech stack

| Concern | Choice | Notes |
| --- | --- | --- |
| Language | Python 3.12 | Type hints everywhere (D-001) |
| Packaging | uv + `pyproject.toml`; build backend hatchling | Lockfile committed. uv MIT/Apache-2.0, hatchling MIT |
| Lint and format | ruff | `ruff check` + `ruff format`. MIT |
| Types | mypy `--strict`, with the pydantic plugin | CI gate. MIT |
| Tests | pytest, pytest-asyncio; httpx for FastAPI's TestClient | No network in unit tests. MIT, Apache-2.0 |
| CLI | typer | Entry point `marketdata`. MIT |
| HTTP client | httpx (async) | |
| Retries | tenacity | |
| Rate limiting | Own async token bucket in `sec/client.py` | Process-wide; see D-007 |
| Database | PostgreSQL 18 | D-002, D-040. Images pinned to the major (`postgres:18`), never `latest` |
| DB driver | psycopg 3 + psycopg_pool | Plain SQL in `db/repositories/` (Dapper style). LGPL-3.0, used as an unmodified library: allowed (not AGPL) |
| Migrations | Alembic, SQL written with `op.execute` | MIT. Pulls in SQLAlchemy (MIT) only as its migration runner; our code uses no ORM |
| Models and config | Pydantic v2, pydantic-settings | MIT |
| XML / HTML | lxml; selectolax for HTML-to-text | Press release tables parsed with lxml (E7a) |
| HTML pages and templates | Jinja2: proof pages (E5b), web UI (E15), sandboxed client templates (E7b) | BSD-3 |
| Web UI interactivity | htmx for server-rendered partials, Chart.js for charts; both vendored as static files with pinned versions | Permissive (BSD-family, MIT); verify in E15 (D-034) |
| Prices | `PriceProvider` interface; first real adapter per OD-4 | E10 (D-031) |
| Webhook signing | stdlib `hmac` / `hashlib`; secrets encrypted at rest with `cryptography` (Fernet) | Apache-2.0 / BSD (E5b) |
| API | FastAPI + uvicorn | MIT, BSD-3 |
| MCP | Official `mcp` Python SDK, Streamable HTTP, mounted in the FastAPI app | |
| Scheduler | APScheduler | |
| Object storage | boto3 against the S3 API; local-folder adapter for dev | Cloudflare R2 or Backblaze B2 in production |
| Logging | structlog (JSON in live and prod, pretty in dev) | MIT/Apache-2.0. Standard-library loggers (uvicorn, alembic, psycopg) share its formatter; logs go to stderr |
| Error tracking | sentry-sdk | Live VM and production |
| LLM | **Not used in M1–M4 (D-014).** From E12/E13: `LLMClient` interface; first adapter uses the Anthropic SDK | Model from config; OD-2 |
| Exploration | Jupyter, pandas, edgartools — in `notebooks/` only | Core code does not import them (D-009) |
| CI | GitHub Actions: ruff, mypy, pytest with a Postgres service | |
| Deploy | Docker Compose on one VM, Caddy for TLS | OD-1 |

Adding a dependency: add it to this table in the same session and check its license.

---

## 6. Engineering conventions and Definition of Done

**Repository layout**

```
marketdata/
  config.py              settings (pydantic-settings)
  log.py                 structlog setup (E0)
  cli.py                 typer CLI: ingest, backfill, extract, normalize, universe, tasks, clients, keys,
                         webhooks, quality, stats, usage, sec (E7b adds: review, templates)
  db/
    pool.py              connection pool
    migrations/          Alembic
    repositories/        one module per table group, plain SQL only
  sec/
    client.py            rate limiter, retries, User-Agent
    urls.py              URL builders (§8)
    raw_store.py         RawStore interface, LocalRawStore, S3RawStore
  ingest/                security_master.py, watcher.py, earnings.py, facts.py, prices.py, form4.py, f13.py
  extract/               interface.py (Extractor), html_tables.py, earnings_rules.py, columns.py, units.py
  verify/                number_check.py, sanity.py, cross_check.py
  normalize/             metric_catalog.py, periods.py, metrics.py, changes.py, insiders.py, holdings.py, quality.py
  prices/                E10: provider.py (PriceProvider), fake.py, <vendor>.py
  webhooks/              outbox.py, signing.py, deliver.py
  content/               E7b only: names_ka.py, periods_ka.py, numbers_ka.py, templates.py
  llm/                   E12/E13 only: client.py, anthropic_client.py, prompts/
  tasks/                 queue.py, registry.py, worker.py, scheduler.py
  service/               companies.py, earnings.py, financials.py, filings.py, compare.py, changes.py,
                         prices.py, status.py, clients.py, insiders.py, funds.py
  api/                   app.py, auth.py, envelope.py, errors.py, routes/, proof_pages/
  mcp_server/            server.py, tools.py
  web/                   E15: routes.py, templates/, static/ (vendored htmx and Chart.js)
data/                    universe.csv, extraction_overrides.yaml, managers.csv
                         (E7b: company_names_ka.csv, glossary_ka.csv, templates/)
notebooks/               exploration only
scripts/                 fetch_fixture.py and other one-off tools
tests/
  unit/  integration/  golden/ (incl. golden/earnings/)  fixtures/sec/
docs/                    PLAN.md, EDGE_CASES.md, DEMO.md, MCP.md, INTEGRATION.md, SECURITY.md, DEPLOY.md
.github/workflows/ci.yml
docker-compose.yml  Dockerfile (E7c)  pyproject.toml  uv.lock  .python-version  alembic.ini
.env.example  README.md  CLAUDE.md
```

Sales material and discovery notes stay outside the repository (§17).

**Rules**

- **Layers:** `api/`, `mcp_server/` and `web/` → `service/` → `db/repositories/`. `ingest/` and `webhooks/` → `db/repositories/`; `ingest/prices.py` uses the `prices/` adapters. No layer skips another. SQL lives only in `db/repositories/`.
- **Pure modules:** `extract/`, `verify/` and `normalize/` (and `content/` in E7b) take records and return records. No I/O inside them, so they can be unit-tested with fixtures.
- **Verification is independent of extraction (D-015).** `verify/` never imports from `extract/`; it checks values against the stored document, whoever produced them.
- **Time:** all timestamps are UTC `timestamptz`; period boundaries are `date`.
- **Numbers:** reported values are `numeric` in Postgres and `Decimal` in Python. Never `float` for financial values.
- **Identifiers:** CIK stored as `bigint`, zero-padded to 10 digits only when building SEC URLs. Accession numbers stored with dashes, e.g. `0000320193-24-000123`.
- **Idempotency:** every write is an upsert on a natural key. Running any task twice gives the same rows.
- **Value changes (D-027):** an upsert that changes an already stored value in `earnings_facts` or `financial_metrics` also writes a `value_changes` row (old value, new value, reason) in the same transaction. `normalize/changes.py` decides the reason.
- **Errors:** service functions raise domain exceptions (`NotFound`, `InvalidParameter`, `NotApplicable`, `Forbidden`); `api/errors.py` maps them to the error envelope (§10).
- **Logging:** structured, with `cik`, `accession_no`, `task_id`, `client_id` fields where relevant.
- **Secrets:** only from environment variables. Never in code, fixtures or logs. Webhook signing secrets are stored encrypted.
- **Edge cases:** every parser surprise, override, amendment oddity or failed check that teaches us something is appended to `docs/EDGE_CASES.md` (date, epic, company and accession, what broke, how it was handled).

**Testing**

- Parsers and extractors are tested with real SEC files saved in `tests/fixtures/sec/` (use `scripts/fetch_fixture.py`). Keep fixtures small (total under 20 MB; trim large JSON where possible).
- Integration tests (`tests/integration/`, marker `integration`) run against PostgreSQL at `TEST_DATABASE_URL`. Its database name must end in `_test`: each session drops and recreates the schema with Alembic, and each test runs in a transaction that is rolled back. They fail rather than skip when PostgreSQL is down. `pytest -m "not integration"` runs the unit tests alone.
- `tests/unit/test_layers.py` enforces the layer rules by reading imports: `verify/` never imports `extract/`, pure modules import nothing that does I/O, interfaces never import `db/repositories/`, and SQL string literals appear only in `db/repositories/` and migrations.
- Golden tests (`tests/golden/*.yaml`, `tests/golden/earnings/*.yaml`) hold hand-checked values, each with the SEC URL it was checked against. They are the accuracy contract.
- The four-quarter earnings backfill is the extraction test set; `marketdata extract report` measures the hit rate on it (§9.9).
- LLM tests (E12/E13 only) use `FakeLLMClient` with recorded responses; no live LLM calls in CI.

**Definition of Done (per story)**

- Acceptance criteria met and shown (a test, or a command with its output).
- `ruff check`, `ruff format --check`, `mypy --strict`, `pytest` all green.
- Migrations included; `downgrade` works where practical.
- New settings added to `.env.example` and §11.
- Edge cases found are appended to `docs/EDGE_CASES.md`.
- §2, §15 and §16 updated at the end of the session.

---

## 7. Data model (target schema)

Each epic creates the tables it needs in its own migration. This is the target; column changes go through §15.

```sql
-- E0
create table tasks (
  task_id      bigserial primary key,
  kind         text not null,
  key          text not null,                -- dedupe key: accession_no, cik, event_id:dest_id ...
  payload      jsonb not null default '{}',
  status       text not null default 'queued'
               check (status in ('queued','running','done','failed','dead')),
  attempts     int not null default 0,
  run_after    timestamptz not null default now(),
  locked_until timestamptz,                   -- reclaim if a worker dies
  last_error   text,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),
  unique (kind, key)
);
create index tasks_ready on tasks (status, run_after);

create table clients (                        -- an API client: a company, an app, or the operator's own test client
  client_id   bigserial primary key,
  name        text not null unique,
  created_at  timestamptz not null default now()
);

create table api_keys (
  key_id         bigserial primary key,
  client_id      bigint not null references clients(client_id),
  scope          text not null default 'api' check (scope in ('api','review')),  -- 'review' used from E7b
  key_hash       text not null unique,        -- sha256 of the key; the key itself is shown once
  rate_per_min   int not null default 120,
  created_at     timestamptz not null default now(),
  revoked_at     timestamptz
);

create table api_requests (
  id           bigserial primary key,
  key_id       bigint references api_keys(key_id),
  at           timestamptz not null default now(),
  method       text not null,
  path         text not null,
  status       int not null,
  duration_ms  int not null
);

-- E1
create table raw_objects (
  key           text primary key,             -- path in the raw store
  url           text not null,
  sha256        text not null,
  size_bytes    bigint not null,
  content_type  text,
  fetched_at    timestamptz not null default now()
);

-- E2
create table companies (
  cik              bigint primary key,
  name             text not null,
  sic_code         text,
  sic_description  text,
  fiscal_year_end  text,                      -- 'MMDD' as the SEC gives it
  state_of_inc     text,
  filer_type       text not null default 'unknown'
                   check (filer_type in ('domestic','foreign_private_issuer','fund','other','unknown')),
  in_universe      boolean not null default false,
  updated_at       timestamptz not null default now()
);

create table securities (
  security_id    bigserial primary key,
  cik            bigint not null references companies(cik),
  ticker         text not null,               -- as the SEC lists it, e.g. 'BRK-B'
  security_type  text not null default 'common'
                 check (security_type in ('common','preferred','fund','adr','other')),
  exchange       text,
  share_class    text,
  cusip          text,
  figi           text,
  valid_from     date not null,
  valid_to       date,                        -- null = current
  unique (ticker, valid_from)
);

-- E3
create table filings (
  accession_no      text primary key,
  cik               bigint not null,          -- filer as listed in submissions (issuer or manager)
  form              text not null,
  filed_at          date not null,
  accepted_at       timestamptz,
  period_of_report  date,
  items_8k          text[],                   -- e.g. '{2.02,9.01}'
  primary_doc       text,
  is_xbrl           boolean,
  discovered_at     timestamptz not null default now()
);
create index filings_by_cik on filings (cik, filed_at desc);

-- E7a
create table documents (
  accession_no  text not null,
  doc_name      text not null,
  doc_type      text not null,                -- e.g. EX-99.1
  raw_key       text not null references raw_objects(key),
  text          text not null,                -- tables converted to markdown
  extracted_at  timestamptz not null default now(),
  primary key (accession_no, doc_name)
);

create table earnings_releases (
  accession_no       text primary key,
  cik                bigint not null references companies(cik),
  accepted_at        timestamptz,
  doc_name           text,                    -- the press release exhibit used
  fiscal_year        int,                     -- D-008 labels
  fiscal_period      text,                    -- Q1..Q4
  period_end         date,                    -- current-quarter column date from the table header
  period_label       text,                    -- as the company states it
  status             text not null check (status in ('complete','partial','failed')),
  extractor          text not null,           -- 'rules' (E7a), 'llm' (E12)
  extractor_version  text not null,
  check_report       jsonb not null,
  verified_at        timestamptz,
  updated_at         timestamptz not null default now()
);

create table earnings_facts (
  accession_no       text not null references earnings_releases(accession_no),
  slot               text not null,           -- revenue, operating_income, net_income, eps_diluted
  value              numeric,                 -- base units (USD, USD/shares); null if not found or failed
  unit               text not null,
  prior_value        numeric,                 -- same quarter one year earlier, from the same table
  change_pct         numeric,                 -- computed by code (§9.9)
  basis              text not null default 'GAAP',
  source_row_label   text,                    -- row label as printed
  source_quote       text,                    -- row label and cells as printed
  scale_text         text,                    -- e.g. 'in millions, except per share amounts'
  method             text not null,           -- 'rule' or 'llm'
  extractor_version  text not null,
  check_status       text not null check (check_status in ('passed','failed','not_found','not_applicable')),
  cross_check_status     text not null default 'pending'          -- E4b, D-028
                         check (cross_check_status in ('pending','matched','mismatch','not_comparable')),
  cross_check_value      numeric,              -- XBRL value from the later 10-Q/10-K
  cross_check_accession  text,                 -- that filing
  quality_flags      text[] not null default '{}',
  primary key (accession_no, slot)
);

create table value_changes (                  -- D-027: stored values never change silently
  change_id   bigserial primary key,
  entity      text not null check (entity in ('earnings_fact','financial_metric')),
  entity_key  jsonb not null,                 -- e.g. {"accession_no": "...", "slot": "revenue"}
  cik         bigint not null,
  old_value   numeric,
  new_value   numeric,
  reason      text not null check (reason in ('correction','restatement','amendment')),
  detail      text,                           -- e.g. rules version change, override added, the amending filing
  changed_at  timestamptz not null default now()
);
create index value_changes_by_time on value_changes (changed_at);

-- E5b
create table webhook_destinations (
  dest_id               bigserial primary key,
  client_id             bigint not null references clients(client_id),
  url                   text not null,        -- https only
  secret_enc            text not null,        -- signing secret, encrypted with WEBHOOK_ENCRYPTION_KEY
  event_types           text[] not null,
  active                boolean not null default true,
  consecutive_failures  int not null default 0,
  created_at            timestamptz not null default now()
);

create table webhook_events (
  event_id    uuid primary key,
  type        text not null,
  client_id   bigint references clients(client_id),   -- null = every subscribed client
  livemode    boolean not null default true,
  payload     jsonb not null,
  created_at  timestamptz not null default now()
);

create table webhook_deliveries (
  event_id      uuid not null references webhook_events(event_id),
  dest_id       bigint not null references webhook_destinations(dest_id),
  attempt       int not null,
  status_code   int,
  error         text,
  attempted_at  timestamptz not null default now(),
  primary key (event_id, dest_id, attempt)
);

-- E4a
create table xbrl_facts (
  id            bigserial primary key,
  cik           bigint not null,
  taxonomy      text not null,                -- us-gaap, dei
  concept       text not null,
  unit          text not null,                -- USD, shares, USD/shares
  period_start  date,                         -- null for instant facts
  period_end    date not null,
  value         numeric not null,
  fy            int,
  fp            text,
  form          text,
  frame         text,                         -- e.g. CY2024Q1, CY2024Q1I
  filed_at      date not null,
  accession_no  text not null,
  unique nulls not distinct (cik, taxonomy, concept, unit, period_start, period_end, accession_no)
);
create index xbrl_facts_lookup on xbrl_facts (cik, concept, period_end);

create table financial_metrics (
  cik                bigint not null references companies(cik),
  metric             text not null,
  period_type        text not null check (period_type in ('Q','FY','TTM')),
  fiscal_year        int not null,
  fiscal_period      text not null,           -- Q1..Q4, FY
  period_start       date,
  period_end         date not null,
  value              numeric,                 -- null allowed, with a quality flag
  unit               text not null,
  method             text not null check (method in
                     ('reported','derived_ytd','derived_q4','derived_ttm','computed')),
  concept            text,                    -- XBRL concept used; null for computed
  accession_no       text,                    -- latest contributing filing
  source_accessions  text[] not null,         -- all contributing filings
  filed_at           date,                    -- filing date of accession_no (keeps point-in-time possible later)
  quality_flags      text[] not null default '{}',
  calc_version       text not null,           -- version of the normalization rules
  updated_at         timestamptz not null default now(),
  primary key (cik, metric, period_type, period_end)
);

-- E8
create table insider_owners (
  accession_no      text not null,
  owner_cik         bigint not null,
  owner_name        text not null,
  is_director       boolean,
  is_officer        boolean,
  officer_title     text,
  is_ten_pct_owner  boolean,
  primary key (accession_no, owner_cik)
);

create table insider_transactions (
  accession_no       text not null,
  line_no            int not null,            -- order within the filing
  issuer_cik         bigint not null,
  period_of_report   date,
  security_title     text,
  is_derivative      boolean not null,
  trade_date         date not null,
  code               text not null,           -- P, S, A, M, F, G, D, ...
  acquired_disposed  char(1),                 -- A or D
  shares             numeric,
  price              numeric,
  owned_after        numeric,
  direct_indirect    char(1),                 -- D or I
  is_10b5_1          boolean,                 -- null when unknown (pre-2023)
  footnotes          jsonb,
  superseded_by      text,                    -- accession_no of the 4/A that replaced it
  primary key (accession_no, line_no)
);
create index insider_by_issuer on insider_transactions (issuer_cik, trade_date desc);

-- E9
create table managers (
  manager_cik   bigint primary key,
  name          text not null,                -- as filed
  display_name  text not null,                -- e.g. 'Berkshire Hathaway'
  curated       boolean not null default true,
  notes         text
);

create table cusip_map (
  cusip        text primary key,
  security_id  bigint references securities(security_id),
  figi         text,
  ticker       text,
  matched_at   timestamptz not null default now(),
  status       text not null check (status in ('matched','unmatched','ambiguous'))
);

create table holdings_raw (
  accession_no           text not null,
  line_no                int not null,
  manager_cik            bigint not null references managers(manager_cik),
  report_period          date not null,
  name_of_issuer         text,
  title_of_class         text,
  cusip                  text not null,
  shares                 numeric,
  share_type             text,                -- SH or PRN
  value_usd              numeric not null,    -- normalized to dollars
  put_call               text,                -- null, PUT or CALL
  investment_discretion  text,
  primary key (accession_no, line_no)
);

create table fund_positions (                -- after amendments and merging
  manager_cik        bigint not null,
  report_period      date not null,
  cusip              text not null,
  security_id        bigint,
  shares             numeric not null,
  value_usd          numeric not null,
  weight_pct         numeric not null,
  change_type        text not null check (change_type in
                     ('new','added','trimmed','unchanged','exited')),
  shares_change      numeric,
  source_accessions  text[] not null,
  primary key (manager_cik, report_period, cusip)
);

-- E10 (D-031)
create table price_vendors (
  vendor          text primary key,
  license_ref     text not null,                -- the §15 decision that records the terms
  display_scopes  text[] not null,              -- channels where its prices may be shown: '{ui,mcp,api}'
  attribution     text,                         -- text the license requires next to prices
  delay           text not null                 -- 'eod' or e.g. '15min'
);

create table prices_eod (
  security_id  bigint not null references securities(security_id),
  trade_date   date not null,
  open         numeric,
  high         numeric,
  low          numeric,
  close        numeric not null,
  adj_close    numeric,
  volume       bigint,
  vendor       text not null references price_vendors(vendor),
  loaded_at    timestamptz not null default now(),
  primary key (security_id, trade_date)
);

-- E7b (on demand)
create table client_settings (
  client_id         bigint primary key references clients(client_id),
  auto_approve      boolean not null default false,
  paused            boolean not null default false,   -- kill switch for rendered content
  disabled_kinds    text[] not null default '{}',
  disabled_tickers  text[] not null default '{}'
);

create table company_names_ka (               -- loaded from data/company_names_ka.csv
  cik       bigint primary key references companies(cik),
  forms     jsonb not null,                   -- Georgian noun forms: {"nom": …, "erg": …, "gen": …, "dat": …}
  reviewed  boolean not null default false
);

create table content_reviews (
  client_id     bigint not null references clients(client_id),
  content_kind  text not null,                -- 'earnings_card' (later: 'profile')
  content_id    text not null,                -- accession_no for earnings
  status        text not null check (status in ('pending','approved','rejected','retracted')),
  reviewer      text,                         -- name entered by the client's reviewer
  note          text,
  updated_at    timestamptz not null default now(),
  primary key (client_id, content_kind, content_id)
);

create table client_templates (
  client_id   bigint not null references clients(client_id),
  kind        text not null,                  -- 'earnings_card', 'earnings_push'
  lang        text not null check (lang in ('ka','en')),
  version     int not null,
  body        text not null,                  -- sandboxed Jinja2 using localized fields only (§9.13)
  created_at  timestamptz not null default now(),
  primary key (client_id, kind, lang, version)
);

-- E13 (later)
create table drafts (
  kind            text not null,              -- 'profile'
  subject_id      text not null,              -- cik for profiles
  lang            text not null check (lang in ('en','ka')),
  prompt_version  text not null,
  model           text not null,
  body            jsonb not null,
  check_status    text not null check (check_status in ('passed','failed')),
  check_report    jsonb,
  created_at      timestamptz not null default now(),
  primary key (kind, subject_id, lang, prompt_version)
);

-- E14 (later): fund_series (series/class ↔ ticker) and nport_holdings, defined in that epic.
```

---

## 8. Data source reference

**SEC rules**

- Every request sends `User-Agent: <app name> <contact email>` from `SEC_USER_AGENT`. The app refuses to start without it.
- Global limit: `SEC_MAX_RPS` (default 8; the SEC allows about 10).
- On 429 or 5xx: exponential backoff with jitter, max 5 attempts, then the task fails and is retried later by the queue.
- The SEC's APIs need no API key. Submissions JSON typically updates within a second of a filing being disseminated; XBRL APIs within a minute ([SEC EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)). Polling is free and fast enough for us; the SEC's real-time push feed (PDS) is a paid subscription and not needed.

**Endpoints**

| Purpose | URL pattern | Notes |
| --- | --- | --- |
| Ticker ↔ CIK list | `https://www.sec.gov/files/company_tickers_exchange.json` | Fields: cik, name, ticker, exchange |
| Fund and ETF tickers | `https://www.sec.gov/files/company_tickers_mf.json` | cik, series id, class id, symbol. Used to classify funds (E2). Verify fields in E2 |
| Filing history | `https://data.sec.gov/submissions/CIK{cik10}.json` | `filings.recent` holds columnar arrays: accessionNumber, filingDate, reportDate, acceptanceDateTime, form, items, primaryDocument, isXBRL. Older pages listed in `filings.files` |
| All XBRL facts of a company | `https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json` | `facts[taxonomy][concept].units[unit][]` with start, end, val, accn, fy, fp, form, filed, frame. No dimensional (segment) facts |
| One concept (debugging) | `https://data.sec.gov/api/xbrl/companyconcept/CIK{cik10}/us-gaap/{concept}.json` | |
| One concept across companies (sanity checks) | `https://data.sec.gov/api/xbrl/frames/us-gaap/{concept}/{unit}/CY{yyyy}Q{q}[I].json` | `I` suffix = instant |
| Bulk facts | `https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip` | Rebuilt nightly around 3:00 ET |
| Bulk submissions | `https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip` | Rebuilt nightly |
| Filing folder | `https://www.sec.gov/Archives/edgar/data/{cik}/{accession_nodash}/` | Add `index.json` for a file list |
| Filing index page | `https://www.sec.gov/Archives/edgar/data/{cik}/{accession_nodash}/{accession_no}-index.htm` | Document table includes each document's **Type** (e.g. EX-99.1). Verify in E7a |
| Insider bulk data | [Insider Transactions Data Sets](https://www.sec.gov/dera/data/form-345) | Quarterly ZIPs of Forms 3/4/5, flattened, from 2006 |
| 13F bulk data | [Form 13F Data Sets](https://www.sec.gov/dera/data/form-13f) | Released for three-month windows |
| Fund holdings (E14) | SEC Form N-PORT data sets | Public with a lag of about two months; verify current rules in E14 |
| CUSIP mapping | `POST https://api.openfigi.com/v3/mapping` | `idType: ID_CUSIP`. A free API key raises limits; check current limits in E9 |
| Prices (E10) | Licensed vendor chosen in OD-4 | Terms recorded in §15 before any load; display scopes per §9.12 |

**Known quirks to verify in the epic that uses them**

- 8-K earnings releases are usually Exhibit 99.1, sometimes 99 or 99.2. Some companies split the income statement across tables or put it in a later exhibit. (E7a)
- Press release tables: units stated in a caption or header ("in millions, except per share data"), negatives in parentheses, dashes for zero or nil, `$` and `%` in separate cells, footnote markers in labels, quarter and year-to-date columns side by side. (E7a)
- Schedules 13D and 13G: structured filings since January 2025; the form codes may differ from the old `SC 13D` / `SC 13G` (e.g. `SCHEDULE 13D`). Also check whether an issuer's submissions JSON lists them when the company is the subject rather than the filer. (E3)
- Form 4: `primaryDocument` in submissions often points at a rendered path like `xslF345X05/form4.xml`. The raw XML is usually the same filename without the `xsl…/` folder. (E8)
- 13F: the information table is a separate XML document in the filing; find it through the filing index (type `INFORMATION TABLE`). (E9)
- `dei:EntityPublicFloat` is reported once a year on the 10-K cover page, measured at the end of the second fiscal quarter; some filers omit it or report it oddly. The universe growth rule (D-035) must handle missing and stale values. (E4a)

---

## 9. Domain rules

### 9.1 Universe

- `data/universe.csv` with columns `ticker,note`, maintained by hand (OD-3). The starter list is about 30 domestic filers chosen for variety (at least a bank, an insurer, a REIT, a January year-end retailer, a September year-end company and a company that reports in thousands), plus a few ETFs and foreign issuers.
- **Growth (D-035):** after E4a, `marketdata universe suggest --top N` ranks domestic filers by their latest `dei:EntityPublicFloat` and writes a candidate list for review. Index membership lists are not used, to avoid depending on index providers' data.
- Each ticker is classified on load (D-018): `domestic` (files 10-K/10-Q), `foreign_private_issuer` (files 20-F, 6-K or 40-F), `fund` (listed in the SEC fund ticker file, or files N-PORT/N-CSR), `other`. Nothing is rejected; the loader prints a count per class.
- In the MVP, earnings facts and fundamentals are produced for `domestic` companies only. Other classes resolve and return a clear reason, e.g. "fund: fundamentals not applicable".

### 9.2 Security master

- Ticker → CIK from `company_tickers_exchange.json` (and `company_tickers_mf.json` for funds); company details (name, SIC, fiscal year end) from submissions JSON.
- Several tickers can map to one CIK (e.g. two share classes). Each gets its own `securities` row.
- Ticker input is case-insensitive, and `.`, `-` and `/` are treated as the same class separator: `brk.b`, `BRK-B` and `BRK/B` all resolve to the SEC's `BRK-B`.
- When a ticker disappears from the SEC list or moves to another CIK, close the old row (`valid_to`) and open a new one.
- Sector information comes from SIC codes only. No GICS (D-017).

### 9.3 Metric catalog

Defined in code (`normalize/metric_catalog.py`), versioned with `CATALOG_VERSION` (D-005). Concepts are tried in the order listed; all are `us-gaap` unless marked `dei`.

**Core set (D-020, called the core set there):** revenue, operating_income, net_income, eps_diluted, operating_cash_flow, capex, cash, long_term_debt, total_equity, shares_outstanding, plus the computed free_cash_flow, margins and year-over-year growth. Golden tests and API defaults cover the core set first; the other metrics are built in the same pass but have lower priority.

| Metric | Kind | Unit | Concepts in priority order | Notes |
| --- | --- | --- | --- | --- |
| `revenue` | flow | USD | Revenues; RevenueFromContractWithCustomerExcludingAssessedTax; RevenueFromContractWithCustomerIncludingAssessedTax; SalesRevenueNet | See financials rule in §9.4 |
| `cost_of_revenue` | flow | USD | CostOfRevenue; CostOfGoodsAndServicesSold; CostOfGoodsSold | |
| `gross_profit` | flow | USD | GrossProfit | Else computed: revenue − cost_of_revenue |
| `rnd_expense` | flow | USD | ResearchAndDevelopmentExpense | |
| `sga_expense` | flow | USD | SellingGeneralAndAdministrativeExpense | |
| `operating_income` | flow | USD | OperatingIncomeLoss | |
| `interest_expense` | flow | USD | InterestExpense; InterestExpenseNonoperating | |
| `pretax_income` | flow | USD | IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest; IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments | |
| `income_tax` | flow | USD | IncomeTaxExpenseBenefit | |
| `net_income` | flow | USD | NetIncomeLoss; ProfitLoss | ProfitLoss includes minority interests: flag `net_income_includes_nci` |
| `eps_basic` | flow | USD/shares | EarningsPerShareBasic | Never derived by subtraction |
| `eps_diluted` | flow | USD/shares | EarningsPerShareDiluted | Never derived by subtraction |
| `shares_diluted_avg` | flow | shares | WeightedAverageNumberOfDilutedSharesOutstanding | Average over the period; never derived by subtraction |
| `depreciation_amortization` | flow | USD | DepreciationDepletionAndAmortization; DepreciationAndAmortization | |
| `operating_cash_flow` | flow | USD | NetCashProvidedByUsedInOperatingActivities | 10-Q values are usually year-to-date |
| `capex` | flow | USD | PaymentsToAcquirePropertyPlantAndEquipment | Year-to-date in 10-Qs |
| `dividends_paid` | flow | USD | PaymentsOfDividends; PaymentsOfDividendsCommonStock | Year-to-date in 10-Qs |
| `buybacks` | flow | USD | PaymentsForRepurchaseOfCommonStock | Year-to-date in 10-Qs |
| `cash` | instant | USD | CashAndCashEquivalentsAtCarryingValue; CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents | Flag when the second concept is used |
| `current_assets` | instant | USD | AssetsCurrent | |
| `total_assets` | instant | USD | Assets | |
| `current_liabilities` | instant | USD | LiabilitiesCurrent | |
| `total_liabilities` | instant | USD | Liabilities | Else computed: LiabilitiesAndStockholdersEquity − StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest |
| `long_term_debt` | instant | USD | LongTermDebtNoncurrent; LongTermDebt | |
| `total_equity` | instant | USD | StockholdersEquity; StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest | |
| `shares_outstanding` | instant | shares | dei:EntityCommonStockSharesOutstanding; CommonStockSharesOutstanding | dei value is as of the cover-page date, not the period end |

**Computed metrics** (method `computed`): `free_cash_flow` = operating_cash_flow − capex; `gross_margin`, `operating_margin`, `net_margin` = metric / revenue; `revenue_growth_yoy`, `eps_diluted_growth_yoy` = vs the same fiscal period one year earlier. With a price (E10, §9.12), either the latest licensed close or a price the client passes: `market_cap` = price × shares_outstanding; `pe_ttm` = price / TTM eps_diluted (null when TTM EPS ≤ 0).

### 9.4 Period logic

1. **Duration** = `period_end − period_start + 1` days. Quarter: 80–100. Six months: 170–195. Nine months: 260–285. Year: 350–380 (covers 52/53-week years). Anything else is ignored with a debug log.
2. **Restatements:** for each (concept, period_start, period_end), use the fact with the latest `filed_at`; ties go to the latest accession number. All facts stay in `xbrl_facts`.
3. **Concept choice:** per metric and company, use the first concept in the priority list that has a fact for the period. **Never mix concepts inside one derivation** (e.g. a nine-month value from one concept and a full-year value from another). On a mismatch, skip the derivation and flag `concept_mismatch`.
4. **Discrete quarters:** use a reported three-month fact when one exists (`method = reported`). Otherwise derive from year-to-date facts: Q2 = 6M − Q1, Q3 = 9M − 6M (`derived_ytd`). This is the normal case for cash-flow metrics in 10-Qs.
5. **Q4:** FY − 9M year-to-date, or FY − (Q1 + Q2 + Q3) when no 9M fact exists (`derived_q4`). Not for per-share or average-share metrics: if Q4 EPS isn't reported, value is null with flag `eps_q4_not_reported`.
6. **Instant metrics:** the value at each quarter end and fiscal year end; no derivation.
7. **Fiscal labels:** `fiscal_year` = the calendar year in which the fiscal year ends (D-008). Quarters are numbered by position after the previous fiscal year end. This can differ from a company's own label (some retailers whose year ends in January call it the previous year). The API always returns `period_start` and `period_end` so nothing is ambiguous. The same rule labels earnings releases (§9.9).
8. **TTM:** flows = sum of the last four discrete quarters, which must be contiguous; any gap gives null with flag `ttm_gap`. Instants = latest quarter-end value.
9. **Units:** only USD, shares and USD/shares are used. A company reporting in another currency is flagged `non_usd_reporter` and its money metrics are skipped in the MVP.
10. **Financial-sector companies** (SIC 6000–6999): revenue concepts differ for banks and insurers. Use `Revenues` if reported; otherwise revenue and gross profit are null with flag `not_applicable_financials`.

### 9.5 Quality checks

Checks write flag codes into `quality_flags` and are returned by the API. **A flag never changes or hides a value.**

| Code | Rule |
| --- | --- |
| `balance_mismatch` | \|Assets − LiabilitiesAndStockholdersEquity\| / Assets > 1% |
| `quarters_dont_sum` | For flows with all four quarters reported: \|Q1+Q2+Q3+Q4 − FY\| / \|FY\| > 0.5% |
| `outlier_jump` | Absolute value changes more than 5× against the previous period of the same type |
| `concept_mismatch` | See §9.4 rule 3 |
| `ttm_gap` | See §9.4 rule 8 |
| `stale_data` | Latest 10-Q or 10-K is older than 120 days |
| `eps_q4_not_reported` | See §9.4 rule 5 |
| `net_income_includes_nci` | ProfitLoss used for net_income |
| `non_usd_reporter`, `not_applicable_financials` | See §9.4 rules 9–10 |
| `release_vs_filing_mismatch` | On `earnings_facts` (E4b): the release value differs from the later 10-Q/10-K XBRL value for the same quarter by more than 0.5% (EPS: more than $0.01); sets `cross_check_status = mismatch` (D-028) |
| `scale_unknown`, `sign_change` | Earnings extraction flags (§9.9) |
| `price_not_licensed` | The price vendor's license does not cover this channel; price fields are null (§9.12) |

### 9.6 8-K item and form labels

| Item or form | Plain-language label | Importance |
| --- | --- | --- |
| 1.01 | Signed a major agreement | normal |
| 1.02 | Ended a major agreement | normal |
| 1.03 | Bankruptcy or receivership | high |
| 1.05 | Material cybersecurity incident | high |
| 2.01 | Completed an acquisition or sale of assets | high |
| 2.02 | Quarterly or annual results | high |
| 2.03 | Took on a major financial obligation | normal |
| 2.05 | Restructuring or exit costs | normal |
| 2.06 | Material impairment | high |
| 3.01 | Delisting notice or listing-rule issue | high |
| 3.02 | Unregistered sale of shares | normal |
| 4.01 | Changed auditor | normal |
| 4.02 | Past financial statements can no longer be relied on | high |
| 5.02 | Director or executive change | normal |
| 5.03 | Changed bylaws or fiscal year | normal |
| 5.07 | Shareholder vote results | normal |
| 7.01 | Regulation FD disclosure | normal |
| 8.01 | Other events | normal |
| 9.01 | Financial statements and exhibits | hidden (attachment-only) |
| Schedule 13D | An investor crossed 5% and may seek to influence the company | high |
| Schedule 13D amendment | An investor seeking influence changed its stake | normal |
| Schedule 13G | A passive investor holds more than 5% | normal |
| Schedule 13G amendment | A passive 5% holder changed its stake | hidden (too frequent) |

### 9.7 Insider trades (Form 4)

- Parse both tables of the XML: non-derivative and derivative. Store both; the default view serves non-derivative lines only.
- Transaction codes: P = open-market or private purchase; S = open-market or private sale; A = grant or award; M = exercise or conversion of a derivative; F = shares withheld to pay exercise price or tax; G = gift; D = disposition to the issuer. Store other codes as-is.
- **Signal view** = non-derivative, code P or S, not superseded.
- **10b5-1 plans:** since 2023 Form 4 has a checkbox for trades under a pre-planned 10b5-1 plan. Store it in `is_10b5_1` (null for earlier filings). The SEC's insider dataset added this field (`AFF10B5ONE`) for 2023 onward; confirm the XML element name in E8.
- **Amendments:** a 4/A supersedes the lines of the earlier filing with the same issuer, same reporting owner(s) and same `period_of_report`. Set `superseded_by`. If no match is found, keep both and flag `amendment_unmatched` in logs.
- Multiple reporting owners share one filing's lines; owners live in `insider_owners`.
- Display aggregation: per owner, per trade date, per code — total shares, share-weighted average price, total value (shares × price). Keep footnotes; prices are often weighted averages explained there.

### 9.8 13F fund portfolios

- `data/managers.csv` (columns `display_name,manager_cik,notes`) lists 20–30 well-known managers. **CIKs are looked up on EDGAR, never guessed.**
- Parse the information table: nameOfIssuer, titleOfClass, cusip, value, sshPrnamt, sshPrnamtType, putCall, investmentDiscretion.
- **Value units:** recent filings report whole dollars; older filings reported thousands. Normalize to dollars using the filing-date cutoff from the SEC's 13F guidance (believed to be early January 2023; **verify in E9**).
- **Unit sanity check without prices (D-023):** implied price (value / shares) must be within ±50% of the median implied price for the same CUSIP across all curated managers in that period, else flag `unit_suspect`.
- **Amendments:** a 13F-HR/A marked RESTATEMENT replaces that period's holdings; one marked NEW HOLDINGS adds lines.
- Merge rows by (cusip, put_call) for each manager and period (filers split one position across rows).
- PUT and CALL rows are shown separately and excluded from portfolio weights.
- `change_type`: `new` (absent last period), `added` (shares up more than 1%), `trimmed` (down more than 1%), `unchanged`, `exited` (present last period, absent now; stored with zero shares).
- `weight_pct` = position value / total value of long equity positions in that period.
- CUSIP → security via OpenFIGI (ID_CUSIP, US listing), cached in `cusip_map`. Unmatched rows keep `name_of_issuer` with `security_id` null.
- Always show both `report_period` (quarter end) and the filing date: 13F data can be up to 45 days old when filed.

### 9.9 Earnings release extraction (rule-based, E7a)

- **Trigger:** an 8-K with item 2.02 from a `domestic` universe company.
- **Source document:** the press release exhibit found through the filing index (EX-99.1, else EX-99, EX-99.2 …). Use the exhibit that contains the income statement table. Raw HTML stays in the raw store; text with tables as markdown goes to `documents`.
- **Slots (M1):** `revenue`, `operating_income`, `net_income`, `eps_diluted` for the current quarter, each with the prior-year quarter from the same table.
- **Table parsing:** HTML tables become a grid (colspan and rowspan expanded; `$` and `%` cells merged with their numbers; parentheses = negative; dashes = nil; footnote markers stripped from labels).
- **Table detection:** the income statement table is the first table whose row labels match a revenue pattern and a net income or EPS pattern, and whose header names a quarter period.
- **Label patterns** live in `extract/earnings_rules.py`, versioned with `RULES_VERSION`. Examples:
  - revenue: "Total revenues", "Revenues", "Net sales", "Total net sales", "Net revenues"
  - operating income: "Operating income", "Income from operations", "Operating income (loss)"
  - net income: "Net income", "Net income attributable to <company>" (prefer the amount attributable to the company over the amount including noncontrolling interests)
  - diluted EPS: "Diluted", "Diluted earnings per share", "Net income per share — diluted"
  - banks and insurers: the revenue slot is `not_applicable` (as §9.4 rule 10)
- **Columns:** take the "three months / quarter / thirteen weeks ended" columns. The current period is the latest date; the prior-year period is the column one year earlier. Never take nine-month, year-to-date or full-year columns for quarterly slots. In Q4 releases with both quarter and full-year columns, take the quarter columns.
- **Units:** read the scale from the table caption or header ("in millions", "in thousands", "except per share"). Per-share values are not scaled. No scale found → the slot fails with flag `scale_unknown`.
- **Overrides:** `data/extraction_overrides.yaml` may pin a row label or table index per CIK when the rules fail. Every override is logged in `docs/EDGE_CASES.md` and counted in the hit-rate report.
- **Change:** `change_pct = (value − prior_value) / |prior_value| × 100`, rounded to one decimal. Null when the prior value is missing or zero, or when the sign changes (flag `sign_change`; `direction` becomes `turned_profit` or `turned_loss`).
- **Verification (blocking, in `verify/`, independent of the extractor; D-015):**
  1. The printed cell text for each value appears in the stored document text, and the source quote is a substring of it after whitespace normalization.
  2. The stored value equals the parsed cell multiplied by the scale, exactly.
  3. Sanity: \|change_pct\| ≤ 1000; revenue > 0; when the table shows diluted shares, EPS × diluted shares is within 2% of net income.
  4. A slot that fails any check is stored with `check_status = failed` and served as null with its reason. Other slots are still served (`status = partial`). All four passed → `complete`.
- **Hit-rate report:** `marketdata extract report` runs over the four-quarter backfill (about 100 releases for 20–30 tickers) and prints, per slot and per company, passed / failed / not found and how many needed overrides. The result decides OD-10 (whether to build E12).
- **Golden set:** at least 30 releases hand-checked in `tests/golden/earnings/*.yaml`, each with its SEC URL.
- **Provenance:** every stored value records `method` and `extractor_version`. Press releases stay in the raw store so a later extractor can re-run over the whole history.
- **Amendments and re-runs (D-027):** an 8-K/A that amends an earnings release is processed like the original; changed values are logged with reason `amendment`. Re-running with a new `RULES_VERSION` or an added override logs changed values with reason `correction`.
- **Cross-check (D-028):** each slot starts as `pending` and becomes `matched`, `mismatch` or `not_comparable` when the 10-Q/10-K for the same quarter is loaded (E4b, §9.10).

### 9.10 Proof, cross-checks, accuracy and changes (E4b, E5a, E5b)

- **Proof object.** Every served value carries `proof`:
  - earnings facts: `accession_no`, `document_url`, `source_quote`, `scale_text`, `checks` (the checks passed), `method`, `extractor_version`, and `cross_check` (`status`, `filing_accession_no`, `filing_value`);
  - financial metrics: `accession_no`, `filing_url`, `concept`, `method` (reported, derived_ytd, derived_q4, derived_ttm, computed), `source_accessions`, `calc_version`.
- **Cross-check (D-028).** When a 10-Q or 10-K for the same quarter is loaded, each earnings slot is compared with the XBRL value for the matching metric and period (§9.3, §9.4): equal within 0.5% (EPS: within $0.01) → `matched`; otherwise `mismatch` with flag `release_vs_filing_mismatch`; no comparable value → `not_comparable`. A mismatch never changes the release value; it is shown, investigated and logged in `docs/EDGE_CASES.md`.
- **Accuracy report.** `marketdata quality accuracy` and `GET /v1/status` publish, from live data: golden-set match rate per metric and per slot; cross-check match rate (matched / (matched + mismatch)); extraction pass rate; median and 90th-percentile minutes from SEC acceptance to verified; value changes by reason over the last 90 days.
- **Changes (D-027).** Any change to a stored `earnings_facts` or `financial_metrics` value writes a `value_changes` row with reason `correction` (our rules or overrides changed), `restatement` (a newer filing changed a past period) or `amendment` (the company amended the filing we used). Changes are served by `GET /v1/changes` and announced by `data.changed`.
- **Proof pages.** `/proof/earnings/{accession_no}` and `/proof/metrics/{ticker}/{metric}/{period_type}/{period_end}` show the value, its proof, the SEC links, the check results, the cross-check and the change history. In M1 they sit behind HTTP Basic auth with an `api`-scope key over TLS. From E15 they are part of the web UI and follow its access rule (§9.14); every number in the UI links to one.

### 9.11 Webhooks (E5b, D-019)

- **Envelope:** `{"id", "type", "created", "livemode", "api_version", "data": {"object": …}}`.
- **Signing:** header `X-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256 of "<t>.<raw body>">`, keyed with the destination's secret. Receivers compare in constant time and reject timestamps more than 5 minutes old.
- **Delivery:** through the task queue (`webhook_deliver`). Success is any 2xx within 10 seconds. Retries after about 1 minute and about 10 minutes (3 attempts in total), then the delivery is `dead`. 50 consecutive failures disable the destination and alert the operator. HTTPS only; private, loopback and link-local addresses are refused.
- **Idempotency:** receivers dedupe on `id`; ordering is not guaranteed.
- **Events (M1):**
  - `earnings.verified`: a new release's facts are verified (complete or partial); the payload is the same release object as the API.
  - `financials.updated`: new or changed metric rows for a company after a 10-Q or 10-K.
  - `data.changed`: a previously served value changed; the payload has the old value, new value, reason and proof.
  - `filing.event`: a high-importance label from §9.6.
- Payloads never contain end-user data. E7b adds `content.*` events (§9.13).
- **Test events:** `livemode: false`, fired from the CLI, at most one per minute per destination.

### 9.12 Prices (E10, D-031)

- End-of-day prices (or delayed prices, if the vendor's terms make that cheaper or simpler) from one licensed vendor chosen in OD-4. No real-time or intraday data.
- The vendor's terms are recorded in §15 before any price is loaded: where prices may be shown (`display_scopes`: `ui`, `mcp`, `api`), the attribution text, the delay and the cost model. The service layer checks the scope before returning a price; a channel outside the license gets null price fields with flag `price_not_licensed`.
- Prices are loaded daily after the US close (22:30 UTC) for every universe security, with two years of history. Every displayed price shows its date, source and any required attribution.
- Price-based metrics (`market_cap`, `pe_ttm`, §9.3) are computed on read from the latest close and never stored as facts.
- **Client-supplied price (kept from D-016):** API clients that license prices themselves can pass `price` and `price_date`. The response then uses that price, with `price_source: "client"`. A client-supplied price is never stored.

### 9.13 Georgian localization and content controls (E7b, on demand)

Built on demand, when a client or our own UI needs Georgian (D-026). D-013 (template mode) applies to client templates.

- **Localized fields** added to the earnings objects: company name forms, fiscal period labels, `display_ka` and `display_en` strings for each value, and `direction` (`up`, `down`, `flat`, `turned_profit`, `turned_loss`).
- **Noun forms.** Georgian declines nouns, so a template cannot simply append a suffix ("კვარტალი" becomes "კვარტლის"). Company names (`data/company_names_ka.csv`) and period labels are supplied in the cases templates need (nominative, ergative, genitive, dative). Brand names may stay in Latin script if the client prefers.
- **Number formatting:** per OD-6, produced by code from stored values only.
- **Glossary:** `data/glossary_ka.csv` (`term_en,term_ka,notes`), drafted from Georgian financial terms in common public use (for example on Georgian banks' and brokers' sites) and reviewed by a Georgian financial editor.
- **Templates:** client templates are Jinja2 in a sandboxed environment. They may reference localized fields and branch on `direction` or on a value being present; no arithmetic, no filters that change numbers. A missing value renders nothing plus the name of the missing field, never a partial sentence.
- **Content controls:** per-client manual or automatic approval, pause by content kind or ticker, and retraction (`client_settings`, `content_reviews`), with review pages behind a `review`-scope key. Controls apply to rendered content only; the data API is unaffected.
- **Events:** `content.pending_review`, `content.approved`, `content.retracted`.

### 9.14 Web UI (E15, D-034)

- **What it is:** a small, fast, read-only website over the same service layer as the API and MCP. English first; Georgian through E7b if wanted.
- **Pages:**
  - search by ticker or company name;
  - company page: identity, filer class and what data applies; latest earnings release with year-over-year changes; 8-quarter history of the core metrics with a chart; latest close with date and source (where licensed); recent filings with plain-language event labels; insider and fund sections once E8 and E9 land;
  - compare page: 2–5 companies side by side;
  - proof pages (§9.10), linked from every number;
  - status page: freshness, track record and the accuracy report (§9.10).
- **Rules:**
  - every number links to its proof page; the disclaimer is on every page;
  - no accounts, watchlists or analytics trackers; no third-party scripts or requests at runtime (static assets are vendored); server logs only, with IP addresses truncated;
  - no rankings, "top movers" or verdict words such as "strong" or "weak" (D-029); changes are shown as numbers, and conventional up and down colours are allowed;
  - funds and foreign issuers get a page that says what is and isn't covered, not an error;
  - pages render server-side from `service/`; no SQL in `web/`.
- **Access (OD-13):** `UI_MODE=private` (the default) requires HTTP Basic auth with an `api`-scope key, for invited testers. `UI_MODE=public` is switched on only when the price license allows public display (or prices are hidden); per-IP rate limits then apply.

---

## 10. API and MCP contracts

### 10.1 REST conventions

- Base path `/v1`. Auth header: `X-API-Key` with an `api`-scope key. Proof pages use HTTP Basic auth with an `api`-scope key as the password until E15; then they follow the UI access rule (§9.14). (`review` scope: E7b.)
- Every successful response uses this envelope:

```json
{
  "data": {},
  "sources": [
    {"accession_no": "<accession>", "form": "10-Q", "filed_at": "<YYYY-MM-DD>",
     "url": "https://www.sec.gov/Archives/edgar/data/<cik>/<accession_nodash>/"}
  ],
  "as_of": "<ISO 8601 UTC timestamp of the newest data used>",
  "quality_flags": [{"code": "<flag>", "metric": "<metric or slot>", "period_end": "<YYYY-MM-DD>"}],
  "disclaimer": "Information from public filings. Not investment advice."
}
```

- Every value inside `data` carries a `proof` object (§9.10).
- Errors: `{"error": {"code": "<code>", "message": "<human-readable>"}}` with codes `invalid_parameter` (400), `unauthorized` (401), `forbidden` (403, wrong key scope), `not_found` (404), `not_applicable` (422), `rate_limited` (429), `internal` (500). Messages say how to fix the request where possible.
- Lists: `limit` (default 20, max 100) and opaque `cursor`.
- Numbers are JSON numbers serialized from `Decimal`; units are always stated next to them.
- Ticker parameters follow §9.2 normalization.

### 10.2 Endpoints

| Endpoint | Epic | Auth | Returns |
| --- | --- | --- | --- |
| `GET /health` | E0 | none | Process and database status |
| `GET /v1/status` | E7c (basic), E5b (accuracy), E11 (full) | none | Freshness, track record, accuracy report, queue depth |
| `GET /v1/coverage` | E5a | api | Covered tickers with class and available data kinds |
| `GET /v1/companies/{ticker}?price=&price_date=` | E5a (E10 adds prices) | api | Snapshot: identity, class, latest period, TTM core metrics, latest release, latest three filings; from E10, latest close, market cap and P/E where licensed, or computed from a `price` the client passes |
| `GET /v1/companies/{ticker}/earnings/latest` | E5a | api | Latest verified release with proof and cross-check status |
| `GET /v1/earnings?since=&ticker=&limit=&cursor=` | E5a | api | Verified releases, newest first |
| `GET /v1/earnings/{accession_no}` | E5a | api | One release |
| `GET /v1/companies/{ticker}/financials?metrics=&period=&limit=` | E5a | api | Metric rows with proof; `period` is Q, FY or TTM; default metrics: the core set (§9.3) |
| `GET /v1/companies/{ticker}/filings?form=&since=` | E5a | api | Filings with plain-language events, including 13D/13G labels |
| `GET /v1/compare?tickers=&metrics=&period=&price=` | E5a | api | 2–5 companies side by side |
| `GET /v1/companies/{ticker}/prices?start=&end=` | E10 | api | End-of-day prices, only if the vendor license includes `api` (§9.12) |
| `GET /v1/changes?since=&ticker=&reason=` | E5b | api | Value changes with old value, new value, reason and proof |
| `GET /proof/earnings/{accession_no}` | E5b | Basic auth (M1); UI rule (E15) | HTML proof page for a release |
| `GET /proof/metrics/{ticker}/{metric}/{period_type}/{period_end}` | E5b | Basic auth (M1); UI rule (E15) | HTML proof page for a metric value |
| `GET /v1/companies/{ticker}/insiders?days=90&signal_only=true` | E8 | api | Aggregated insider trades |
| `GET /v1/funds?curated=true` | E9 | api | Curated managers |
| `GET /v1/funds/{manager_cik}/holdings?quarter=` | E9 | api | Positions with weights and changes |
| `GET /v1/companies/{ticker}/funds` | E9 | api | Curated managers holding this company, latest period |

E7b (on demand) adds localized fields to the earnings objects, `render=` for client templates, review endpoints, client settings and `/review` pages. Web UI pages (E15) are listed in §9.14; they are HTML pages, not part of the API contract.

Example `earnings/latest` response body (`data` part):

```json
{
  "ticker": "<TICKER>",
  "company": "<Company Inc.>",
  "period": {"fiscal_year": 2026, "fiscal_period": "Q3", "period_end": "<YYYY-MM-DD>",
             "label": "<company's own wording>"},
  "release": {"accession_no": "<accession>", "accepted_at": "<ISO 8601 UTC>",
              "verified_at": "<ISO 8601 UTC>", "status": "complete"},
  "facts": [
    {"slot": "revenue", "value": 0, "unit": "USD", "prior_value": 0, "change_pct": 0.0,
     "proof": {"accession_no": "<accession>", "document_url": "<SEC document URL>",
               "source_quote": "<row label and cells as printed>",
               "scale_text": "<e.g. in millions, except per share amounts>",
               "checks": ["in_source", "scale", "sanity"], "method": "rule",
               "extractor_version": "<rules version>",
               "cross_check": {"status": "pending", "filing_accession_no": null, "filing_value": null}},
     "quality_flags": []}
  ]
}
```

### 10.3 MCP server (E6)

- Official Python SDK, Streamable HTTP at `/mcp`, inside the same FastAPI app. Auth: the same `X-API-Key` header (`api` scope).
- For desktop clients that can't send headers to a remote server, test with a local bridge such as `mcp-remote` with a header option (verify in E6). OAuth and public directory listings are post-MVP (§3).
- Each tool call is logged in `api_requests` (method `MCP`, path = tool name), so usage is measured the same way as the API.
- Tools are task-shaped, return compact JSON (target under 8 KB per call), cap lists at 20, and include source URLs. Each description says when to use the tool, what units it returns, and that the data is information, not investment advice.

| Tool | Epic | Inputs | Returns |
| --- | --- | --- | --- |
| `lookup_company` | E6 | `query` (name or ticker) | Up to 10 matches: ticker, name, cik, class |
| `company_snapshot` | E6 | `ticker` | Same content as the REST snapshot (price fields from E10 if the license includes `mcp`) |
| `get_financials` | E6 | `ticker`, `metrics` (optional), `period` (Q, FY or TTM), `limit` (max 12) | Metric rows + sources |
| `recent_filings` | E6 | `ticker`, `form` (optional), `days` (max 365) | Up to 20 filings with event labels |
| `compare_companies` | E6 | `tickers` (2–5), `metrics`, `period` | Comparison table + sources |
| `latest_earnings` | E6 | `ticker` | Verified release facts with proof; no free text |
| `insider_activity` | E8 | `ticker`, `days` (max 365), `signal_only` (default true) | Aggregated trades + sources |
| `investor_portfolio` | E9 | `manager` (name or CIK), `quarter` (optional) | Top 20 positions with changes + sources |

---

## 11. Configuration and environments

**Environment variables** (all listed in `.env.example`)

| Variable | Example | Used by |
| --- | --- | --- |
| `ENV` | `dev`, `live` or `prod` | all |
| `LOG_LEVEL` | `INFO` | all |
| `DATABASE_URL` | `postgresql://marketdata:marketdata@localhost:5432/marketdata` | all. Required. The Docker Postgres is on host port 5433 (D-037) |
| `TEST_DATABASE_URL` | `postgresql://marketdata:marketdata@localhost:5432/marketdata_test` | tests only; name must end in `_test` |
| `RAW_STORE_KIND` | `local` or `s3` | E1 |
| `RAW_STORE_PATH` | `./var/raw` | E1 (local) |
| `S3_ENDPOINT_URL`, `S3_BUCKET`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY` | — | E1 (s3) |
| `SEC_USER_AGENT` | `marketdata <your email>` | E1 |
| `SEC_MAX_RPS` | `8` | E1 |
| `UNIVERSE_FILE` | `data/universe.csv` | E2 |
| `EXTRACTION_OVERRIDES_FILE` | `data/extraction_overrides.yaml` | E7a |
| `ALERT_EMAIL`, `ALERT_TELEGRAM_BOT_TOKEN`, `ALERT_TELEGRAM_CHAT_ID` | — | E7c |
| `SENTRY_DSN` | — | E7c |
| `WEBHOOK_ENCRYPTION_KEY` | — (Fernet key) | E5b |
| `PUBLIC_BASE_URL` | `https://<live host>` | E5b (proof links in webhooks) |
| `PRICE_PROVIDER`, `PRICE_API_KEY` | — (OD-4) | E10 |
| `UI_MODE` | `private` or `public` | E15 |
| `MANAGERS_FILE` | `data/managers.csv` | E9 |
| `OPENFIGI_API_KEY` | — | E9 |
| `COMPANY_NAMES_KA_FILE`, `GLOSSARY_KA_FILE` | `data/company_names_ka.csv`, `data/glossary_ka.csv` | E7b (on demand) |
| `LLM_PROVIDER`, `ANTHROPIC_API_KEY`, `LLM_MODEL` | — (OD-2) | E12, E13 only |

**Environments**

- **dev:** PostgreSQL from `docker compose up -d postgres` (18, host port 5433) or a local PostgreSQL 18 (D-037, D-040); API, worker and scheduler run with `uv run`; raw store is the local folder `./var/raw`.
- **live (from E7c):** one small always-on VM running all services in Docker Compose behind Caddy, so releases that land around midnight Tbilisi time are processed and the track record starts during the Q3 2026 season (OD-1). The web UI runs there in `private` mode until OD-13 is decided.
- **prod (E11):** hardened version of the live setup; raw store in R2 or B2.

**Common commands**

```bash
docker compose up -d postgres
uv sync
uv run alembic upgrade head
uv run pytest
uv run ruff check . && uv run ruff format --check . && uv run mypy marketdata
uv run uvicorn marketdata.api.app:app --reload
uv run marketdata worker
uv run marketdata scheduler
uv run marketdata extract report
uv run marketdata quality accuracy
```

---

## 12. Epics and user stories

Each epic lists its goal, dependencies, stories with acceptance criteria (AC), what is out of scope, and a demo that proves the session is done. Story IDs (`E3-S2`) are used in commits and the decision log. Epics appear in build order (D-032).

### E0 — Foundation (session 1)

**Goal:** an empty repo becomes a running skeleton with database, migrations, config, CLI, health endpoint and CI.
**Depends on:** OD-7 (name) — use `marketdata` until decided.

- **E0-S1 Dev environment.** As the operator, I want one-command setup so every session starts from a working environment.
  - AC: `docker compose up -d postgres` starts PostgreSQL 18 (D-040; was 16) with a named volume.
  - AC: `uv sync` installs everything; Python 3.12 pinned in `pyproject.toml`.
  - AC: `.env.example` lists every variable in §11; `README.md` has a quickstart of at most 6 steps.
- **E0-S2 Skeleton and config.** As a developer, I want the §6 layout with typed settings and logging so later epics only add code.
  - AC: package layout matches §6 (empty modules allowed).
  - AC: `uv run marketdata --help` lists command groups.
  - AC: settings load from env via pydantic-settings; missing required values fail fast with a clear message.
  - AC: structlog prints JSON when `ENV` is `live` or `prod`, readable output when `ENV=dev`.
- **E0-S3 Database and migrations.** As a developer, I want versioned migrations so schema changes are repeatable.
  - AC: Alembic configured; migration `0001` creates `tasks`, `clients`, `api_keys`, `api_requests` (§7).
  - AC: `alembic upgrade head` and `alembic downgrade -1` both work.
  - AC: connection pool in `db/pool.py`; a repository test inserts and reads a task.
- **E0-S4 Health endpoint.** As an API integrator, I want a health check so monitoring can tell if the service is up.
  - AC: `GET /health` returns `{"status":"ok","db":"ok"}` with 200; returns 503 with `"db":"down"` when Postgres is unreachable (tested).
- **E0-S5 CI.** As the operator, I want every push checked automatically so broken code never reaches main.
  - AC: GitHub Actions workflow runs ruff (check + format), mypy `--strict`, pytest with a Postgres service; green on main.
- **E0-S6 AI context files and edge-case log.** As the operator, I want the plan and the edge-case log in the repo so every session reads and adds to the same record.
  - AC: `docs/PLAN.md` (this file) and `CLAUDE.md` (Appendix A) committed.
  - AC: `docs/EDGE_CASES.md` committed with its format: date, epic, company and accession, what broke, how it was handled.

**Out of scope:** any SEC access.
**Demo:** `docker compose up -d postgres && uv run alembic upgrade head && uv run uvicorn marketdata.api.app:app` then `curl localhost:8000/health`.

### E1 — SEC client and raw store (session 2)

**Goal:** one safe way to call the SEC, and every downloaded file kept forever.
**Depends on:** E0.

- **E1-S1 SEC client.** As the operator, I want every SEC call to go through one client so we never break the SEC's fair-access rules.
  - AC: async httpx client in `sec/client.py`; `SEC_USER_AGENT` required (startup fails without it).
  - AC: process-wide async token bucket limited to `SEC_MAX_RPS`; a test shows 40 requests take at least `(40 / SEC_MAX_RPS) − 1` seconds.
  - AC: retries on 429 and 5xx with exponential backoff and jitter, max 5 attempts; tested with a mocked transport.
  - AC: connect and read timeouts set; non-retryable errors raise a typed exception.
- **E1-S2 Raw store.** As the operator, I want original files stored unchanged so parsing bugs can be fixed by re-reading, not re-downloading.
  - AC: `RawStore` interface with `put(key, data, content_type)`, `get(key)`, `exists(key)`.
  - AC: `LocalRawStore` and `S3RawStore` implementations; selected by `RAW_STORE_KIND`.
  - AC: write-once: putting the same key with identical bytes is a no-op; different bytes raise an error.
  - AC: `S3RawStore` tested against a local S3-compatible server (MinIO in Docker) or moto.
- **E1-S3 Fetch and store.** As a developer, I want one helper that downloads and saves in one step so no job forgets to keep the original.
  - AC: `fetch_and_store(url, key) -> bytes` saves to the raw store before returning and upserts a `raw_objects` row (migration `0002`) with url, sha256, size, content type, fetch time.
  - AC: key layout: `raw/sec/{cik}/{accession_no}/{file}` for filing documents; `raw/sec/api/{endpoint}/{cik}/{fetched_at}.json` for API responses.
- **E1-S4 URL builders.** As a developer, I want all SEC URL patterns in one module so they are tested once.
  - AC: `sec/urls.py` covers every row of the §8 endpoint table; unit tests check CIK padding and accession-number formatting.
- **E1-S5 Fixtures.** As a developer, I want real SEC files available offline so tests never depend on the network.
  - AC: `scripts/fetch_fixture.py <url> <name>` saves a response under `tests/fixtures/sec/`.
  - AC: committed fixtures: submissions and companyfacts for 3 companies chosen for variety (one September year-end, one December year-end, one January year-end retailer). Trim large files if needed to keep the fixture folder small.

**Out of scope:** parsing.
**Demo:** a CLI command `uv run marketdata sec get <url>` that fetches through the client, stores the file, and prints the raw key.

### E2 — Security master and universe (session 3)

**Goal:** we know which companies we cover, what kind of filer each is, and can resolve any ticker.
**Depends on:** E1; OD-3 (the starter list of about 30 domestic filers plus a few ETFs and foreign issuers, §9.1).

- **E2-S1 Universe file.** As the operator, I want to control coverage with a simple file so changing it needs no code.
  - AC: `data/universe.csv` with `ticker,note`; the loader validates each ticker against the SEC ticker lists, prints unknown ones, and prints a count per class (§9.1).
- **E2-S2 Companies and securities.** As a developer, I want company and ticker records loaded from the SEC so everything else can join on CIK.
  - AC: migration creates `companies` and `securities` (§7) with `filer_type` and `security_type`.
  - AC: `uv run marketdata ingest security-master` loads tickers from `company_tickers_exchange.json` and `company_tickers_mf.json`, and details from each universe company's submissions JSON; sets `in_universe` and classifies per §9.1.
  - AC: re-running changes nothing (idempotent); a test proves it.
- **E2-S3 Ticker resolution.** As an API integrator, I want `brk.b`, `BRK-B` and `BRK/B` to mean the same thing so integration is forgiving.
  - AC: `service.companies.resolve_ticker(text)` returns the current security, its class and CIK per §9.2; raises `NotFound` otherwise.
  - AC: unit tests cover case, separators, two share classes of one company, a closed (historic) ticker, an ETF and a foreign issuer.
- **E2-S4 Ticker changes.** As the operator, I want ticker changes handled automatically so old tickers don't break silently.
  - AC: the daily run closes rows for tickers that disappeared or moved CIK and opens new ones; tested with two snapshot fixtures.
- **E2-S5 Capabilities.** As an API integrator, I want a clear answer when a ticker has no fundamentals so my app can say why.
  - AC: `service.companies.capabilities(cik)` returns which data kinds apply (earnings, fundamentals) with a reason; tested for a domestic company, an ETF and a foreign issuer.

**Out of scope:** CUSIP/FIGI (E9), ETF holdings (E14).
**Demo:** `uv run marketdata company show <TICKER>` prints CIK, name, class, SIC, fiscal year end, tickers and capabilities.

### E3 — Task queue and filings watcher (session 4)

**Goal:** new filings are detected within 10 minutes and turned into queued work.
**Depends on:** E2.

- **E3-S1 Postgres task queue.** As a developer, I want a simple, reliable queue in the database we already have so we don't run extra infrastructure.
  - AC: `enqueue(kind, key, payload)` deduplicates on `(kind, key)`; re-enqueueing a `done` task requires an explicit `force=True`.
  - AC: `claim()` uses `FOR UPDATE SKIP LOCKED`, sets `locked_until`; tasks whose lock expired are reclaimable.
  - AC: `fail()` reschedules with backoff; after 5 attempts status becomes `dead`. Task kinds may set their own attempt limit and backoff (webhook delivery uses 3, §9.11).
  - AC: a concurrency test with two claimers shows no task is processed twice.
  - AC: CLI `marketdata tasks list|retry|purge-done`.
- **E3-S2 Worker and registry.** As a developer, I want handlers registered by task kind so new epics only add a handler.
  - AC: `tasks/registry.py` maps kind → async handler; `uv run marketdata worker` runs handlers with configurable concurrency; graceful shutdown on SIGTERM.
  - AC: handler exceptions are logged with task context and recorded in `last_error`.
- **E3-S3 Scheduler.** As the operator, I want periodic jobs to enqueue work, not do it, so all work goes through one path.
  - AC: `uv run marketdata scheduler` enqueues `filings_watch` every 10 minutes and `security_master` daily at 06:00 UTC.
- **E3-S4 Filings watcher.** As a user, I want new company events to show up quickly so the information is current.
  - AC: migration creates `filings` (§7).
  - AC: for each universe CIK (and, from E9, each curated manager CIK) the watcher reads submissions JSON, inserts unseen accessions with `items_8k` parsed, and enqueues routed tasks per §4 (only for registered kinds).
  - AC: first run on a fixture inserts that company's recent filings; a second run inserts 0; a fixture with one new filing enqueues exactly the expected task.
- **E3-S5 Filing history backfill.** As the operator, I want five years of filing history so the feed isn't empty on day one.
  - AC: `uv run marketdata backfill filings --years 5` follows `filings.files` pages.
- **E3-S6 Event labels.** As a user, I want 8-K filings and 5% stakes described in plain words so I understand what happened.
  - AC: §9.6 table in code; `service.filings` returns `events: [{item_or_form, label, importance}]` per filing; hidden items are not returned.
  - AC: the actual Schedule 13D/13G form codes and whether issuer submissions list them are verified and recorded in §15; a fixture with a 13D filing yields the activist label.

**Out of scope:** handlers for earnings, facts, Form 4, 13F (later epics).
**Demo:** run scheduler and worker for 15 minutes; `uv run marketdata filings list <TICKER> --limit 10` shows filings with event labels.

### E7a — Earnings release extraction and verification, rule-based (session 5)

**Goal:** verified current-quarter revenue, operating income, net income and diluted EPS from every earnings press release in the universe, with no LLM (D-014).
**Depends on:** E3.

- **E7a-S1 Find and store the press release.** As a developer, I want the earnings exhibit located reliably so extraction starts from the right document.
  - AC: `earnings_extract` handler finds the press release exhibit via the filing index (§8), stores the raw HTML, and writes a `documents` row with text (tables as markdown) (migration).
  - AC: works on fixtures of the last 4 earnings 8-Ks of 5 companies, including one that uses EX-99 and one with a split income statement; failures are logged with the accession number.
- **E7a-S2 Extractor interface and table parser.** As a developer, I want extraction behind an interface so a later LLM extractor plugs into the same verification (D-015).
  - AC: `Extractor` protocol in `extract/interface.py`: `extract(document) -> ExtractionResult` (slots with value, prior value, unit, source row label, source quote, scale text, method, extractor version).
  - AC: `extract/html_tables.py` builds grids handling colspan/rowspan, `$` and `%` cells, parentheses, dashes and footnote markers; unit tests per case.
- **E7a-S3 Rule extractor.** As the operator, I want the four slots extracted by deterministic rules so results are explainable and cheap.
  - AC: `RuleExtractor` implements §9.9 table detection, label patterns, column choice, units and overrides, with `RULES_VERSION`.
  - AC: fixtures cover a September year-end, a January year-end retailer, a Q4 release with full-year columns, a release in thousands, and a bank (revenue `not_applicable`).
- **E7a-S4 Verification and storage.** As the operator, I want every value checked against the document so we never serve a wrong number.
  - AC: §9.9 verification in `verify/`, which does not import `extract/`; migration creates `earnings_releases` and `earnings_facts`; `status` is `complete`, `partial` or `failed`.
  - AC: crafted cases (wrong scale, value not in the source, sign change, missing prior year, nine-month column picked by mistake) each produce the expected result.
  - AC: migration also creates `value_changes`; a re-run with a changed rule or a new override writes `correction` rows, and an 8-K/A writes `amendment` rows (tested, D-027).
- **E7a-S5 Backfill and hit-rate report.** As the operator, I want to measure the rules on real releases so we know whether an LLM fallback is needed.
  - AC: `uv run marketdata backfill earnings --quarters 4` processes the universe.
  - AC: `uv run marketdata extract report` prints passed / failed / not found per slot and per company, and override counts.
  - AC: at least 30 releases hand-checked in `tests/golden/earnings/`, all passing or each difference listed in §16.
  - AC: the report's numbers and the OD-10 decision are recorded in §15.

**Out of scope:** serving (E5a), localization (E7b), LLM (E12), guidance, non-GAAP measures, foreign issuers' 6-K releases.
**Demo:** `uv run marketdata earnings show <TICKER>` prints the latest release's slots with source quotes and check results.

### E7c — Live run and track record (session 6)

**Goal:** the pipeline runs around the clock from the Q3 2026 earnings season, and every release it processes counts toward a measured track record.
**Depends on:** E7a; OD-1 (a small always-on VM).

- **E7c-S1 Always-on deployment.** As the operator, I want the watcher running around the clock so releases that land at midnight Tbilisi time are processed.
  - AC: Docker Compose on the live VM behind Caddy (HTTPS); nightly `pg_dump` to object storage; external uptime check on `/health`; Sentry enabled.
- **E7c-S2 Track-record stats.** As the operator, I want speed and accuracy measured on live data, not only in tests.
  - AC: `uv run marketdata stats` and a basic `GET /v1/status` report: releases processed, slot pass rate, share of complete releases, median and 90th-percentile minutes from SEC acceptance to verified, overrides used, value changes by reason.
  - AC: stats are kept per week so the record shows a trend.
- **E7c-S3 Alerts.** As the operator, I want to know when a release fails before anyone asks.
  - AC: alerts (email or Telegram) when a universe release fails extraction or verification, or verification takes longer than 60 minutes.

**Out of scope:** the data API (E5a), webhooks and proof pages (E5b).
**Demo:** `uv run marketdata stats` on the live VM after a week of earnings releases.

### E4a — XBRL facts and reported metrics (session 7)

**Goal:** reported quarterly, annual and balance-sheet values for the metric catalog, with provenance.
**Depends on:** E3.

- **E4a-S1 Facts loader.** As a developer, I want every company's XBRL facts in Postgres so metrics are computed with SQL and Python, not by re-reading JSON.
  - AC: migration creates `xbrl_facts` (§7).
  - AC: `facts_refresh` handler loads `us-gaap` and `dei` facts from companyfacts JSON (raw file stored first); idempotent.
  - AC: `uv run marketdata backfill facts` loads all universe companies (per-company API, or the nightly bulk ZIP if faster).
  - AC: fact counts for a fixture company match a count computed directly from the fixture JSON.
- **E4a-S2 Metric catalog.** As a developer, I want the metric definitions in one versioned module so rules are reviewable in git.
  - AC: `normalize/metric_catalog.py` implements §9.3 with `CATALOG_VERSION` and marks the core set; a test asserts every metric has at least one concept, a kind and a unit.
- **E4a-S3 Period logic.** As a developer, I want period classification and fiscal labels in one tested module so every metric uses the same rules.
  - AC: `normalize/periods.py` implements §9.4 rules 1, 2 and 7.
  - AC: unit tests cover 52/53-week years, a January year-end, a September year-end and a December year-end.
- **E4a-S4 Reported metrics.** As a user, I want the company's own reported numbers so I can trust what I see.
  - AC: migration creates `financial_metrics` (§7).
  - AC: rows with `method = reported` for Q (three-month facts), FY and instant metrics, with `concept`, `accession_no`, `source_accessions`, `filed_at`, `calc_version`.
  - AC: when a newer filing changes a stored value, a `value_changes` row with reason `restatement` is written; a new `calc_version` that changes a value writes `correction` (tested, D-027).
  - AC: golden tests for 5 companies × 4 metrics (revenue, net_income, eps_diluted, total_assets) × last 8 quarters, hand-checked against press releases (YAML in `tests/golden/` with the release URL per value).

- **E4a-S5 Universe growth (D-035).** As the operator, I want a rule-based way to grow coverage without licensed index lists.
  - AC: `uv run marketdata universe suggest --top N` ranks domestic filers by their latest `dei:EntityPublicFloat` (from the nightly bulk companyfacts file) and writes a candidate CSV for review; filers with a missing or stale value are listed separately.

**Out of scope:** YTD differencing, Q4, TTM, computed metrics, quality checks (E4b).
**Demo:** `uv run marketdata metrics show <TICKER> --metric revenue --period Q` prints values with their source accession numbers.

### E4b — Derived metrics, quality checks, cross-checks (session 8)

**Goal:** complete quarterly series, TTM values, ratios and quality flags, and a cross-check of every earnings release against the later filing.
**Depends on:** E4a, E7a.

- **E4b-S1 Derived quarters.** As a user, I want quarterly cash flow even when filings only report year-to-date so every quarter is comparable.
  - AC: §9.4 rules 3–5 implemented (YTD differencing, Q4, no mixed concepts, no derived EPS or average shares).
  - AC: unit tests with synthetic facts for each rule; golden tests for `operating_cash_flow` and `capex` quarterly for 5 companies.
- **E4b-S2 TTM and computed metrics.** As a user, I want trailing-twelve-month figures and margins so I can compare companies with different year-ends.
  - AC: §9.4 rule 8 and the computed metrics in §9.3 (except price-based ones); tests for gaps and contiguity.
- **E4b-S3 Quality checks.** As the operator, I want suspicious values flagged so I can review them before a user sees a wrong number.
  - AC: every §9.5 check implemented, each with a unit test that triggers it.
  - AC: `uv run marketdata quality report` lists flags by company and metric.
- **E4b-S4 Golden set of 10.** As the operator, I want accuracy measured across sectors so we know where the rules break.
  - AC: golden set covers 10 companies including a bank, a January year-end retailer and a September year-end company; core-set metrics first (D-020).
  - AC: all golden tests pass, or each known difference is listed in §16 with its reason.
- **E4b-S5 Recompute.** As the operator, I want to rebuild all metrics from stored facts so rule changes apply to history.
  - AC: `uv run marketdata normalize --all` rebuilds `financial_metrics` without any SEC call; prints duration; `calc_version` updated.
- **E4b-S6 Release-vs-filing cross-check (D-028).** As a data evaluator, I want each earnings-release number compared with the audited filing so I can see it was right.
  - AC: when a 10-Q or 10-K for the same quarter is loaded, each `earnings_facts` slot gets `cross_check_status`, `cross_check_value` and `cross_check_accession` per §9.10; mismatches flag `release_vs_filing_mismatch` and appear in the quality report.
  - AC: fixtures for a matching quarter, a quarter whose numbers changed between release and filing, and a slot with no comparable value.

**Out of scope:** prices and price-based ratios (E10).
**Demo:** quality report, release-vs-filing results and golden test run.

### E5a — Data API (session 9)

**Goal:** the REST API for every M1 dataset, with proof on every value.
**Depends on:** E4b.

- **E5a-S1 Clients, keys and the envelope.** As the operator, I want per-client keys and a standard response format before the first data endpoint ships.
  - AC: CLI `marketdata clients create|list` and `marketdata keys create|list|revoke`; the key is shown once and stored as a sha256 hash.
  - AC: `X-API-Key` required on `/v1/*` except `/v1/status`; missing or revoked → 401; wrong scope → 403; over `rate_per_min` → 429.
  - AC: each `/v1` request writes `api_requests`; the §10.1 envelope with disclaimer and quality flags is on every success response (tested).
- **E5a-S2 Service functions.** As a developer, I want all query logic in `service/` so REST and MCP return identical data.
  - AC: `company_snapshot`, `latest_earnings`, `earnings_feed`, `get_earnings`, `get_financials`, `recent_filings`, `compare` and `coverage` return Pydantic models that include proof and quality flags.
  - AC: no SQL outside `db/repositories/`; integration tests against seeded Postgres.
- **E5a-S3 REST routes.** As an API integrator, I want documented, consistent endpoints so integration is fast.
  - AC: E5a routes from §10.2 with pagination and ticker normalization; OpenAPI at `/docs` with examples.
  - AC: a contract test per route checks that every value has a complete proof object (§9.10).
- **E5a-S4 Not-applicable handling.** As an API integrator, I want funds and foreign issuers to answer with a reason, not an error page.
  - AC: earnings, snapshot and financials return `not_applicable` (422) with the §9.1 reason for funds and foreign issuers; tested.

**Out of scope:** webhooks, change feed and proof pages (E5b); insiders, funds, prices (their epics).
**Demo:** create a client and key, then `curl -H "X-API-Key: …" localhost:8000/v1/companies/<TICKER>/earnings/latest` and `…/financials?period=Q`.

### E5b — Webhooks, change feed, proof pages and accuracy report (session 10)

**Goal:** clients are told when data is ready or changes, can see the proof for any number, and can read our measured accuracy.
**Depends on:** E5a.

- **E5b-S1 Webhooks.** As an API integrator, I want to be told when data is ready or changes so I don't poll.
  - AC: migrations create `webhook_destinations`, `webhook_events`, `webhook_deliveries`; CLI `marketdata webhooks create|list|disable|test` (the secret is shown once and stored encrypted).
  - AC: events, envelope, signing, delivery, retries and auto-disable per §9.11; a reference receiver in the tests verifies signatures; the retry schedule is tested with a mocked transport.
- **E5b-S2 Change feed.** As an API integrator, I want every change to data I already have, with the reason.
  - AC: `GET /v1/changes` per §10.2 serves `value_changes`; each new change emits `data.changed`; tested for each reason.
- **E5b-S3 Proof pages.** As a data evaluator, I want to open any number and see where it came from.
  - AC: proof pages per §9.10 for releases and metric values, showing value, proof, SEC links, checks, cross-check and change history; Basic auth with an `api`-scope key.
- **E5b-S4 Accuracy report.** As a data evaluator, I want published accuracy, not promises.
  - AC: `uv run marketdata quality accuracy` and `GET /v1/status` publish the §9.10 accuracy figures from live data.
- **E5b-S5 Demo guide.** As the operator, I want a repeatable demo for any prospective client: product, engineering, and data or compliance people.
  - AC: `docs/DEMO.md` covers: the API in curl or Postman; a webhook received by a test receiver; a proof page for a live number; a release cross-checked against its 10-Q; the accuracy report and live stats; the integration on one page.

**Out of scope:** MCP (E6), web UI (E15), localization (E7b), sales material (outside the repo, §17).
**Demo:** the API, webhook, proof and accuracy parts of `docs/DEMO.md` on the live VM.

### E6 — MCP server (session 11) → Milestone M1

**Goal:** the same data in AI chats, as a first-class interface (D-032).
**Depends on:** E5a.

- **E6-S1 MCP server.** As an AI chat user, I want to connect the platform to my AI client so it can answer with real filing data.
  - AC: official Python SDK, Streamable HTTP at `/mcp`, mounted in the FastAPI app; `X-API-Key` (api scope) required.
  - AC: MCP Inspector connects and lists tools; requests without a valid key are rejected.
  - AC: each tool call writes `api_requests` (method `MCP`, path = tool name).
- **E6-S2 Tools.** As an AI chat user, I want task-shaped tools so the AI picks the right one without trial and error.
  - AC: `lookup_company`, `company_snapshot`, `get_financials`, `recent_filings`, `compare_companies`, `latest_earnings` per §10.3, each calling `service/`.
  - AC: input validation with clear error messages; outputs under about 8 KB; lists capped at 20; source URLs included.
  - AC: in-process tests call each tool.
- **E6-S3 MCP guide.** As an AI chat user, I want clear setup instructions for my client.
  - AC: `docs/MCP.md` with setup for Claude Desktop, Claude Code and at least one other client, five example questions (including one in Georgian) and the tool calls they should trigger; linked from `docs/DEMO.md`.

**Out of scope:** OAuth, public directory listings (§3).
**Demo:** ask an AI client "How did <company>'s revenue and free cash flow change over the last 8 quarters?" and get an answer citing SEC filing links.

### E10 — Prices, end-of-day and licensed (session 12)

**Goal:** end-of-day prices and the price-based metrics that need them, in every channel the vendor license allows (D-031).
**Depends on:** E5a; OD-4.

- **E10-S1 Vendor and license.** As the operator, I want the price license recorded and enforced in code so we never show a price where we may not.
  - AC: the chosen vendor's terms recorded in §15 (display scopes, attribution, delay, cost model) before any load.
  - AC: migration creates `price_vendors` and `prices_eod`; `PriceProvider` interface, `FakePriceProvider` for tests, one real adapter.
  - AC: the service layer returns price fields only for channels in `display_scopes`, otherwise null with flag `price_not_licensed` (tested for each channel).
- **E10-S2 Daily load and backfill.** As the operator, I want prices loaded automatically every trading day.
  - AC: a scheduler job at 22:30 UTC enqueues `prices_eod`; `uv run marketdata backfill prices --years 2`; missing days and splits are logged in `docs/EDGE_CASES.md`.
- **E10-S3 Price-based metrics.** As a user, I want market value and P/E next to the fundamentals, with the price date.
  - AC: snapshot, compare and MCP `company_snapshot` show the latest close, market cap and P/E with price date and source where licensed (§9.12).
  - AC: the client-supplied `price` parameter still works and takes precedence; it is never stored; `price_source` says which was used.
- **E10-S4 Documentation.** As an API integrator, I want to know where prices come from and when to pass my own.
  - AC: a section in `docs/INTEGRATION.md`.

**Out of scope:** real-time or intraday prices; corporate-action history beyond what the vendor provides.
**Demo:** a snapshot showing market cap and P/E with the price date, and the same request from a channel outside the license returning `price_not_licensed`.

### E15 — Web UI (sessions 13–14) → Milestone M2

**Goal:** a small, fast, read-only website where invited testers (later, anyone) can look up a company and check every number (D-034).
**Depends on:** E5b, E6; E10 for price fields (the UI can launch without them).

- **E15-S1 Skeleton and access.** As the operator, I want the UI in the same app and deployment so there is one thing to run.
  - AC: `web/` package with server-rendered Jinja2 pages; htmx and Chart.js vendored as static files with pinned versions; a base layout with the disclaimer on every page.
  - AC: `UI_MODE=private` requires Basic auth with an `api`-scope key; `UI_MODE=public` applies per-IP rate limits (both tested).
  - AC: no SQL in `web/`; no cookies beyond access control; no third-party requests from the browser.
- **E15-S2 Search and company page.** As a UI visitor, I want one page that shows how a company is doing and lets me check every number.
  - AC: search by ticker or name; the company page per §9.14 (identity, class, latest release, 8-quarter core-metric history with a chart, latest close where licensed, recent filings with labels).
  - AC: every number links to its proof page; funds and foreign issuers get a "what we cover" page instead of an error.
- **E15-S3 Compare page.** As a UI visitor, I want 2–5 companies side by side.
  - AC: a compare page built on `service.compare`, with proof links.
- **E15-S4 Proof and status pages in the UI.** As a data evaluator, I want proof and accuracy one click away.
  - AC: the E5b proof pages use the UI layout and access rule; a status page shows freshness, the track record and the accuracy report.
- **E15-S5 Quality.** As the operator, I want the UI tested like the API.
  - AC: render tests for each page with seeded data; a test that every number on the company page has a proof link; pages work at phone width; basic accessibility checks (labels, contrast, keyboard navigation).

**Out of scope:** accounts, watchlists, alerts, Georgian (E7b); insider and fund sections (added by E8 and E9).
**Demo:** open a company page on the live VM, click a revenue figure, and land on its proof page with the SEC quote and the 10-Q cross-check.

### E8 — Insider trades (session 15)

**Goal:** clean, readable insider buying and selling for universe companies.
**Depends on:** E5a, E6 and E15 (E3 for the watcher).

- **E8-S1 Form 4 parser.** As a developer, I want Form 4 XML parsed into typed records so trades are stored consistently.
  - AC: parses owners and roles, non-derivative and derivative lines, footnotes, and the 10b5-1 checkbox (§9.7).
  - AC: fixtures for at least 5 real filings, including a 4/A, a filing with several owners, and one with the 10b5-1 box checked.
- **E8-S2 Wiring.** As the operator, I want new Form 4s processed automatically.
  - AC: migration creates `insider_owners` and `insider_transactions`; `form4_parse` handler registered; the watcher enqueues it.
- **E8-S3 Amendments.** As a user, I want corrected filings to replace the originals so I don't see a trade twice.
  - AC: §9.7 superseding rule with tests for matched and unmatched amendments.
- **E8-S4 Backfill.** As the operator, I want two years of history so the feature is useful from day one.
  - AC: `uv run marketdata backfill insiders --quarters 8` uses the SEC Insider Transactions datasets for universe issuers.
  - AC: a reconciliation report compares a sample of companies between dataset and XML parsing; differences listed in §16.
- **E8-S5 Serve.** As a user, I want to see who bought or sold, how much, and at what price.
  - AC: `GET /v1/companies/{ticker}/insiders` and MCP `insider_activity` with the §9.7 aggregation; `signal_only=true` by default; 10b5-1 marked.
  - AC: an insider section on the UI company page (§9.14), with proof links to the Form 4 filings.

**Demo:** insider activity for a company over 365 days via REST and via an AI client.

### E9 — 13F fund portfolios (session 16) → Milestone M3

**Goal:** what well-known investors hold and how that changed, plus which of them hold a given company.
**Depends on:** E5a, E6 and E15 (E3 for the watcher).

- **E9-S1 Curated managers.** As a user, I want to follow well-known investors by name.
  - AC: `data/managers.csv` with 20–30 managers; each CIK looked up on EDGAR and noted; migration creates `managers`; loader command; the watcher includes manager CIKs.
- **E9-S2 13F parser.** As a developer, I want information tables parsed and normalized so values are comparable across years.
  - AC: migration creates `holdings_raw`; parser handles the §9.8 fields, unit normalization (cutoff verified and recorded in §15) and RESTATEMENT / NEW HOLDINGS amendments.
  - AC: the cross-manager `unit_suspect` check (§9.8, D-023) is implemented and tested.
  - AC: fixtures for one older filing (thousands), one recent filing (dollars) and one RESTATEMENT amendment.
- **E9-S3 CUSIP mapping.** As a developer, I want CUSIPs mapped to our securities so positions link to companies.
  - AC: migration creates `cusip_map`; OpenFIGI client with its own rate limit and caching; unmatched and ambiguous cases stored with status.
- **E9-S4 Positions and changes.** As a user, I want to see a fund's biggest positions and what it bought or sold last quarter.
  - AC: migration creates `fund_positions`; §9.8 merge, weights and change types implemented with tests.
- **E9-S5 Backfill.** As the operator, I want eight quarters of history for curated managers.
  - AC: `uv run marketdata backfill funds --quarters 8` from per-filing XML (or the SEC 13F datasets if simpler).
- **E9-S6 Serve.** As an API integrator and AI chat user, I want portfolios and holders available through the API and MCP.
  - AC: `GET /v1/funds`, `GET /v1/funds/{manager_cik}/holdings`, `GET /v1/companies/{ticker}/funds` and MCP `investor_portfolio`; each shows report period and filing date.
  - AC: fund pages and a "held by" section on the UI company page, showing report period and filing date (§9.14).

**Demo:** "What did <manager> buy and sell last quarter?" answered by an AI client with links to the 13F filing.

### E11 — Operations and production readiness (sessions 17–18) → Milestone M4

**Goal:** a deployed, monitored, documented service a client can evaluate.
**Depends on:** E15, E9; OD-1.

- **E11-S1 Production deployment.** As the operator, I want a repeatable deployment so the service can be rebuilt quickly.
  - AC: builds on the E7c live setup: multi-stage Dockerfile running as non-root; production Compose file with Caddy (HTTPS); a deploy script; documented in `docs/DEPLOY.md`.
- **E11-S2 Backups.** As the operator, I want backups I've proven I can restore.
  - AC: nightly `pg_dump` to object storage with 14-day retention; `scripts/restore_test.sh` restores into a scratch database and runs smoke checks.
- **E11-S3 Monitoring and alerts.** As the operator, I want to know about problems before a client does.
  - AC: Sentry in all processes; external uptime checks on `/health` and the UI.
  - AC: `GET /v1/status` reports freshness (minutes from SEC filing time to availability), the accuracy report, queue depth and dead tasks.
  - AC: alerts for dead tasks, failed extractions, cross-check mismatches, new quality flags on golden-set companies, freshness above 60 minutes, missing price loads, and auto-disabled webhook destinations.
- **E11-S4 Golden set of 50.** As the operator, I want accuracy measured at scale.
  - AC: 50 companies across sectors in the golden sets; the E5b accuracy report covers them; results recorded in §15.
- **E11-S5 Integration guide.** As an API integrator, I want everything needed to integrate in one document.
  - AC: `docs/INTEGRATION.md` covering auth and scopes, endpoints, envelope, proof objects, errors, rate limits, webhooks and signature verification, the change feed, prices and client-supplied prices, data provenance, update times, and a draft SLA; OpenAPI published.
- **E11-S6 Security, privacy and license review.** As the operator, I want a checklist I can hand to a client's vendor-risk team.
  - AC: `docs/SECURITY.md`: no end-user data and no contact with a client's users; UI privacy (no accounts, no trackers, truncated IP addresses); secrets handling (including webhook secrets); proof-page and UI access; DB roles (API role read-only); TLS; key rotation; logging and retention; dependency license audit (no AGPL); the licensed-data rule and the price license (D-017, D-031).
- **E11-S7 Usage metering.** As the operator, I want usage per key across the API and MCP so any pricing can rest on real use.
  - AC: `uv run marketdata usage --since <date>` reports calls per key, endpoint or tool, and day from `api_requests`.

**Demo:** the deployed URL, the UI, the status page with the accuracy report, and the integration guide shown end to end.

### E7b — Georgian localization and content controls (on demand)

**Goal:** when a client or our own UI needs it, Georgian-ready fields and client-controlled templates on top of the verified data, with approval, pause and retraction (D-026).
**Depends on:** E5a; OD-6, OD-8, OD-9.

- **E7b-S1 Localized fields.** As a copywriter, I want company names, periods and numbers ready in Georgian, in the grammatical forms my sentences need.
  - AC: migration creates `company_names_ka`; loader for `data/company_names_ka.csv`; period labels, noun forms and number formatting per §9.13 and OD-6.
  - AC: `data/glossary_ka.csv` with at least 50 terms, reviewed (review recorded in §15).
  - AC: a test proves every `display_ka` and `display_en` string encodes exactly the stored value.
- **E7b-S2 Templates.** As a copywriter, I want my own wording filled with verified numbers so all customer-facing copy stays ours.
  - AC: migration creates `client_templates`; CLI `marketdata templates upload <client> <kind> <lang> <file>`; sandboxed rendering per §9.13; `render=` on the earnings endpoints.
  - AC: sample `earnings_card` and `earnings_push` templates (EN and KA) render for 5 fixture releases; a template that tries arithmetic or access outside the localized fields is rejected; a missing value returns nothing plus its name.
- **E7b-S3 Content controls.** As a client's compliance team, I want to approve, pause and withdraw rendered content.
  - AC: migrations create `client_settings` and `content_reviews`; `review`-scope keys; review endpoints, client settings and `/review` pages per §9.13.
  - AC: integration tests: pending content is not rendered for `api` keys; a paused client gets no rendered content; a retraction emits `content.retracted`.

**Out of scope:** LLM drafts (E13).
**Demo:** a Georgian card rendered from a client template for a live release, approved on a review page.

### E12 — LLM fallback extractor and guidance direction (later, optional)

**Goal:** raise extraction coverage and add guidance direction, with the LLM only reading (D-014).
**Depends on:** the E7a hit-rate report; OD-2, OD-10.

- **E12-S1 LLM client.** As a developer, I want the LLM behind an interface so the provider can change.
  - AC: `LLMClient` interface, `AnthropicClient` adapter (model from `LLM_MODEL`), `FakeLLMClient` for tests; retries; tokens and cost logged per call.
- **E12-S2 LLM extractor.** As the operator, I want slots the rules missed found by an LLM and still verified by code.
  - AC: `LLMExtractor` implements the E7a `Extractor` interface; called only for slots the rules did not pass; returns a value and an exact quote; verification unchanged (§9.9); `method = llm`.
  - AC: hit rate on the same test set before and after, recorded in §15.
- **E12-S3 Guidance direction.** As a user, I want to know if the company raised or lowered its outlook.
  - AC: slot `guidance_direction` (`raised`, `lowered`, `maintained`, `initiated`, `withdrawn`) with the exact guidance sentence, verified as a substring of the release; golden tests on 20 releases.

**Out of scope:** any free text for readers.

### E13 — Georgian company profiles and filing sections (later)

**Goal:** "what this company does" in Georgian, drafted for copywriters or editors (a client's or ours) to edit, and filing sections for AI chats.
**Depends on:** E6, E7b; OD-2; a decision on LLM-written text recorded in §15, and the approval of any client that shows it (guardrail 9).

- **E13-S1 10-K sections.** As an AI chat user, I want a company's business description, risk factors and management discussion.
  - AC: Item 1, 1A and 7 extracted into `documents`; MCP tool `get_filing_section` added (decision recorded in §15, as it changes D-006).
- **E13-S2 Profile drafts.** As a copywriter, I want a draft profile I can edit instead of writing from scratch.
  - AC: migration creates `drafts`; English draft, then Georgian draft; every number checked against the source; drafts edited and approved on the review pages; only copywriter-approved text is served.

**Out of scope:** risk-factor summaries for readers.

### E14 — ETF holdings, N-PORT (later)

**Goal:** "what's inside my ETF" and "which ETFs hold this stock".
**Depends on:** E2, E5a.

- **E14-S1 Holdings.** As a user, I want to see an ETF's top holdings and their weights.
  - AC: series/class ↔ ticker mapping for universe ETFs; N-PORT parsing into holdings with weights; report period and filing date always shown (public lag verified and recorded in §15).
- **E14-S2 Serve.** As an API integrator, I want ETF holdings and look-through by API.
  - AC: `GET /v1/etfs/{ticker}/holdings` and `GET /v1/companies/{ticker}/etfs`.

---

## 13. Milestones

| Milestone | Epics | What it proves |
| --- | --- | --- |
| M1 — Trusted data engine | E0–E3, E7a, E7c, E4a, E4b, E5a, E5b, E6 | Verified earnings facts with a live track record from the Q3 2026 season; verified financials cross-checked against filings; the REST API, webhooks, change feed, proof pages, a published accuracy report, and MCP. The live run (E7c) should start by early-to-mid November 2026 so part of the season is captured. |
| M2 — Our own UI | E10, E15 | Licensed end-of-day prices and a read-only web UI where every number links to its proof. |
| M3 — Breadth | E8, E9 | Insider trades and famous investors' portfolios in the API, MCP and UI. |
| M4 — Production-ready | E11 | Hardened deployment, monitoring, usage metering, integration, security and privacy docs. |
| On demand | E7b, E12, E13, E14 | Built when a client or our UI needs them, or when the E7a hit-rate report calls for E12. |

After M1 and the discovery conversations (§17), choose the market (OD-12). The order of M2–M4 can change with that choice.

---

## 14. Open decisions

Resolve each before the epic in "Needed by" starts. When decided, move it to §15.

| ID | Decision | Options | Needed by |
| --- | --- | --- | --- |
| OD-1 | Hosting | One small always-on VM (e.g. Hetzner) for the live run, reused for M2–M4 / AWS or Azure in an EU region | E7c |
| OD-2 | LLM provider and model | Anthropic (first adapter planned) / other. Not needed for M1–M4 | E12 or E13 |
| OD-3 | Starter universe | About 30 domestic filers chosen for variety (§9.1), plus a few ETFs and foreign issuers; growth by D-035 | E2 |
| OD-4 | Price vendor | End-of-day (or delayed) prices with display rights for our UI and MCP, and optionally API redistribution; compare cost, attribution rules and history depth (D-031) | E10 |
| OD-6 | Georgian number formatting | Decimal and thousands separators, currency and scale words | E7b (on demand) |
| OD-7 | Product and repository name | `marketdata` is a placeholder | E0 |
| OD-8 | Template ownership | The client renders from our data on its side / the client registers templates with us / both (default: both) | E7b (on demand) |
| OD-9 | Review model for rendered content | Manual approval of every item / auto-approve after N clean items (default: manual) | E7b (on demand) |
| OD-10 | When to build the LLM fallback | Build E12 if the E7a hit rate is below a threshold (proposed: 90% of slots passed across the test set) | After E7a |
| OD-11 | Commercial delivery options | API service we run / data license into a client's warehouse / deployment in a client's infrastructure with a maintenance contract / paid UI or MCP plans | After OD-12 |
| OD-12 | Target market | Banks and brokers with trading apps / fintechs / AI-tool builders / retail investors directly / a mix | After M1 and discovery (§17) |
| OD-13 | Public UI launch | Stay private for invited testers / go public. Condition: the price license allows public display (or prices are hidden) | Before `UI_MODE=public` |

---

## 15. Decision log

Append-only. Sessions add entries here; never edit old ones (supersede them with a new entry instead).

| ID | Date | Decision | Why |
| --- | --- | --- | --- |
| D-001 | 2026-10-12 | Python 3.12 for the whole MVP | Most MVP work is SEC parsing and data cleaning, where Python's ecosystem is strongest. Guram is more confident in .NET and accepts the trade-off. |
| D-002 | 2026-10-12 | PostgreSQL 16 is the only database | Data is relational (joins across companies, filings, facts, holdings); upserts make tasks idempotent; queue, JSONB and later pgvector in one system. |
| D-003 | 2026-10-12 | Raw files are kept write-once in object storage | Parsing fixes re-read stored files instead of re-downloading. |
| D-004 | 2026-10-12 | No congressional trading data in the MVP | Commercial-use restriction on these reports in the Ethics in Government Act. |
| D-005 | 2026-10-12 | The metric catalog lives in code, not in a database table | Versioned in git together with the rules that use it. Deviates from the architecture doc's `metric_map` table. |
| D-006 | 2026-10-12 | The MCP server has 8 tools, including `recent_filings` | The M1 demo needs filings in AI chats. Deviates from the architecture doc's 7 tools. |
| D-007 | 2026-10-12 | One worker process with async concurrency in the MVP | Keeps the SEC rate limit enforceable in one process; revisit if throughput is too low. |
| D-008 | 2026-10-12 | `fiscal_year` = calendar year in which the fiscal year ends | Consistent across companies; can differ from a company's own label, so period dates are always returned. |
| D-009 | 2026-10-12 | Core code does not import edgartools or pandas; notebooks may | We own the standardization rules; avoids depending on a library's behaviour and license. |
| D-010 | 2026-10-19 | Positioning: a verified, plain-language, Georgian content layer packaged for banks — not a broad US market-data API | Raw US fundamentals are a commodity; Financial Datasets and others sell 40+ datasets cheaply, machine-first, and are already in AI chat directories. Our edge is language, readability, bank packaging and verification. |
| D-011 | 2026-10-19 | New build order: E0–E3, then earnings content (E7a–E7c) as Milestone M1, then financials (E4a–E6), insiders, 13F, prices, operations. `latest_earnings` moves to E6. Supersedes the 2026-10-12 order in §2 and the milestone definitions in §13 | Earnings content doesn't depend on XBRL normalization, and the first demo must show what general data APIs don't offer. Earnings season (to late November 2026) is the window for a live demo and a track record. |
| D-012 | 2026-10-19 | The bank owns the channel: we never contact end users; content is delivered by API and webhook, white-label by default; the bank approves, pauses and retracts | Banks don't let vendors talk to their customers. Keeps vendor review small and avoids competing with partner banks. |
| D-013 | 2026-10-19 | Template mode is the default: the bank's copywriters write templates; we supply verified slots. Free-text drafts come later (E13) | From Guram's experience at Bank of Georgia: all customer-facing copy is written in-house by copywriters and the product manager. |
| D-014 | 2026-10-19 | No LLM in stage 1. Extraction is rule-based with code verification. Later the LLM may only find numbers (E12, still verified by code) or draft text for copywriters with the bank's approval (E13). "AI reads, it doesn't write." Supersedes the LLM summaries in the 2026-10-12 E7a/E7b | Deterministic, cheaper and easier to test; removes a separate AI-content approval track at banks. |
| D-015 | 2026-10-19 | Extraction sits behind an `Extractor` interface; verification is independent of it; every value records `method` and `extractor_version`; press releases stay in the raw store | Lets an LLM extractor be added later without changing verification, and lets both be compared on the same test set. |
| D-016 | 2026-10-19 | No price storage or redistribution in the bank channel; price-based metrics use a client-supplied price; `prices_eod` dropped from the MVP. Supersedes the 2026-10-12 E10 | Banks already license prices for their apps; exchanges treat display to customers as distribution with per-user and redistribution fees. Takes the price vendor off the pilot path. |
| D-017 | 2026-10-19 | No licensed data without a license recorded here: SIC codes instead of GICS; no consensus estimates, analyst ratings, news articles or call transcripts | GICS must be licensed from MSCI and S&P; estimates, news and transcripts are proprietary or copyrighted. |
| D-018 | 2026-10-19 | ETFs and foreign private issuers resolve as classified securities with a "not applicable" reason for fundamentals. Supersedes the 2026-10-12 non-goal "ETFs and funds as securities" and the §9.1 rejection rule | Retail holdings often include ETFs and popular foreign issuers; returning "not found" for them would look broken in a bank app. |
| D-019 | 2026-10-19 | Webhooks use a signed envelope (`t=…,v1=…` HMAC-SHA256), are delivered through the task queue, with 3 attempts and auto-disable after 50 failures | Proven pattern (documented by Financial Datasets and Stripe); reuses the queue we already have. |
| D-020 | 2026-10-19 | A pilot metric set (§9.3) gets golden tests and API defaults first; the rest of the catalog stays at lower priority | Effort goes to the numbers a bank app actually shows. |
| D-021 | 2026-10-19 | `docs/EDGE_CASES.md` is kept from E0 onward; every session appends what broke and how it was handled | It is the evidence for "a pipeline, not a prompt" when a bank considers building in-house. |
| D-022 | 2026-10-19 | Clients, API keys with scopes, the request log and the response envelope move from E5 to E7b-S1 | The first endpoint now ships in E7b. |
| D-023 | 2026-10-19 | The 13F `unit_suspect` check compares implied prices across curated managers instead of using market prices | Follows D-016: no price data in the MVP. |
| D-024 | 2026-10-19 | Positioning refined: a trusted-data provider for banks — cleared, reviewed, verified data with proof per value, cross-checks, published accuracy and announced changes. Supersedes the "content layer" framing of D-010; D-010's point about not competing on breadth stands | Banks keep customer-facing copy in-house; the verified pipeline is the asset; Georgian presentation is thin and can be done by the bank or added on demand. |
| D-025 | 2026-10-19 | Build order: E0–E3, E7a, E7c (live run), E4a, E4b, E5a, E5b as Milestone M1; then E6, E8, E9 (M2); E10, E11 (M3); E7b and E12–E14 on demand. Supersedes D-011 | The live run starts during the Q3 2026 season; the demo shows trusted data, not presentation. |
| D-026 | 2026-10-19 | E7b becomes "Georgian localization and content controls", built on demand; templates, approval, pause and retraction move into it, and D-013 applies there. E5 splits into E5a (data API, including clients, keys and the envelope; supersedes D-022) and E5b (webhooks, change feed, proof pages, accuracy report, demo guide). E7c becomes the live run and track record | Follows D-024 and D-025. |
| D-027 | 2026-10-19 | Stored values never change silently: every change to an earnings fact or metric is logged in `value_changes` with old value, new value and reason (correction, restatement, amendment), served by `/v1/changes` and announced by `data.changed` | A data provider earns trust by how it handles corrections. |
| D-028 | 2026-10-19 | Each earnings-release value carries a cross-check status against the later 10-Q/10-K XBRL value; the match rate is published in the accuracy report | An independent check the bank can see, using data we already load. |
| D-029 | 2026-10-19 | "Intelligence" features come later and stay factual (for example, guidance raised or lowered, several insiders buying in the same week); no rankings, scores or signals | Anything that ranks stocks reads as advice to a bank's compliance team. |
| D-030 | 2026-10-23 | The product is a buyer-agnostic, trusted-data engine with three interfaces: REST API (with webhooks and change feed), MCP and our own web UI. The market is chosen after M1 and discovery (OD-12). Supersedes the bank focus of D-024; D-024's trust features and D-010's no-breadth rule stand | The engine is the same for every buyer; building it first keeps options open, and discovery across several segments replaces planning around one bank. |
| D-031 | 2026-10-23 | Prices return: end-of-day (or delayed) prices from one licensed vendor (OD-4), stored in `prices_eod` and shown only in channels its license allows (`display_scopes`); the client-supplied `price` parameter stays. Supersedes D-016 | Our own UI and MCP look incomplete without a share price; a client that licenses its own prices can still pass them. |
| D-032 | 2026-10-23 | Build order: E0–E3, E7a, E7c, E4a, E4b, E5a, E5b, E6 (M1); E10, E15 (M2); E8, E9 (M3); E11 (M4); E7b and E12–E14 on demand. MCP is a first-class interface, no longer for demos only. Supersedes D-025 | Interfaces follow the engine; the UI needs the price decision, so it comes after MCP. |
| D-033 | 2026-10-23 | The channel rule is generalized: D-012 applies to any client that shows our data to its users. We collect no end-user data in any interface. Public MCP directory listing and OAuth are post-MVP and no longer wait for a bank deal | Keeps the B2B principle without tying the product to banks. |
| D-034 | 2026-10-23 | The web UI is server-rendered in the same FastAPI app (Jinja2, htmx, Chart.js), read-only, with no accounts, trackers or third-party scripts; every number links to its proof. It starts private (Basic auth) and goes public only when the price license and OD-5 allow (OD-13) | Stays in Python with one deployment and a small security and privacy surface; makes proof the centre of the UI. |
| D-035 | 2026-10-23 | Universe: a starter list of about 30 domestic filers chosen for variety, plus a few ETFs and foreign issuers; growth toward the roughly 500 largest domestic filers by `dei:EntityPublicFloat` from SEC data, not index membership lists | A neutral start that tests edge cases early; growth needs no licensed data. |
| D-036 | 2026-10-23 | OD-5 resolved: Guram confirmed the employment check is OK. The code lives in his personal GitHub account | Clears the last blocker before E0. |
| D-037 | 2026-10-24 | Dev may use a local PostgreSQL instead of Docker; Guram's machine runs PostgreSQL 18 on 5432. The Docker Postgres in `docker-compose.yml` stays at 16 and publishes host port 5433 (localhost only), so the two can run side by side. CI and deployments stay on 16; D-002 stands | A local PostgreSQL 18 already held port 5432. CI on 16 catches any use of features newer than 16. |
| D-038 | 2026-10-24 | `GET /health` returns `{"status":"ok","db":"ok"}` (200) or `{"status":"error","db":"down"}` (503), with no envelope and no disclaimer. The API starts while PostgreSQL is down (the pool opens without waiting); the database check gives up after 2 seconds | Health is an operational endpoint that carries no data, so guardrail 1 and §10.1 don't apply. Starting while the database is down lets monitoring see a 503 instead of a refused connection. |
| D-039 | 2026-10-24 | `Competitors/` (a copy of Financial Datasets' published docs) is kept out of the repository via `.gitignore` | §17: discovery material stays outside the repo, and the content is a third party's. |
| D-040 | 2026-10-24 | PostgreSQL 18, the latest major (18.6 on this date), everywhere: Docker dev, CI, live and production. Images are pinned to the major (`postgres:18`), never `latest`; moving to a new major is a new decision here. Supersedes the version in D-002 (PostgreSQL stays the only database) and the 16-vs-18 split in D-037 | Guram wants the latest release, and his local server is already 18, so dev, CI and deployments now run the same major. Data directories don't carry over between majors, so a floating `latest` tag would break the volume and change CI without notice when 19 ships. |

---

## 16. Follow-ups

Sessions append unfinished work, known data differences and ideas here, with the epic and story they relate to.

| Date | From | Item | Suggested epic |
| --- | --- | --- | --- |
| 2026-10-19 | Planning | Earnings for foreign private issuers: detect results 6-Ks; extraction and verification work the same way | After E7a |
| 2026-10-19 | Planning | Guidance direction and non-GAAP measures need text reading | E12 |
| 2026-10-19 | Planning | Business segments need dimensional XBRL, which companyfacts omits: parse XBRL instances or the SEC Financial Statement and Notes data sets | Later |
| 2026-10-19 | Planning | Use Financial Datasets as an accuracy cross-check for golden sets only, never as a served source. Check its terms first; the account currently has no credits | E4b, E7a |
| 2026-10-19 | Planning | Insider ownership statements (Forms 3 and 5) | Later |
| 2026-10-19 | Planning | IPO feed from S-1/F-1 filings; IPO registration statements are probably not XBRL-tagged (verify), so financials would need extraction | Later |
| 2026-10-19 | Planning | Values as first reported (point-in-time) for institutional buyers; `filed_at` is kept on served values to make this possible | Later |
| 2026-10-19 | Planning | Georgian groups listed in London as a local hook for Georgian banks | Post-MVP |
| 2026-10-19 | Planning | Factual "intelligence" events: guidance direction, several insiders buying in the same week | After M3 (D-029) |
| 2026-10-23 | Planning | Public MCP listing in Claude's and ChatGPT's directories: needs OAuth and each directory's review | After M2 |
| 2026-10-23 | Planning | UI accounts, watchlists and alerts: bring personal data and its legal duties; decide only with a chosen market | After OD-12 |
| 2026-10-24 | E0-S1, S5 | **Blocker.** On Guram's managed Mac (Microsoft Defender, Kandji, Carbon Black), every binary signed with Developer ID "OpenAI OpCo, LLC (2DC432GLL2)" is killed at launch (exit 137). Astral's uv and ruff are signed that way. Until IT allows them, `uv.lock` can't be generated, `uv sync` and ruff can't run locally, and CI (`uv sync --locked`) can't pass. Code was checked meanwhile with mypy, pytest, black and pycodestyle in a pip venv outside the repo | E0 (IT request) |
| 2026-10-24 | E0-S5 | First push to `origin` (github.com/guramkashmadze/martketdata, still empty) and a green CI run on main, after `uv.lock` exists and Guram approves the push. Consider renaming the repo to `marketdata` before the first push | E0 |
| 2026-10-24 | E0 | `Dockerfile` and the `api`, `worker`, `scheduler` and `caddy` Compose services: E0's compose file has only `postgres` | E7c |
| 2026-10-24 | E0 | `tasks` has `unique (kind, key)`, so a finished task can't be enqueued again by a plain insert (E0's insert returns the existing row). E3 must decide how a re-run works, for example resetting a done row to `queued` | E3 |
| 2026-10-24 | E0 | Starlette warns that using `httpx` with its TestClient is deprecated and suggests `httpx2`. Check `httpx2`'s status and license when E1 picks the SEC HTTP client | E1 |
| 2026-10-24 | E0 | Plan text: D-034 and §9.14 say the UI goes public when "OD-5" allows, but OD-5 was the employment check (D-036); this probably means OD-13 | Next plan revision |

---

## 17. Business and discovery track (outside the code)

- **Discovery (during M1):** short conversations with 5–8 people across possible markets: a bank or broker with a trading app, a fintech, builders of AI tools, retail investors, and someone in research or education. Ask:
  1. What US company data they use today, from where, and what they pay.
  2. What they don't trust about it, or what breaks.
  3. What they would need to see to trust a new source.
  4. Which interface they would use: API, MCP or a website.

  Keep notes outside the repository; choose the market (OD-12) after M1 and these conversations.
- **Price vendor research (before E10):** compare end-of-day and delayed options on display rights (UI, MCP, API), attribution, history depth and cost (OD-4).
- **In demos:** show live data, proof pages, the accuracy report and the edge-case count, not the architecture or this plan.
- **Sales material** (for example a build-vs-buy one-pager for business clients) waits for OD-12 and stays outside the repository.
- **When Georgian is wanted (E7b):** draft the glossary from Georgian financial terms in common public use, collect two or three examples of the client's copy if it's for a client, and have a Georgian financial editor review the glossary and about ten samples.

---

## Appendix A — `CLAUDE.md` (copy to the repo root)

```markdown
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
```

---

## Appendix B — Session start prompt

Paste this at the start of each Claude Code session, replacing `<EPIC>`:

```text
We're building the marketdata platform. Read CLAUDE.md, then docs/PLAN.md sections 1–11 and epic <EPIC> in section 12.
Also check §2 Status, §14 Open decisions, §15 Decision log, §16 Follow-ups and docs/EDGE_CASES.md for anything that affects this epic.

Before writing code:
1. Summarize the epic's goal, stories and acceptance criteria in your own words.
2. List open questions, missing information, or conflicts with the plan or the existing code.
3. Propose an implementation order and the migrations you will add.
Wait for my OK.

Then implement story by story. After each story, run ruff, mypy and pytest and show me the results and how the acceptance criteria are met.
Append every edge case you hit to docs/EDGE_CASES.md as you go.
```

---

## Appendix C — End-of-session checklist

- [ ] Every completed story's acceptance criteria shown (test names or command output).
- [ ] `ruff check`, `ruff format --check`, `mypy --strict`, `pytest` green.
- [ ] Migrations added and applied; downgrade checked where practical.
- [ ] `.env.example` and §11 updated for any new setting.
- [ ] No LLM calls added outside E12/E13; no licensed data added without a §15 entry.
- [ ] Any change to stored values writes `value_changes` (tested).
- [ ] Edge cases appended to `docs/EDGE_CASES.md`.
- [ ] §2 Status updated; decisions in §15; leftovers and known differences in §16.
- [ ] Commit with message `E<id>: <short summary>`.
- [ ] Final message tells me what the next session should start with and any decision I need to make first.
