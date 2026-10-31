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
| E0 | Foundation | 1 | — | Done | 2026-10-24. PDM, black, isort and flake8 instead of uv and ruff (D-041); PostgreSQL 18 (D-040). Lint gate and 25 tests green on PostgreSQL 18 (Docker and local), and the first CI run on main is green ([run 40113361253](https://github.com/guramkashmadze/martketdata/actions/runs/40113361253), commit `a384273`) |
| E1 | SEC client and raw store | 2 | E0 | Done | 2026-10-24. httpx2 replaces httpx (D-042); `SEC_USER_AGENT` is required only by processes that call the SEC (D-043); rate limiter with a burst of one (D-044); migration `0002` (`raw_objects`). Fixtures for AAPL, GOOGL and TGT, 2.6 MB in total (D-048). Lint gate and 216 tests green on local PostgreSQL 18; the live demo stored 14 files through `marketdata sec get`. 12 edge cases logged. CI on main is green ([run 40181002170](https://github.com/guramkashmadze/martketdata/actions/runs/40181002170), commit `3b0d6b7`), after a CLI help test was fixed for GitHub's forced colour output (§16) |
| E2 | Security master and universe | 3 | E1, OD-3 | Done | 2026-10-25. Starter universe of 39 tickers checked against the SEC's files (D-050, resolves OD-3), extended to 74 on request (D-056); ticker rules and `data/security_overrides.csv` (D-051); filer classes, including the SIC 6221 rule for commodity ETFs (D-052); EDGAR names and migration `0003` (D-053); ticker history with a safety limit (D-054); layout and service conventions (D-055). Lint gate and 383 tests green on local PostgreSQL 18. The live load stored 39,036 securities and 9,183 companies; a second live load changed nothing. With D-056 the universe is 60 domestic filers, 9 foreign private issuers and 5 funds. 22 edge cases logged. CI on main is green ([run 40204940570](https://github.com/guramkashmadze/martketdata/actions/runs/40204940570), commit `b247eeb`) |
| E3 | Task queue and filings watcher | 4 | E2 | Done | 2026-10-26. Queue in PostgreSQL with forced re-runs, backoff and fencing (D-058); `marketdata worker`, `scheduler` (APScheduler 3.11, D-065), `backfill filings`, `filings list` and `tasks list|retry|purge-done`; one process calls the SEC at a time (D-062). Migration `0004`: `filings` keyed by accession and listing company, with the listing's file number (D-060), and `watch_state`, so unchanged submissions are neither stored nor parsed (D-059). `accepted_at` comes from submissions, checked per file after the SEC served Apple's file with every time shifted (D-057). 13D/13G codes verified and labelled on the subject's listing only (D-061); §9.6 gains eight 8-K items (D-064); routing starts after a company's first watch (D-063). Lint gate and 491 tests green on local PostgreSQL 18. Live run on the dev database, 2026-10-25 21:15–21:31 UTC: `backfill filings --years 5`, then scheduler and worker for 15 minutes. The backfill and the day's security-master load (no changes) took 52 seconds: 74 submissions files and 56 older pages gave 187,005 filings of the 74 universe companies (JPMorgan 102,256), all with acceptance times. Watch rounds at 21:15 and 21:25 found every file unchanged and stored nothing. 354 SEC requests in all, no failed task and nothing logged above info; both processes stopped cleanly on SIGTERM. `filings list FUBO --form 'SCHEDULE 13D'` shows Disney's 13D with the activist label; Disney's own listing has none. 8 edge cases logged. CI on main is green ([run 40236585444](https://github.com/guramkashmadze/martketdata/actions/runs/40236585444), commit `5ca042a`) |
| E7a | Earnings release extraction and verification (rule-based) | 5 | E3 | Done | 2026-10-27. No LLM (D-014); rules version 2 (D-079). S1: the press release found by exhibit type through the index page and stored once (D-070), with EDGAR's SGML wrapper stripped; task priority (D-067); predecessor CIKs (D-068); migration `0005` (`documents`, `tasks.priority`, `company_predecessors`). S2: the `Extractor` interface and lxml table grids (D-066). S3: `RuleExtractor`: statement tables, quarter columns (D-073), fiscal labels (D-074), scale, labels and TOML overrides (D-075). S4: verification in `verify/` (in the source, scale, sanity with D-076's EPS tolerance); migration `0006` (`earnings_releases` with `no_statement` (D-071), `earnings_facts`, `value_changes`); corrections and amendments (D-077). S5: `backfill earnings --quarters 4`, `extract report` and `earnings show`; 16 overrides; a golden set of 35 releases, all passing (D-078). Run through the worker on the dev database: 242 filings of 60 companies, with no SEC request for documents. Hit rate 81.2% (91.8% without the releases printed as pictures), so OD-10 calls for E12 (D-080); the version-2 run wrote 14 corrections. Lint gate and 912 tests green on local PostgreSQL 18. 40 edge cases logged. CI on main is green ([run 40541236490](https://github.com/guramkashmadze/martketdata/actions/runs/40541236490), commit `39da1d8`). Rules version 3 on 2026-10-28 (D-086), fixing what E4a's survey found: runs of years with one period length (Rivian's six months served as the quarter, Accenture's missing prior year), a table's own title over the lines above it and a current quarter near the filing (Meta dated a year early), net income "attributed to" the company (Gevo). The forced re-run of the 262 stored releases wrote 16 corrections; the hit rate stays 82.5%. Lint gate and 1,057 tests green; 5 edge cases logged. CI on main is green ([run 40762179787](https://github.com/guramkashmadze/martketdata/actions/runs/40762179787), commit `f423f11`) |
| E7c | Live run and track record | 6 | E7a, OD-1 | Not started | Starts the track record during the Q3 2026 season |
| E4a | XBRL facts and reported metrics | 7 | E3 | Done | 2026-10-28. Built before E7c (Guram's choice: E7c needs the server of OD-1). S1: `xbrl_facts` (migration `0007`), every us-gaap and dei fact from per-company companyfacts, each response kept, predecessors loaded too; `facts_refresh` routed with force (D-081). S2: catalog version 1, §9.3 plus four concepts from a survey of the 60 companies against E7a's verified releases (D-082). S3: durations widened for Costco, the latest filing wins among the 10-K and 10-Q family, year ends from each company's 10-Ks and transition reports (D-083). S4: `financial_metrics` (migration `0008`), reported quarters, years and instants, banks' revenue not applicable, restatement and correction value changes, predecessors merged in the service layer, `metrics show`; golden set of 5 companies × 4 metrics × 8 quarters, every value equal to its press release (D-084). S5: public float from the frames API with a share-count check, `frame_facts` (migration `0009`), `universe suggest` (D-085). Dev database: 1,149,093 facts and 69,121 metric rows for 61 CIKs; 500 candidates ranked, 309 suspect floats listed apart. The survey found two wrong E7a releases (Rivian, Meta) and companyfacts files missing 10-Qs (KO, V, NEE) (§16). Lint gate and 1,033 tests green on local PostgreSQL 18. 21 edge cases logged. CI on main is green ([run 40637015310](https://github.com/guramkashmadze/martketdata/actions/runs/40637015310), commit `f7f20ec`) |
| E4b | Derived metrics, quality checks, cross-checks | 8 | E4a, E7a | Done | 2026-10-29. S1: quarters derived from year-to-date facts (§9.4 rules 3–5): six months less three, nine less six, the year less nine months or less its first three quarters, one concept per derivation, a reported three-month quarter always first, no derived EPS or average shares (D-087); a predecessor's facts fill the periods the company's own filings don't report (D-088). S2: TTM and §9.3's computed metrics, catalog version 2, calc version 2.2, unit `percent` (migration `0010`, D-089). S3: every §9.5 check with a triggering test, `stale_data` decided when read, `marketdata quality report` (D-090). S5: `marketdata normalize --all` with no SEC request (D-091). S6: release-vs-filing cross-check after every facts refresh, stored release and rebuild; releases without a stated period end take it from the filing (migration `0011`, D-092). S4: golden set of 10 companies (a bank, a January retailer, a September year end), core set first: 658 values equal their releases, 26 known differences listed in §16 (D-093). Dev database at migration `0011`: 178,054 metric rows for 61 CIKs rebuilt from stored facts in about 6 s with no SEC request and no value changed (reported 90,711, of them 21,530 instants' TTM; derived quarters 13,275; TTM sums 39,040; computed 35,028); the 262 releases cross-checked: 703 slots matched, 2 mismatched (CoreWeave's 10-K in millions; indie Semiconductor's 10-K changing its year), 319 not comparable, 24 pending, a match rate of 99.7%. Quality report: 15,176 flagged rows; KO, NEE and V stale (companyfacts gaps). Lint gate and 1,159 tests green on local PostgreSQL 18. 13 edge cases logged. CI on main is green ([run 40822815612](https://github.com/guramkashmadze/martketdata/actions/runs/40822815612), commit `dba6f3a`) |
| E5a | Data API | 9 | E4b | Done | 2026-10-29. First `outlier_jump` was kept to revenue, total assets, total equity and shares outstanding (rules version 3, calc version 2.3; 4,311 flagged rows became 291, D-094). S1: `marketdata clients create\|list` and `keys create\|list\|revoke`, a key shown once and stored as its sha256; `X-API-Key` on the /v1 router: 401, 403, and 429 with Retry-After over the key's requests of the last minute; an `api_requests` row per /v1 request; the §10.1 envelope with numbers written from `Decimal` digit for digit (D-095, D-096); migration `0012`. S2: eight service functions returning data with sources, `as_of` and flags: the latest release whatever its status, the feed of verified releases, the 11 releases without a stated period end dated from their 10-Q or 10-K, predecessors merged, sources older than the filings backfill found through their facts (D-096 to D-098). S3: the §10.2 routes, `/docs` with examples from Apple's filings, a contract test per route on a hand-made dataset and on Apple's real filings loaded from the fixtures. S4: `not_applicable` (422) with the §9.1 reason for funds, foreign issuers and companies outside the universe (D-099). Demo on the dev database: client `demo`; `/v1/companies/AAPL/earnings/latest` and `…/financials?period=Q` (128 rows of the 16 core metrics, 9 sources, 20 ms); JPMorgan's snapshot led to listing only stake filings about the company. Lint gate and 1,242 tests green on local PostgreSQL 18. 5 edge cases logged. CI on main is green ([run 40930722018](https://github.com/guramkashmadze/martketdata/actions/runs/40930722018), commit `d698656`) |
| E5b | Webhooks, change feed, proof pages and accuracy report | 10 | E5a | Done | 2026-10-30. S1: webhooks through an outbox: the code that changes the data writes the event in the same transaction, `webhook_publish` builds the §9.11 envelope around the API's own object and `webhook_deliver` sends it signed to a checked public address, three attempts about 1 and 10 minutes apart, 50 failed deliveries in a row switching the destination off; `earnings.verified` only for releases routed live (`earnings_releases.live`), `financials.updated` when a facts refresh adds rows or changes values, `data.changed` per value change, `filing.event` per new filing with a high-importance label (D-100 to D-102); `marketdata webhooks create|list|disable|enable|test`, the secret printed once and stored encrypted (Fernet); `scripts/webhook_receiver.py`, the reference receiver the tests check deliveries with; migration `0013`. S2: `GET /v1/changes`, newest first, each change with the value served now and its proof (D-103). S3: proof pages for releases and metric values, Jinja2, HTTP Basic auth with an `api` key (D-104). S4: `marketdata quality accuracy` and `GET /v1/status` (no key) from golden values loaded into the database (migration `0014`), the time to verified over live releases only, D-028's tolerance kept (D-105). S5: `docs/DEMO.md`, run on the dev database: Apple's Q3 2026 release matched its 10-Q on all four slots; a webhook of each type delivered to the reference receiver, the first after one retry; accuracy: golden release slots 135 of 138, golden metric values 658 of 684, cross-check 99.7%, extraction 82.5%, no live release yet. The demo found the decrypted signing secret in a logged traceback: tracebacks carry no local values now (D-106). Lint gate and 1,334 tests green on local PostgreSQL 18. 4 edge cases logged. CI on main is green ([run 41175252635](https://github.com/guramkashmadze/martketdata/actions/runs/41175252635), commit `e13283e`) |
| E6 | MCP server | 11 | E5a | Not started | Milestone M1 |
| E10 | Prices, end-of-day and licensed | 12 | E5a, OD-4 | Not started | D-031 |
| E15 | Web UI | 13–14 | E5b, E6; E10 for price fields | Not started | Milestone M2 |
| E8 | Insider trades | 15 | E5a, E6, E15 | Not started | |
| E9 | 13F fund portfolios | 16 | E5a, E6, E15 | Not started | Milestone M3 |
| E11 | Operations and production readiness | 17–18 | E15, E9 | Not started | Milestone M4 |
| E7b | Georgian localization and content controls | on demand | E5a, OD-6, OD-8, OD-9 | Not started | When a client or our UI needs Georgian (D-026) |
| E12 | LLM fallback extractor and guidance direction | later | E7a report, OD-2 | Not started | Needed: the E7a hit rate is 81.2%, below OD-10's 90% (D-080); first for releases printed as pictures. When to build it is open (§16) |
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
| 10-K, 10-Q, 10-K/A, 10-Q/A (and transition reports) of a domestic filer | `facts_refresh` (key: cik) | E4a |
| 8-K or 8-K/A with item 2.02 from a domestic filer | `earnings_extract` (key: accession_no) | E7a |
| Other 8-K | none (items are stored on the `filings` row) | E3 |
| SC 13D, SC 13G and their structured successors (`SCHEDULE 13D` …, D-061) | none (form-level event labels on the subject company's listing, §9.6) | E3 |
| 4, 4/A on the issuer's listing (no file number, D-063) | `form4_parse` (key: accession_no) | E8 |
| 13F-HR, 13F-HR/A from a curated manager | `f13_parse` (key: accession_no) | E9 |
| Scheduler, after the US close | `prices_eod` (key: trade date) | E10 |
| (internal) | `webhook_publish` (key: event id), queued with the event in the transaction that changes the data (D-100) | E5b |
| (internal) | `webhook_deliver` (key: `event_id:dest_id`), one per active destination that takes the event's type, after live work and the watches (D-101) | E5b |

Only tasks whose handler is registered are enqueued, so early epics don't fill the queue with work nobody can do yet. A company's first watch stores its filings and routes nothing, and the older pages a backfill reads never route: history goes through each epic's backfill (D-063).

---

## 5. Tech stack

| Concern | Choice | Notes |
| --- | --- | --- |
| Language | Python 3.15 | Type hints everywhere (D-001, D-049). Pinned to the minor version (`>=3.15,<3.16`); moving to a newer one is a new decision |
| Packaging | PDM + `pyproject.toml` (D-041); build backend hatchling | `pdm.lock` committed; in-project `.venv`; Python 3.15 from the system (Homebrew `python@3.15` on macOS). PDM MIT, hatchling MIT |
| Lint and format | black (format), isort (imports), flake8 with flake8-bugbear, flake8-simplify, pep8-naming, flake8-async, flake8-datetimez (D-041) | `pdm run lint` checks; `pdm run fmt` fixes. Settings in `pyproject.toml` and `.flake8`. All MIT |
| Types | mypy `--strict`, with the pydantic plugin; `types-boto3[s3]` for boto3; `types-lxml` for lxml (E7a) | CI gate. MIT; types-lxml Apache-2.0 (dev only; pulls cssselect BSD-3, beautifulsoup4 MIT, soupsieve MIT, types-html5lib Apache-2.0) |
| Tests | pytest, pytest-asyncio; httpx2 for FastAPI's TestClient; moto (in-process S3) for the S3 raw store | No network in unit tests. MIT, Apache-2.0; moto Apache-2.0 (dev only) |
| CLI | typer | Entry point `marketdata`. MIT |
| HTTP client | httpx2 (async), D-042 | pydantic's maintained fork of httpx, same API. BSD-3; pulls in httpcore2 (BSD-3) and truststore (MIT: certificates from the OS trust store) |
| Retries | tenacity | Apache-2.0 |
| Rate limiting | Own async token bucket in `sec/client.py` | Process-wide, burst of one; see D-007, D-044 |
| Database | PostgreSQL 18 | D-002, D-040. Images pinned to the major (`postgres:18`), never `latest` |
| DB driver | psycopg 3 + psycopg_pool | Plain SQL in `db/repositories/` (Dapper style). LGPL-3.0, used as an unmodified library: allowed (not AGPL) |
| Migrations | Alembic, SQL written with `op.execute` | MIT. Pulls in SQLAlchemy (MIT) only as its migration runner; our code uses no ORM |
| Models and config | Pydantic v2, pydantic-settings | MIT |
| XML / HTML | lxml 6.1 (E7a, D-066) | Press release tables and text from one lxml parse; selectolax is not used. BSD-3 (bundles libxml2 and libxslt, MIT). Wheels for Python 3.15 on macOS and Linux |
| HTML pages and templates | Jinja2 3.1: proof pages (E5b), web UI (E15), sandboxed client templates (E7b) | BSD-3; pulls in MarkupSafe (BSD-3) |
| Web UI interactivity | htmx for server-rendered partials, Chart.js for charts; both vendored as static files with pinned versions | Permissive (BSD-family, MIT); verify in E15 (D-034) |
| Prices | `PriceProvider` interface; first real adapter per OD-4 | E10 (D-031) |
| Webhook signing | stdlib `hmac` / `hashlib`; secrets encrypted at rest with `cryptography` 50 (Fernet) | Apache-2.0 or BSD-3 (E5b); pulls in cffi (MIT) and pycparser (BSD-3). It was a dev dependency of moto before |
| API | FastAPI + uvicorn | MIT, BSD-3 |
| MCP | Official `mcp` Python SDK, Streamable HTTP, mounted in the FastAPI app | |
| Scheduler | APScheduler 3.11 (D-065) | MIT; pulls in tzlocal (MIT). Only `tasks/scheduler.py` uses it; it ships no type hints, so mypy skips its imports. 4.0 is still an alpha |
| Object storage | boto3 against the S3 API; local-folder adapter for dev | Cloudflare R2 or Backblaze B2 in production. Apache-2.0 (with botocore, s3transfer; jmespath and urllib3 MIT; python-dateutil Apache-2.0/BSD). Write-once details in D-046 |
| Logging | structlog (JSON in live and prod, pretty in dev) | MIT/Apache-2.0. Standard-library loggers (uvicorn, alembic, psycopg) share its formatter; logs go to stderr |
| Error tracking | sentry-sdk | Live VM and production |
| LLM | **Not used in M1–M4 (D-014).** From E12/E13: `LLMClient` interface; first adapter uses the Anthropic SDK | Model from config; OD-2 |
| Exploration | Jupyter, pandas, edgartools — in `notebooks/` only | Core code does not import them (D-009) |
| CI | GitHub Actions: `pdm install --check`, black, isort, flake8, mypy, migrations, pytest with a PostgreSQL 18 service | `pdm install --check` fails on a missing or stale lockfile |
| Deploy | Docker Compose on one VM, Caddy for TLS | OD-1 |

Adding a dependency: add it to this table in the same session and check its license. Add it with `pdm add <package> --update-reuse` (and relock with `pdm lock --update-reuse`): a plain `pdm lock` resolves every package again and moved more than a dozen other pins in E5b.

---

## 6. Engineering conventions and Definition of Done

**Repository layout**

```
marketdata/
  config.py              settings (pydantic-settings)
  log.py                 structlog setup (E0)
  cli.py                 typer CLI: ingest, backfill, extract, normalize, universe, company, tasks, clients, keys,
                         webhooks, quality, stats, usage, sec (E7b adds: review, templates)
  db/
    pool.py              connection pool
    migrations/          Alembic
    repositories/        one module per table group, plain SQL only
  sec/
    client.py            rate limiter, retries, User-Agent
    urls.py              URL builders (§8)
    raw_store.py         RawStore interface, LocalRawStore, S3RawStore
  ingest/                security_master.py, watcher.py, earnings.py, facts.py, cross_check.py (E4b), prices.py,
                         form4.py, f13.py; E5b: announce.py (webhook events), golden.py
  extract/               interface.py (Extractor), html_tables.py, earnings_rules.py, columns.py, units.py
  verify/                number_check.py, sanity.py, cross_check.py
  normalize/             metric_catalog.py, periods.py, metrics.py, changes.py, insiders.py, holdings.py, quality.py,
                         tickers.py, universe.py, security_master.py (E2), financials.py, computed.py,
                         cross_check.py (E4b); E5b: webhooks.py, golden.py
  prices/                E10: provider.py (PriceProvider), fake.py, <vendor>.py
  webhooks/              outbox.py, signing.py, deliver.py
  content/               E7b only: names_ka.py, periods_ka.py, numbers_ka.py, templates.py
  llm/                   E12/E13 only: client.py, anthropic_client.py, prompts/
  tasks/                 queue.py, registry.py, worker.py, scheduler.py
  service/               companies.py, earnings.py, financials.py, filings.py, compare.py, changes.py,
                         prices.py, status.py, clients.py, insiders.py, funds.py, quality.py (E4b),
                         served.py, pages.py, snapshot.py (E5a); E5b: events.py, webhooks.py,
                         proof.py, accuracy.py, json_text.py
  api/                   app.py, auth.py, envelope.py, errors.py, routes/, proof_pages/; E5a: deps.py
                         (a connection per request), params.py, examples.py (/docs), request_log.py;
                         E5b: proof_pages/ (render.py, routes.py, templates/), routes/changes.py,
                         routes/status.py
  mcp_server/            server.py, tools.py
  web/                   E15: routes.py, templates/, static/ (vendored htmx and Chart.js)
data/                    universe.csv, security_overrides.csv, extraction_overrides.yaml, managers.csv
                         (E7b: company_names_ka.csv, glossary_ka.csv, templates/)
notebooks/               exploration only
scripts/                 fetch_fixture.py and other one-off tools
tests/
  unit/  integration/  golden/ (incl. golden/earnings/)  fixtures/sec/
docs/                    PLAN.md, EDGE_CASES.md, DEMO.md, MCP.md, INTEGRATION.md, SECURITY.md, DEPLOY.md
.github/workflows/ci.yml
docker-compose.yml  Dockerfile (E7c)  pyproject.toml  pdm.lock  .python-version  .flake8
alembic.ini  .env.example  README.md  CLAUDE.md
```

Sales material and discovery notes stay outside the repository (§17).

**Rules**

- **Layers:** `api/`, `mcp_server/`, `web/` and `webhooks/` → `service/` → `db/repositories/`: webhooks send the API's own objects and reach their tables through `service/` (D-100). `ingest/` → `db/repositories/`; it writes webhook events in the transaction that changes the data (`ingest/announce.py`); `ingest/prices.py` uses the `prices/` adapters. No layer skips another. SQL lives only in `db/repositories/`.
- **Pure modules:** `extract/`, `verify/` and `normalize/` (and `content/` in E7b) take records and return records. No I/O inside them, so they can be unit-tested with fixtures.
- **Verification is independent of extraction (D-015).** `verify/` never imports from `extract/`; it checks values against the stored document, whoever produced them.
- **Time:** all timestamps are UTC `timestamptz`; period boundaries are `date`.
- **Numbers:** reported values are `numeric` in Postgres and `Decimal` in Python. Never `float` for financial values.
- **Identifiers:** CIK stored as `bigint`, zero-padded to 10 digits only when building SEC URLs. Accession numbers stored with dashes, e.g. `0000320193-24-000123`.
- **Idempotency:** every write is an upsert on a natural key. Running any task twice gives the same rows.
- **Value changes (D-027):** an upsert that changes an already stored value in `earnings_facts` or `financial_metrics` also writes a `value_changes` row (old value, new value, reason) in the same transaction. `normalize/changes.py` decides the reason.
- **Errors:** service functions raise domain exceptions (`NotFound`, `InvalidParameter`, `NotApplicable`, `Forbidden`); `api/errors.py` maps them to the error envelope (§10).
- **Logging:** structured, with `cik`, `accession_no`, `task_id`, `client_id` fields where relevant.
- **Secrets:** only from environment variables. Never in code, fixtures or logs. Webhook signing secrets are stored encrypted. A logged traceback names frames and lines but no local values (D-106).
- **Edge cases:** every parser surprise, override, amendment oddity or failed check that teaches us something is appended to `docs/EDGE_CASES.md` (date, epic, company and accession, what broke, how it was handled).

**Testing**

- Parsers and extractors are tested with real SEC files saved in `tests/fixtures/sec/` (use `scripts/fetch_fixture.py`). Keep fixtures small (total under 20 MB; trim large JSON where possible).
- Integration tests (`tests/integration/`, marker `integration`) run against PostgreSQL at `TEST_DATABASE_URL`. Its database name must end in `_test`: each session drops and recreates the schema with Alembic, and each test runs in a transaction that is rolled back. They fail rather than skip when PostgreSQL is down. `pytest -m "not integration"` runs the unit tests alone.
- `tests/unit/test_layers.py` enforces the layer rules by reading imports: `verify/` never imports `extract/`, pure modules import nothing that does I/O, interfaces never import `db/repositories/`, and SQL string literals appear only in `db/repositories/` and migrations.
- Golden tests (`tests/golden/*.yaml`, `tests/golden/earnings/*.toml` (D-078)) hold hand-checked values, each with the SEC URL it was checked against. They are the accuracy contract.
- The four-quarter earnings backfill is the extraction test set; `marketdata extract report` measures the hit rate on it (§9.9).
- LLM tests (E12/E13 only) use `FakeLLMClient` with recorded responses; no live LLM calls in CI.

**Definition of Done (per story)**

- Acceptance criteria met and shown (a test, or a command with its output).
- `pdm run lint` (black, isort, flake8, `mypy --strict`) and `pdm run pytest` all green.
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
  priority     int not null default 0,        -- E7a (D-067): 10 live, 0 periodic, -10 backfills
  run_after    timestamptz not null default now(),
  locked_until timestamptz,                   -- reclaim if a worker dies
  last_error   text,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),
  unique (kind, key)
);
create index tasks_ready on tasks (status, priority desc, run_after);

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
-- E5a (migration 0012, D-095): key_hash is 64 lowercase hex digits and rate_per_min above 0;
-- the rate limit counts a key's requests of the last minute
create index api_requests_by_key on api_requests (key_id, at);

-- E1
create table raw_objects (
  key           text primary key,             -- path in the raw store
  url           text not null,
  sha256        text not null,
  size_bytes    bigint not null,
  content_type  text,
  fetched_at    timestamptz not null default now()
);

-- E2 (migration 0003; D-053: name may be null, value checks, one current row per ticker)
create table companies (
  cik              bigint primary key,
  name             text,                      -- EDGAR's name; null = listed only in company_tickers_mf.json
  sic_code         text,
  sic_description  text,
  fiscal_year_end  text,                      -- 'MMDD' as the SEC gives it (checked for shape)
  state_of_inc     text,
  filer_type       text not null default 'unknown'
                   check (filer_type in ('domestic','foreign_private_issuer','fund','other','unknown')),
  in_universe      boolean not null default false,
  updated_at       timestamptz not null default now()
);

create table securities (
  security_id    bigserial primary key,
  cik            bigint not null references companies(cik),
  ticker         text not null,               -- as the SEC lists it, e.g. 'BRK-B' (canonical, D-051)
  security_type  text not null default 'common'
                 check (security_type in ('common','preferred','fund','adr','other')),
  exchange       text,
  share_class    text,
  cusip          text,
  figi           text,
  valid_from     date not null,
  valid_to       date,                        -- null = current
  unique (ticker, valid_from),
  check (valid_to is null or valid_to > valid_from)
);
create unique index securities_current_ticker on securities (ticker) where valid_to is null;
create index securities_by_cik on securities (cik);

-- E3 (migration 0004; D-060: one row per filing and listing company, with the listing's file number)
create table filings (
  accession_no      text not null,
  cik               bigint not null,          -- the company whose submissions list it (issuer, filer or manager)
  form              text not null,
  filed_at          date not null,
  accepted_at       timestamptz,              -- null when the listing's times failed the check (D-057)
  period_of_report  date,
  items_8k          text[],                   -- e.g. '{2.02,9.01}'; null for forms other than 8-K
  primary_doc       text,
  is_xbrl           boolean,
  file_number       text,                     -- this listing's; '005-…' marks the subject of a 13D/13G (D-061)
  discovered_at     timestamptz not null default now(),
  primary key (accession_no, cik)
);
create index filings_by_cik on filings (cik, filed_at desc);

create table watch_state (                    -- D-059: the last submissions file each company's watch processed
  cik         bigint primary key,
  sha256      text not null,
  raw_key     text not null references raw_objects(key),
  checked_at  timestamptz not null,           -- the last poll
  changed_at  timestamptz not null            -- the last poll that found new bytes
);

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

create table company_predecessors (            -- D-068: history filed under an earlier CIK
  cik              bigint not null references companies(cik),
  predecessor_cik  bigint not null references companies(cik),
  source_url       text not null,              -- the SEC filing that shows the succession
  note             text not null,
  primary key (cik, predecessor_cik),
  check (cik <> predecessor_cik)
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
  status             text not null check (status in ('complete','partial','failed','no_statement')),  -- D-071
  extractor          text not null,           -- 'rules' (E7a), 'llm' (E12)
  extractor_version  text not null,
  check_report       jsonb not null,
  verified_at        timestamptz,
  updated_at         timestamptz not null default now()
);
create index earnings_releases_by_acceptance                 -- E5a (0012): the feed (D-097)
  on earnings_releases (accepted_at desc, accession_no desc);

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
  primary key (accession_no, slot),
  check (check_status = 'passed' or (value is null and prior_value is null and change_pct is null)),
  check (cross_check_status not in ('matched','mismatch')       -- E4b (migration 0011, D-092)
         or (cross_check_value is not null and cross_check_accession is not null))
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

-- E5b (migration 0013, D-100 to D-102): the outbox, written in the transaction that changes the
-- data; the body is stored as delivered (json keeps the text)
create table webhook_destinations (
  dest_id               bigserial primary key,
  client_id             bigint not null references clients(client_id),
  url                   text not null check (url ~ '^https?://'),  -- https; http to this machine in dev
  secret_enc            text not null,        -- signing secret, encrypted with WEBHOOK_ENCRYPTION_KEY
  event_types           text[] not null,      -- some of the four §9.11 types
  active                boolean not null default true,
  consecutive_failures  int not null default 0,  -- deliveries whose every attempt failed, in a row
  created_at            timestamptz not null default now(),
  disabled_at           timestamptz,          -- switched off by the operator or after 50 failures
  disabled_reason       text,
  last_test_at          timestamptz,          -- one test event a minute
  check (active = (disabled_at is null))
);

create table webhook_events (
  event_id      uuid primary key,             -- UUIDv7
  type          text not null,                -- earnings.verified, financials.updated, data.changed, filing.event
  subject       text not null,                -- what it is about: accession, change id, cik:digest, accession:cik
  detail        jsonb not null default '{}',  -- what the writer knew (a refresh's metrics and periods)
  client_id     bigint references clients(client_id),   -- null = every subscribed client
  dest_id       bigint references webhook_destinations(dest_id),  -- a test event's one destination
  livemode      boolean not null default true,
  payload       json,                         -- the envelope as delivered; null until published
  created_at    timestamptz not null default now(),
  published_at  timestamptz,
  check ((payload is null) = (published_at is null)),
  check (livemode = (dest_id is null))
);
create unique index webhook_events_once on webhook_events (type, subject) where livemode;

create table webhook_deliveries (
  event_id      uuid not null references webhook_events(event_id),
  dest_id       bigint not null references webhook_destinations(dest_id),
  attempt       int not null,
  status_code   int,
  error         text,                         -- e.g. "answered 503", "refused: ..."
  duration_ms   int not null,
  attempted_at  timestamptz not null default now(),
  primary key (event_id, dest_id, attempt)
);
alter table earnings_releases add column live boolean not null default false;  -- routed when filed
create index value_changes_by_cik on value_changes (cik, change_id);           -- /v1/changes?ticker=

-- E5b (migration 0014, D-105): the golden sets as data, replaced by `marketdata quality accuracy`
create table golden_values (
  golden_id     bigserial primary key,
  kind          text not null check (kind in ('earnings', 'metric')),
  cik           bigint not null,
  accession_no  text,                         -- earnings: the release
  slot          text,
  metric        text,                         -- metrics: the metric, period type and end
  period_type   text,
  period_end    date,
  value         numeric,                      -- as printed, in base units; null when it doesn't apply
  prior_value   numeric,                      -- earnings: the same quarter a year earlier
  status        text not null check (status in ('expected', 'known_difference', 'not_applicable')),
  note          text,                         -- why a known difference differs
  source_url    text not null,                -- the SEC document it was read from
  source_file   text not null,                -- e.g. golden/earnings/aapl.toml
  loaded_at     timestamptz not null default now(),
  unique nulls not distinct (kind, cik, accession_no, slot, metric, period_type, period_end)
);

-- E4a (migrations 0007-0009; checks and frame_facts per D-081, D-084, D-085); E4b: 0010 adds the unit
-- percent (D-089)
create table xbrl_facts (
  id            bigserial primary key,
  cik           bigint not null references companies(cik),
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
create index xbrl_facts_by_accession on xbrl_facts (accession_no);  -- E5a (0012): sources (D-096)

create table financial_metrics (
  cik                bigint not null references companies(cik),
  metric             text not null,
  period_type        text not null check (period_type in ('Q','FY','TTM')),
  fiscal_year        int not null,
  fiscal_period      text not null,           -- Q1..Q4, FY
  period_start       date,
  period_end         date not null,
  value              numeric,                 -- null allowed, with a quality flag
  unit               text not null,           -- USD, shares, USD/shares; percent for margins and growth
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

create table frame_facts (                    -- E4a-S5 (D-085): one concept across every filer
  taxonomy      text not null,
  concept       text not null,                -- EntityPublicFloat, EntityCommonStockSharesOutstanding
  unit          text not null,
  frame         text not null,                -- e.g. CY2025Q2I
  cik           bigint not null,              -- any filer, not only one in companies
  entity_name   text,
  period_start  date,
  period_end    date not null,
  value         numeric not null,
  accession_no  text not null,
  raw_key       text not null references raw_objects(key),
  loaded_at     timestamptz not null default now(),
  primary key (taxonomy, concept, unit, frame, cik)
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
- Schedules 13D and 13G: verified in E3 (D-061). Old codes `SC 13D`, `SC 13D/A`, `SC 13G`, `SC 13G/A` until December 2024; `SCHEDULE 13D`, `SCHEDULE 13D/A`, `SCHEDULE 13G`, `SCHEDULE 13G/A` since. The subject company's submissions list them (with a `005-` file number), and so do the filer's (with none).
- `acceptanceDateTime` in submissions is UTC, but the SEC once served a whole file with every time shifted by the Eastern offset a second time (Apple, 2026-10-24). The watcher checks each file and stores no times from a file that fails (D-057). (E3)
- Form 4: `primaryDocument` in submissions often points at a rendered path like `xslF345X05/form4.xml`. The raw XML is usually the same filename without the `xsl…/` folder. (E8)
- 13F: the information table is a separate XML document in the filing; find it through the filing index (type `INFORMATION TABLE`). (E9)
- `dei:EntityPublicFloat` is reported once a year on the 10-K cover page, measured at the end of the second fiscal quarter; some filers omit it or report it oddly. The universe growth rule (D-035) must handle missing and stale values. (E4a) Verified in E4a (D-085): the frames API serves dei concepts; hundreds of cover pages state the float a thousand or a million times too large (Cabot: 4.4 quadrillion dollars), and some date it a year early (AMD).
- companyfacts doesn't list every XBRL 10-Q (KO, V and NEE lacked their July 2026 10-Qs three months later), gives the CIK as an integer or a string, and includes facts of 8-K, proxy statements and other forms. (E4a)

---

## 9. Domain rules

### 9.1 Universe

- `data/universe.csv` with columns `ticker,note`, maintained by hand (D-050, which resolved OD-3). The starter list is 31 domestic filers chosen for variety (at least a bank, an insurer, a REIT, a January year-end retailer, a September year-end company and a company that reports in thousands), plus 4 ETFs and 4 foreign private issuers. On 2026-10-25 it grew to 74 tickers with 35 companies the operator asked for (D-056): 60 domestic filers, 9 foreign private issuers and 5 funds. One ticker per company is enough; the company's other tickers load with it. The file is checked before anything is fetched: header, canonical tickers, no duplicates, a note on every row.
- **Growth (D-035, D-085):** `marketdata backfill public-float` loads nine quarters of `dei:EntityPublicFloat` and cover-page share counts from the SEC's frames API; `marketdata universe suggest --top N` ranks listed filers by their latest float into a candidate CSV for review, and lists apart universe companies without a value, stale values (over two years) and suspect ones (over 10,000 dollars a share, or 0). Index membership lists are not used, to avoid depending on index providers' data.
- Each universe company is classified on load (D-018, D-052): `fund` if listed in the SEC fund ticker file; otherwise the newest periodic report in its submissions' `filings.recent` decides: 10-K or 10-Q (and amendments, transition reports) `domestic`, 20-F or 40-F `foreign_private_issuer`, N-CSR, N-CSRS or NPORT-P `fund`. A 10-K filer with SIC 6221 (commodity contracts) and no 8-K item 2.02 is an exchange-traded commodity trust or pool: `fund`. With no periodic report, a 6-K makes it `foreign_private_issuer`, else `other`. Nothing is rejected; the loader prints the class and its evidence for each universe company and a count per class. Companies outside the universe are not fetched, so they stay `unknown` unless the fund list makes them `fund`.
- In the MVP, earnings facts and fundamentals are produced for `domestic` companies only. Other classes resolve and return a clear reason, e.g. "fund: fundamentals not applicable".

### 9.2 Security master

- Ticker → CIK from `company_tickers_exchange.json` (and `company_tickers_mf.json` for funds); company details (name, SIC, fiscal year end) from submissions JSON.
- Several tickers can map to one CIK (e.g. two share classes). Each gets its own `securities` row.
- Ticker input is case-insensitive, and `.`, `-` and `/` are treated as the same class separator: `brk.b`, `BRK-B` and `BRK/B` all resolve to the SEC's `BRK-B`.
- When a ticker disappears from the SEC list or moves to another CIK, close the old row (`valid_to`) and open a new one (D-054: dates are the dates of the snapshots; a row opened the same day changes in place; a load refuses lists that would end more than 5% of either list's tickers unless forced).
- Security types come from the ticker's suffix (D-051): `-P` or `-P<letter>` preferred; `-WT`, `-UN`, `-RI`, `-WI` other; a one-letter suffix is a share class of common stock; fund-list entries are `fund`. `data/security_overrides.csv` (`ticker,security_type,source_url`) corrects tickers the rules get wrong, each with the SEC page that shows the type. `adr` is not inferred.
- A ticker listed by both SEC lists under different CIKs keeps the exchange list's entry (D-051).
- Sector information comes from SIC codes only. No GICS (D-017).

### 9.3 Metric catalog

Defined in code (`normalize/metric_catalog.py`), versioned with `CATALOG_VERSION` (D-005; version 1 in E4a, D-082). Concepts are tried in the order listed, per period; all are `us-gaap` unless marked `dei`.

**Core set (D-020, called the core set there):** revenue, operating_income, net_income, eps_diluted, operating_cash_flow, capex, cash, long_term_debt, total_equity, shares_outstanding, plus the computed free_cash_flow, margins and year-over-year growth. Golden tests and API defaults cover the core set first; the other metrics are built in the same pass but have lower priority.

| Metric | Kind | Unit | Concepts in priority order | Notes |
| --- | --- | --- | --- | --- |
| `revenue` | flow | USD | Revenues; RevenuesNetOfInterestExpense; RevenueFromContractWithCustomerExcludingAssessedTax; RevenueFromContractWithCustomerIncludingAssessedTax; SalesRevenueNet | See financials rule in §9.4. RevenuesNetOfInterestExpense is SoFi's total net revenue (D-082) |
| `cost_of_revenue` | flow | USD | CostOfRevenue; CostOfGoodsAndServicesSold; CostOfGoodsSold | |
| `gross_profit` | flow | USD | GrossProfit | Else computed: revenue − cost_of_revenue |
| `rnd_expense` | flow | USD | ResearchAndDevelopmentExpense | |
| `sga_expense` | flow | USD | SellingGeneralAndAdministrativeExpense | |
| `operating_income` | flow | USD | OperatingIncomeLoss | |
| `interest_expense` | flow | USD | InterestExpense; InterestExpenseNonoperating | |
| `pretax_income` | flow | USD | IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest; IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments | |
| `income_tax` | flow | USD | IncomeTaxExpenseBenefit | |
| `net_income` | flow | USD | NetIncomeLoss; NetIncomeLossAvailableToCommonStockholdersBasic; ProfitLoss | The amount available to common stockholders is after preferred dividends: flag `net_income_to_common` (Realty Income, D-082). ProfitLoss includes minority interests: flag `net_income_includes_nci` |
| `eps_basic` | flow | USD/shares | EarningsPerShareBasic | Never derived by subtraction |
| `eps_diluted` | flow | USD/shares | EarningsPerShareDiluted | Never derived by subtraction |
| `shares_diluted_avg` | flow | shares | WeightedAverageNumberOfDilutedSharesOutstanding | Average over the period; never derived by subtraction |
| `depreciation_amortization` | flow | USD | DepreciationDepletionAndAmortization; DepreciationAndAmortization | |
| `operating_cash_flow` | flow | USD | NetCashProvidedByUsedInOperatingActivities | 10-Q values are usually year-to-date |
| `capex` | flow | USD | PaymentsToAcquirePropertyPlantAndEquipment; PaymentsToAcquireProductiveAssets | Year-to-date in 10-Qs. Productive assets: Amazon, NVIDIA, Visa (D-082) |
| `dividends_paid` | flow | USD | PaymentsOfDividends; PaymentsOfDividendsCommonStock | Year-to-date in 10-Qs |
| `buybacks` | flow | USD | PaymentsForRepurchaseOfCommonStock | Year-to-date in 10-Qs |
| `cash` | instant | USD | CashAndCashEquivalentsAtCarryingValue; CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents | Flag `cash_includes_restricted` when the second concept is used |
| `current_assets` | instant | USD | AssetsCurrent | |
| `total_assets` | instant | USD | Assets | |
| `current_liabilities` | instant | USD | LiabilitiesCurrent | |
| `total_liabilities` | instant | USD | Liabilities | Else computed: LiabilitiesAndStockholdersEquity − StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest |
| `long_term_debt` | instant | USD | LongTermDebtNoncurrent; LongTermDebt; LongTermDebtAndCapitalLeaseObligations | The third: Coca-Cola, ExxonMobil, Accenture (D-082) |
| `total_equity` | instant | USD | StockholdersEquity; StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest | |
| `shares_outstanding` | instant | shares | dei:EntityCommonStockSharesOutstanding; CommonStockSharesOutstanding | dei value is as of the cover-page date, not the period end; it is counted at the end of the period its filing reports (D-084). Companies with several share classes have neither (dimensional facts) |

**Computed metrics** (method `computed`): `free_cash_flow` = operating_cash_flow − capex; `gross_margin`, `operating_margin`, `net_margin` = metric / revenue; `revenue_growth_yoy`, `eps_diluted_growth_yoy` = vs the same fiscal period one year earlier. With a price (E10, §9.12), either the latest licensed close or a price the client passes: `market_cap` = price × shares_outstanding; `pe_ttm` = price / TTM eps_diluted (null when TTM EPS ≤ 0).

Catalog version 2 (E4b, D-089) lists the computed metrics in `COMPUTED`. Margins and growth are percentages (unit `percent`) rounded to one decimal, like a release's change (§9.9). Growth compares the period of the same type ending 350 to 380 days earlier; across a change of sign it is null with `sign_change`, and from a zero there is no row. A margin needs positive revenue. A null input gives a null value with the input's flags, and a value carries the flags its inputs took from their concepts. `gross_profit` and `total_liabilities` are computed (method `computed`) only for the periods whose filings tag no value.

### 9.4 Period logic

1. **Duration** = `period_end − period_start + 1` days. Quarter: 80–120. Six months: 165–200. Nine months: 250–290. Year: 350–380 (covers 52/53-week years, and Costco's 12-, 16- and 17-week quarters and 24- and 36-week year-to-date periods; D-083). Anything else is ignored with a debug log.
2. **Restatements:** for each (concept, period_start, period_end), use the fact with the latest `filed_at`; ties go to the latest accession number. All facts stay in `xbrl_facts`. Metrics read facts of the 10-K and 10-Q family only, transition reports included; facts from 8-K, proxy statements and other forms are stored but never used (D-083).
3. **Concept choice:** per metric and company, use the first concept in the priority list that has a fact for the period. **Never mix concepts inside one derivation** (e.g. a nine-month value from one concept and a full-year value from another). On a mismatch, skip the derivation and flag `concept_mismatch`. A derivation reads the first concept in the order that has every part; only when the parts exist under different concepts alone is the quarter null with `concept_mismatch` (E4b, D-087).
4. **Discrete quarters:** use a reported three-month fact when one exists (`method = reported`). Otherwise derive from year-to-date facts: Q2 = 6M − Q1, Q3 = 9M − 6M (`derived_ytd`). This is the normal case for cash-flow metrics in 10-Qs.
5. **Q4:** FY − 9M year-to-date, or FY − (Q1 + Q2 + Q3) when no 9M fact exists (`derived_q4`). Not for per-share or average-share metrics: if Q4 EPS isn't reported, value is null with flag `eps_q4_not_reported` (average shares get the same flag). A reported three-month fact always wins, Q4 included (Accenture, Target; D-087).
6. **Instant metrics:** the value at each quarter end and fiscal year end; no derivation.
7. **Fiscal labels:** `fiscal_year` = the calendar year in which the fiscal year ends (D-008), the year before for a year ending January 1 to 7 (D-074). Quarters are numbered by position after the previous fiscal year end. This can differ from a company's own label (some retailers whose year ends in January call it the previous year). Over history the year ends are the ends of the company's 10-K annual periods and of its transition reports; after the last of them, the nominal year end from submissions, counted from the last real one (FuboTV's move to September; D-083). The API always returns `period_start` and `period_end` so nothing is ambiguous. The same rule labels earnings releases (§9.9), from the nominal year end.
8. **TTM:** flows = sum of the last four discrete quarters, which must be contiguous; any gap gives null with flag `ttm_gap`. Instants = latest quarter-end value. A quarter end has a TTM row once four quarters reach it; a quarter that doesn't apply (a bank's revenue) gives its own flag, not `ttm_gap`. Average shares have no TTM, and TTM EPS is the sum of four quarters' EPS, so it is null wherever a fourth quarter's isn't reported (D-089).
9. **Units:** only USD, shares and USD/shares are used. A company reporting in another currency is flagged `non_usd_reporter` and its money metrics are skipped in the MVP.
10. **Banks** (SIC 6000–6099): revenue and gross profit are null with flag `not_applicable_financials`, as in their releases (D-072, D-084); a bank's net revenue isn't comparable. Insurers, REITs, lenders and holding companies keep the catalog's revenue concepts. Supersedes the SIC 6000–6999 rule.
11. **Predecessors** (D-068, D-088): a company's metrics read its predecessors' facts for the periods its own filings don't report, so a quarter can be derived across a succession (XOM's second quarter of 2026: six months under 2115436, the first quarter under 34088). The company keeps the rows that read one of its own filings; each predecessor's rows are built from its own facts.

### 9.5 Quality checks

Checks write flag codes into `quality_flags` and are returned by the API. **A flag never changes or hides a value.** Every code is described in `normalize/quality.py` and has a test that triggers it (E4b-S3); `marketdata quality report` lists them by company and metric.

| Code | Rule |
| --- | --- |
| `balance_mismatch` | \|Assets − LiabilitiesAndStockholdersEquity\| / Assets > 1%; set on total assets, liabilities and equity (E4b) |
| `quarters_dont_sum` | For flows with all four quarters reported: \|Q1+Q2+Q3+Q4 − FY\| / \|FY\| > 0.5%; per-share and average-share metrics left out (D-090) |
| `outlier_jump` | Revenue, total assets, total equity or shares outstanding whose absolute value changes more than 5× against the previous period of the same type; a zero isn't compared (D-090, D-094) |
| `concept_mismatch` | See §9.4 rule 3 |
| `ttm_gap` | See §9.4 rule 8 |
| `stale_data` | Latest 10-Q or 10-K is older than 120 days: the latest filing among the company's metric rows, decided when read (the quality report, the newest value of a series; D-090) |
| `eps_q4_not_reported` | See §9.4 rule 5 |
| `net_income_includes_nci` | ProfitLoss used for net_income |
| `net_income_to_common` | NetIncomeLossAvailableToCommonStockholdersBasic used for net_income: after preferred dividends (E4a, D-082) |
| `cash_includes_restricted` | The second cash concept used: cash includes restricted cash (E4a, D-082) |
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
| 1.04 | Mine safety shutdown order or pattern of violations | normal |
| 1.05 | Material cybersecurity incident | high |
| 2.01 | Completed an acquisition or sale of assets | high |
| 2.02 | Quarterly or annual results | high |
| 2.03 | Took on a major financial obligation | normal |
| 2.04 | An event made a financial obligation due sooner or larger | high |
| 2.05 | Restructuring or exit costs | normal |
| 2.06 | Material impairment | high |
| 3.01 | Delisting notice or listing-rule issue | high |
| 3.02 | Unregistered sale of shares | normal |
| 3.03 | Changed the rights of shareholders | normal |
| 4.01 | Changed auditor | normal |
| 4.02 | Past financial statements can no longer be relied on | high |
| 5.01 | Change in control of the company | high |
| 5.02 | Director or executive change | normal |
| 5.03 | Changed bylaws or fiscal year | normal |
| 5.04 | Temporary halt on trading in employee benefit plans | normal |
| 5.05 | Changed or waived its code of ethics | normal |
| 5.06 | Changed its shell company status | normal |
| 5.07 | Shareholder vote results | normal |
| 5.08 | Set the deadline for shareholder director nominations | normal |
| 7.01 | Regulation FD disclosure | normal |
| 8.01 | Other events | normal |
| 9.01 | Financial statements and exhibits | hidden (attachment-only) |
| Schedule 13D | An investor crossed 5% and may seek to influence the company | high |
| Schedule 13D amendment | An investor seeking influence changed its stake | normal |
| Schedule 13G | A passive investor holds more than 5% | normal |
| Schedule 13G amendment | A passive 5% holder changed its stake | hidden (too frequent) |

Items 1.04, 2.04, 3.03, 5.01, 5.04, 5.05, 5.06 and 5.08 were added in E3 (D-064); any other 8-K item is returned as "8-K item <code>" (normal), never dropped. Schedule 13D and 13G rows cover both form codes (`SC 13D`, `SCHEDULE 13D` …) and are shown only on the listing of the company the filing is about, whose file number starts with `005-`; the filer's own listing gets no label (D-061). Labels live in `normalize/filing_events.py`.

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
- **Source document:** the press release exhibit found through the filing index (EX-99.1, else EX-99, EX-99.2 …). Use the exhibit that contains the income statement table. Raw HTML stays in the raw store; text with tables as markdown goes to `documents`. A stored document is never fetched again (D-070). An exhibit printed as pictures (a JPG per page, its text in a hidden layer) has no table to read: such a release fails rather than counting as one without a statement (D-071).
- **Slots (M1):** `revenue`, `operating_income`, `net_income`, `eps_diluted` for the current quarter, each with the prior-year quarter from the same table.
- **Table parsing:** HTML tables become a grid (colspan and rowspan expanded; `$` and `%` cells merged with their numbers; parentheses = negative; dashes = nil; footnote markers stripped from labels).
- **Table detection:** the income statement table is the first table whose row labels match a revenue pattern and a net income or EPS pattern, and whose header names a quarter period. A table's own title naming the statement decides over the lines above it; a table whose current quarter ended after the filing, or more than 130 days before it, is not the release's (D-086).
- **Label patterns** live in `extract/earnings_rules.py`, versioned with `RULES_VERSION`. Examples:
  - revenue: "Total revenues", "Revenues", "Net sales", "Total net sales", "Net revenues"
  - operating income: "Operating income", "Income from operations", "Operating income (loss)"
  - net income: "Net income", "Net income attributable to <company>" or "attributed to <company>" (prefer the amount attributable to the company over the amount including noncontrolling interests)
  - diluted EPS: "Diluted", "Diluted earnings per share", "Net income per share — diluted"
  - banks (SIC 6000–6099): the revenue slot is `not_applicable` (D-072); insurers, REITs and lenders keep it
- **Columns:** take the columns whose heading says three months, a quarter ("Q3", "Third Quarter", "4th Qtr.", "3Q26") or 12 to 17 weeks (D-073). The current period is the latest date; the prior-year period is the column one year earlier. Never take nine-month, year-to-date or full-year columns for quarterly slots. In Q4 releases with both quarter and full-year columns, take the quarter columns. Columns that compare years side by side ("2025 2026", then "2025 2026" again) are a run with one period length: when a column that took its length from the label column or the title disagrees with one naming its own, the period headings go to the runs in the order printed (Rivian); a column of a run without a length takes the run's (Accenture) (D-086).
- **Units:** read the scale from the table caption or header ("in millions", "in thousands", "except per share"). Per-share values are not scaled. When neither states one, a parenthesised note in the paragraph right above the table may (D-079). No scale found → the slot fails with flag `scale_unknown`.
- **Overrides:** `data/extraction_overrides.toml` (D-075) may pin a row label or table per CIK when the rules fail, or mark a slot `not_applicable` when the company's releases don't present that line (with what they print instead); each cites the company's SEC filing. Every override is logged in `docs/EDGE_CASES.md` and counted in the hit-rate report.
- **Change:** `change_pct = (value − prior_value) / |prior_value| × 100`, rounded to one decimal. Null when the prior value is missing or zero, or when the sign changes (flag `sign_change`; `direction` becomes `turned_profit` or `turned_loss`).
- **Verification (blocking, in `verify/`, independent of the extractor; D-015):**
  1. The printed cell text for each value appears in the stored document text, and the source quote is a substring of it after whitespace normalization.
  2. The stored value equals the parsed cell multiplied by the scale, exactly.
  3. Sanity: \|change_pct\| ≤ 1000; revenue > 0; when the table shows diluted shares, EPS × diluted shares is within 2% of net income or $0.005 per share, whichever is larger, using net income to common shareholders when printed (D-076).
  4. A slot that fails any check is stored with `check_status = failed` and served as null with its reason. Other slots are still served (`status = partial`). All that apply passed → `complete`; none passed → `failed`. An 8-K whose exhibits have no income line at all → `no_statement` (D-071), left out of the hit rate.
- **Hit-rate report:** `marketdata extract report` runs over the four-quarter backfill (242 filings of 60 companies in E7a) and prints, per slot and per company, passed / failed / not found and how many needed overrides. The hit rate is passed slots over the slots that apply in releases with a statement. The result decided OD-10 (D-080).
- **Golden set:** at least 30 releases hand-checked in `tests/golden/earnings/*.toml` (D-078), each with its SEC URL.
- **Provenance:** every stored value records `method` and `extractor_version`. Press releases stay in the raw store so a later extractor can re-run over the whole history.
- **Amendments and re-runs (D-027):** an 8-K/A that amends an earnings release is processed like the original; changed values are logged with reason `amendment`. Re-running with a new `RULES_VERSION` or an added override logs changed values with reason `correction`.
- **Cross-check (D-028):** each slot starts as `pending` and becomes `matched`, `mismatch` or `not_comparable` when the 10-Q/10-K for the same quarter is loaded (E4b, §9.10).

### 9.10 Proof, cross-checks, accuracy and changes (E4b, E5a, E5b)

- **Proof object.** Every served value carries `proof`:
  - earnings facts: `accession_no`, `document_url`, `source_quote`, `scale_text`, `checks` (the checks passed), `method`, `extractor_version`, and `cross_check` (`status`, `filing_accession_no`, `filing_value`);
  - financial metrics: `accession_no`, `filing_url`, `concept`, `method` (reported, derived_ytd, derived_q4, derived_ttm, computed), `source_accessions`, `calc_version`.
- **Cross-check (D-028).** When a 10-Q or 10-K for the same quarter is loaded, each earnings slot is compared with the XBRL value for the matching metric and period (§9.3, §9.4): equal within 0.5% (EPS: within $0.01) → `matched`; otherwise `mismatch` with flag `release_vs_filing_mismatch`; no comparable value → `not_comparable`. A mismatch never changes the release value; it is shown, investigated and logged in `docs/EDGE_CASES.md`. The quarter is the period end the release states, matched within a week to a filing's; a release that states none takes the latest period a 10-Q or 10-K reports that ended at most 90 days before it and whose fiscal quarter its label names. The XBRL value is the quarter as the first filing reporting it gave it (a Q4 is the 10-K's year less nine months), so a later restatement doesn't count against the release. A filing listed without facts in companyfacts is `not_comparable`; none listed, `pending`. It runs after every facts refresh, every stored release and in `marketdata normalize --all` (E4b, D-092).
- **Accuracy report.** `marketdata quality accuracy` and `GET /v1/status` publish, from live data: golden-set match rate per metric and per slot; cross-check match rate (matched / (matched + mismatch)); extraction pass rate; median and 90th-percentile minutes from SEC acceptance to verified; value changes by reason over the last 90 days. The golden sets are loaded into `golden_values` by the CLI (GOLDEN_DIR) and compared with what is served: a known difference counts as a miss, a value that doesn't apply isn't counted. The minutes count only releases the watcher routed when they were filed. Every mismatch is listed with both values; D-028's tolerance stays (E5b, D-105).
- **Changes (D-027).** Any change to a stored `earnings_facts` or `financial_metrics` value writes a `value_changes` row with reason `correction` (our rules or overrides changed), `restatement` (a newer filing changed a past period) or `amendment` (the company amended the filing we used). Changes are served by `GET /v1/changes` and announced by `data.changed`.
- **Proof pages.** `/proof/earnings/{accession_no}` and `/proof/metrics/{ticker}/{metric}/{period_type}/{period_end}` show the value, its proof, the SEC links, the check results, the cross-check and the change history. In M1 they sit behind HTTP Basic auth with an `api`-scope key over TLS. From E15 they are part of the web UI and follow its access rule (§9.14); every number in the UI links to one. Rendered server-side with Jinja2, they load no script, font or image and are never cached; errors are pages too (E5b, D-104).

### 9.11 Webhooks (E5b, D-019)

- **Envelope:** `{"id", "type", "created", "livemode", "api_version", "data": {"object": …, "proof_url": …}, "disclaimer"}`: `created` is ISO 8601 UTC like the API, `api_version` is `v1`, `proof_url` starts with PUBLIC_BASE_URL (null without it, or for an object with no one page), and numbers keep every digit (D-102).
- **Signing:** header `X-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256 of "<t>.<raw body>">`, keyed with the destination's secret. Receivers compare in constant time and reject timestamps more than 5 minutes old.
- **Delivery:** through the task queue (`webhook_deliver`). Success is any 2xx within 10 seconds. Retries after about 1 minute and about 10 minutes (3 attempts in total), then the delivery is `dead`. 50 consecutive failures disable the destination and alert the operator. HTTPS only; private, loopback and link-local addresses are refused. A failure is a delivery whose every attempt failed; a delivered event starts the count again; the operator switches a destination back on with `webhooks enable` (the alert is an error log until E7c). The host is resolved at every attempt and the connection goes to a checked public address with the name kept for TLS; redirects aren't followed. In dev, `http` and `https` to this machine are allowed for the local demo (D-102).
- **Idempotency:** receivers dedupe on `id`; ordering is not guaranteed.
- **Events (M1):**
  - `earnings.verified`: a new release's facts are verified (complete or partial); the payload is the same release object as the API. Only releases the watcher routed when they were filed, once each (D-101).
  - `financials.updated`: new or changed metric rows for a company after a 10-Q or 10-K. One per facts refresh that added a period or changed a value; it names the filings, metrics and periods, and the rows come from the API (D-101).
  - `data.changed`: a previously served value changed; the payload has the old value, new value, reason and proof. One per `value_changes` row, the same object as a `/v1/changes` item (D-103).
  - `filing.event`: a high-importance label from §9.6, on a filing a watch found (the company and the filing as `/filings` lists it).
- Payloads never contain end-user data. E7b adds `content.*` events (§9.13).
- **Test events:** `livemode: false`, fired from the CLI, at most one per minute per destination. Each carries the newest real object of its type to one destination (D-102).

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

- Base path `/v1`. Auth header: `X-API-Key` with an `api`-scope key. Proof pages use HTTP Basic auth with an `api`-scope key as the password (any user name) until E15; then they follow the UI access rule (§9.14). They are logged and rate-limited like `/v1` (D-104). (`review` scope: E7b.)
- Every successful response uses this envelope:

```json
{
  "data": {},
  "sources": [
    {"accession_no": "<accession>", "form": "10-Q", "filed_at": "<YYYY-MM-DD>",
     "url": "https://www.sec.gov/Archives/edgar/data/<cik>/<accession_nodash>/"}
  ],
  "as_of": "<ISO 8601 UTC timestamp of the newest data used>",
  "quality_flags": [{"code": "<flag>", "metric": "<metric or slot>", "period_end": "<YYYY-MM-DD>",
                     "ticker": "<TICKER>"}],
  "disclaimer": "Information from public filings. Not investment advice."
}
```

- Every value inside `data` carries a `proof` object (§9.10). `sources` are the filings the values came from, each once, newest first; `as_of` is when the newest row used was last written; each flag names its value's metric or slot, period and ticker (E5a, D-096).
- Errors: `{"error": {"code": "<code>", "message": "<human-readable>"}}` with codes `invalid_parameter` (400), `unauthorized` (401), `forbidden` (403, wrong key scope), `not_found` (404), `method_not_allowed` (405), `not_applicable` (422), `rate_limited` (429, with `Retry-After`), `internal` (500). Messages say how to fix the request where possible. A parameter that fails validation is `invalid_parameter`, so 422 always means `not_applicable` (D-096).
- Lists: `limit` (default 20, max 100) and opaque `cursor`; `data.next_cursor` is the next page's, null on the last (D-098).
- Numbers are JSON numbers written from the stored `Decimal` digit for digit, never through a float (D-096); units are always stated next to them.
- Ticker parameters follow §9.2 normalization.

### 10.2 Endpoints

| Endpoint | Epic | Auth | Returns |
| --- | --- | --- | --- |
| `GET /health` | E0 | none | Process and database status |
| `GET /v1/status` | E7c (basic), E5b (accuracy), E11 (full) | none | Freshness, track record, accuracy report, queue depth. E5b: freshness and the accuracy report in the envelope with no sources, an answer reused for a minute (D-105) |
| `GET /v1/coverage?limit=&cursor=` | E5a | api | Covered tickers with class and available data kinds |
| `GET /v1/companies/{ticker}?price=&price_date=` | E5a (E10 adds prices) | api | Snapshot: identity, class, latest period, TTM core metrics, latest release, latest three filings; from E10, latest close, market cap and P/E where licensed, or computed from a `price` the client passes |
| `GET /v1/companies/{ticker}/earnings/latest` | E5a | api | Latest verified release with proof and cross-check status |
| `GET /v1/earnings?since=&ticker=&limit=&cursor=` | E5a | api | Verified releases, newest first |
| `GET /v1/earnings/{accession_no}` | E5a | api | One release |
| `GET /v1/companies/{ticker}/financials?metrics=&period=&limit=` | E5a | api | Metric rows with proof; `period` is Q, FY or TTM; default metrics: the core set (§9.3); `limit` periods per metric, 8 by default, at most 40 (D-098) |
| `GET /v1/companies/{ticker}/filings?form=&since=&limit=&cursor=` | E5a | api | Filings with plain-language events, including 13D/13G labels; a predecessor's included (D-098) |
| `GET /v1/compare?tickers=&metrics=&period=&price=` | E5a (E10 adds `price`) | api | 2–5 companies side by side: each metric's newest value of the period type, TTM by default (D-098) |
| `GET /v1/companies/{ticker}/prices?start=&end=` | E10 | api | End-of-day prices, only if the vendor license includes `api` (§9.12) |
| `GET /v1/changes?since=&ticker=&reason=&limit=&cursor=` | E5b | api | Value changes with old value, new value, reason and proof, newest first; each with the value served now (D-103) |
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
| `S3_ENDPOINT_URL`, `S3_BUCKET`, `S3_REGION`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY` | — | E1 (s3). `S3_REGION` added in E1 (D-046): B2 signs with its region; R2 accepts `auto` |
| `SEC_USER_AGENT` | `marketdata <your email>` | E1. Required by processes that call the SEC (D-043) |
| `SEC_MAX_RPS` | `8` | E1. At most 8 (guardrail 6) |
| `UNIVERSE_FILE` | `data/universe.csv` | E2 |
| `SECURITY_OVERRIDES_FILE` | `data/security_overrides.csv` | E2 (D-051) |
| `WORKER_CONCURRENCY` | `4` | E3. Tasks the worker runs at once (1–32); they share one SEC rate limit |
| `PREDECESSORS_FILE` | `data/predecessors.csv` | E7a (D-068) |
| `EXTRACTION_OVERRIDES_FILE` | `data/extraction_overrides.toml` | E7a (D-075) |
| `ALERT_EMAIL`, `ALERT_TELEGRAM_BOT_TOKEN`, `ALERT_TELEGRAM_CHAT_ID` | — | E7c |
| `SENTRY_DSN` | — | E7c |
| `WEBHOOK_ENCRYPTION_KEY` | — (Fernet key, checked when set) | E5b: `webhooks create` and the worker's deliveries refuse to run without it |
| `PUBLIC_BASE_URL` | `https://<live host>` | E5b (proof links in webhooks) |
| `GOLDEN_DIR` | `tests/golden` | E5b: the golden sets `quality accuracy` loads (D-105) |
| `PRICE_PROVIDER`, `PRICE_API_KEY` | — (OD-4) | E10 |
| `UI_MODE` | `private` or `public` | E15 |
| `MANAGERS_FILE` | `data/managers.csv` | E9 |
| `OPENFIGI_API_KEY` | — | E9 |
| `COMPANY_NAMES_KA_FILE`, `GLOSSARY_KA_FILE` | `data/company_names_ka.csv`, `data/glossary_ka.csv` | E7b (on demand) |
| `LLM_PROVIDER`, `ANTHROPIC_API_KEY`, `LLM_MODEL` | — (OD-2) | E12, E13 only |

**Environments**

- **dev:** PostgreSQL from `docker compose up -d postgres` (18, host port 5433) or a local PostgreSQL 18 (D-037, D-040); API, worker and scheduler run with `pdm run`; raw store is the local folder `./var/raw`.
- **live (from E7c):** one small always-on VM running all services in Docker Compose behind Caddy, so releases that land around midnight Tbilisi time are processed and the track record starts during the Q3 2026 season (OD-1). The web UI runs there in `private` mode until OD-13 is decided.
- **prod (E11):** hardened version of the live setup; raw store in R2 or B2.

**Common commands**

```bash
docker compose up -d postgres
pdm install
pdm run alembic upgrade head
pdm run pytest
pdm run lint
pdm run fmt
pdm run uvicorn marketdata.api.app:app --reload
pdm run marketdata worker
pdm run marketdata scheduler
pdm run marketdata extract report
pdm run marketdata normalize --all
pdm run marketdata quality report
pdm run marketdata quality accuracy
```

---

## 12. Epics and user stories

Each epic lists its goal, dependencies, stories with acceptance criteria (AC), what is out of scope, and a demo that proves the session is done. Story IDs (`E3-S2`) are used in commits and the decision log. Epics appear in build order (D-032).

### E0 — Foundation (session 1)

**Goal:** an empty repo becomes a running skeleton with database, migrations, config, CLI, health endpoint and CI.
**Depends on:** OD-7 (name) — use `marketdata` until decided.

- **E0-S1 Dev environment.** As the operator, I want one-command setup so every session starts from a working environment.
  - AC: `docker compose up -d postgres` starts PostgreSQL 18 (D-040; was 16) with a named volume.
  - AC: `pdm install` installs everything from the committed `pdm.lock` (D-041; was `uv sync`); the Python version pinned in `pyproject.toml` (3.15 since D-049; was 3.12).
  - AC: `.env.example` lists every variable in §11; `README.md` has a quickstart of at most 6 steps.
- **E0-S2 Skeleton and config.** As a developer, I want the §6 layout with typed settings and logging so later epics only add code.
  - AC: package layout matches §6 (empty modules allowed).
  - AC: `pdm run marketdata --help` lists command groups.
  - AC: settings load from env via pydantic-settings; missing required values fail fast with a clear message.
  - AC: structlog prints JSON when `ENV` is `live` or `prod`, readable output when `ENV=dev`.
- **E0-S3 Database and migrations.** As a developer, I want versioned migrations so schema changes are repeatable.
  - AC: Alembic configured; migration `0001` creates `tasks`, `clients`, `api_keys`, `api_requests` (§7).
  - AC: `alembic upgrade head` and `alembic downgrade -1` both work.
  - AC: connection pool in `db/pool.py`; a repository test inserts and reads a task.
- **E0-S4 Health endpoint.** As an API integrator, I want a health check so monitoring can tell if the service is up.
  - AC: `GET /health` returns `{"status":"ok","db":"ok"}` with 200; returns 503 with `"db":"down"` when Postgres is unreachable (tested).
- **E0-S5 CI.** As the operator, I want every push checked automatically so broken code never reaches main.
  - AC: GitHub Actions workflow runs black, isort and flake8 (D-041; was ruff), mypy `--strict`, pytest with a Postgres service; green on main.
- **E0-S6 AI context files and edge-case log.** As the operator, I want the plan and the edge-case log in the repo so every session reads and adds to the same record.
  - AC: `docs/PLAN.md` (this file) and `CLAUDE.md` (Appendix A) committed.
  - AC: `docs/EDGE_CASES.md` committed with its format: date, epic, company and accession, what broke, how it was handled.

**Out of scope:** any SEC access.
**Demo:** `docker compose up -d postgres && pdm run alembic upgrade head && pdm run uvicorn marketdata.api.app:app` then `curl localhost:8000/health`.

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
**Demo:** a CLI command `pdm run marketdata sec get <url>` that fetches through the client, stores the file, and prints the raw key.

### E2 — Security master and universe (session 3)

**Goal:** we know which companies we cover, what kind of filer each is, and can resolve any ticker.
**Depends on:** E1; OD-3 (the starter list of about 30 domestic filers plus a few ETFs and foreign issuers, §9.1).

- **E2-S1 Universe file.** As the operator, I want to control coverage with a simple file so changing it needs no code.
  - AC: `data/universe.csv` with `ticker,note`; the loader validates each ticker against the SEC ticker lists, prints unknown ones, and prints a count per class (§9.1).
- **E2-S2 Companies and securities.** As a developer, I want company and ticker records loaded from the SEC so everything else can join on CIK.
  - AC: migration creates `companies` and `securities` (§7) with `filer_type` and `security_type`.
  - AC: `pdm run marketdata ingest security-master` loads tickers from `company_tickers_exchange.json` and `company_tickers_mf.json`, and details from each universe company's submissions JSON; sets `in_universe` and classifies per §9.1.
  - AC: re-running changes nothing (idempotent); a test proves it.
- **E2-S3 Ticker resolution.** As an API integrator, I want `brk.b`, `BRK-B` and `BRK/B` to mean the same thing so integration is forgiving.
  - AC: `service.companies.resolve_ticker(text)` returns the current security, its class and CIK per §9.2; raises `NotFound` otherwise. (Takes a connection first, `resolve_ticker(conn, text)`, D-055.)
  - AC: unit tests cover case, separators, two share classes of one company, a closed (historic) ticker, an ETF and a foreign issuer.
- **E2-S4 Ticker changes.** As the operator, I want ticker changes handled automatically so old tickers don't break silently.
  - AC: the daily run closes rows for tickers that disappeared or moved CIK and opens new ones; tested with two snapshot fixtures.
- **E2-S5 Capabilities.** As an API integrator, I want a clear answer when a ticker has no fundamentals so my app can say why.
  - AC: `service.companies.capabilities(cik)` returns which data kinds apply (earnings, fundamentals) with a reason; tested for a domestic company, an ETF and a foreign issuer. (Takes a connection first, `capabilities(conn, cik)`, D-055.)

**Out of scope:** CUSIP/FIGI (E9), ETF holdings (E14).
**Demo:** `pdm run marketdata company show <TICKER>` prints CIK, name, class, SIC, fiscal year end, tickers and capabilities.

### E3 — Task queue and filings watcher (session 4)

**Goal:** new filings are detected within 10 minutes and turned into queued work.
**Depends on:** E2.

- **E3-S1 Postgres task queue.** As a developer, I want a simple, reliable queue in the database we already have so we don't run extra infrastructure.
  - AC: `enqueue(kind, key, payload)` deduplicates on `(kind, key)`; re-enqueueing a `done` task requires an explicit `force=True`. (Force also queues a `dead` task again; D-058.)
  - AC: `claim()` uses `FOR UPDATE SKIP LOCKED`, sets `locked_until`; tasks whose lock expired are reclaimable.
  - AC: `fail()` reschedules with backoff; after 5 attempts status becomes `dead`. Task kinds may set their own attempt limit and backoff (webhook delivery uses 3, §9.11).
  - AC: a concurrency test with two claimers shows no task is processed twice.
  - AC: CLI `marketdata tasks list|retry|purge-done`.
- **E3-S2 Worker and registry.** As a developer, I want handlers registered by task kind so new epics only add a handler.
  - AC: `tasks/registry.py` maps kind → async handler; `pdm run marketdata worker` runs handlers with configurable concurrency; graceful shutdown on SIGTERM.
  - AC: handler exceptions are logged with task context and recorded in `last_error`.
- **E3-S3 Scheduler.** As the operator, I want periodic jobs to enqueue work, not do it, so all work goes through one path.
  - AC: `pdm run marketdata scheduler` enqueues `filings_watch` every 10 minutes and `security_master` daily at 06:00 UTC.
- **E3-S4 Filings watcher.** As a user, I want new company events to show up quickly so the information is current.
  - AC: migration creates `filings` (§7).
  - AC: for each universe CIK (and, from E9, each curated manager CIK) the watcher reads submissions JSON, inserts unseen accessions with `items_8k` parsed, and enqueues routed tasks per §4 (only for registered kinds).
  - AC: first run on a fixture inserts that company's recent filings; a second run inserts 0; a fixture with one new filing enqueues exactly the expected task. (A first run routes nothing; D-063.)
- **E3-S5 Filing history backfill.** As the operator, I want five years of filing history so the feed isn't empty on day one.
  - AC: `pdm run marketdata backfill filings --years 5` follows `filings.files` pages. (The command queues one task per universe company and the worker reads the pages; D-063.)
- **E3-S6 Event labels.** As a user, I want 8-K filings and 5% stakes described in plain words so I understand what happened.
  - AC: §9.6 table in code; `service.filings` returns `events: [{item_or_form, label, importance}]` per filing; hidden items are not returned.
  - AC: the actual Schedule 13D/13G form codes and whether issuer submissions list them are verified and recorded in §15; a fixture with a 13D filing yields the activist label. (Recorded as D-061.)

**Out of scope:** handlers for earnings, facts, Form 4, 13F (later epics).
**Demo:** run scheduler and worker for 15 minutes; `pdm run marketdata filings list <TICKER> --limit 10` shows filings with event labels.

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
  - AC: `pdm run marketdata backfill earnings --quarters 4` processes the universe.
  - AC: `pdm run marketdata extract report` prints passed / failed / not found per slot and per company, and override counts.
  - AC: at least 30 releases hand-checked in `tests/golden/earnings/`, all passing or each difference listed in §16.
  - AC: the report's numbers and the OD-10 decision are recorded in §15.

**Out of scope:** serving (E5a), localization (E7b), LLM (E12), guidance, non-GAAP measures, foreign issuers' 6-K releases.
**Demo:** `pdm run marketdata earnings show <TICKER>` prints the latest release's slots with source quotes and check results.

### E7c — Live run and track record (session 6)

**Goal:** the pipeline runs around the clock from the Q3 2026 earnings season, and every release it processes counts toward a measured track record.
**Depends on:** E7a; OD-1 (a small always-on VM).

- **E7c-S1 Always-on deployment.** As the operator, I want the watcher running around the clock so releases that land at midnight Tbilisi time are processed.
  - AC: Docker Compose on the live VM behind Caddy (HTTPS); nightly `pg_dump` to object storage; external uptime check on `/health`; Sentry enabled.
- **E7c-S2 Track-record stats.** As the operator, I want speed and accuracy measured on live data, not only in tests.
  - AC: `pdm run marketdata stats` and a basic `GET /v1/status` report: releases processed, slot pass rate, share of complete releases, median and 90th-percentile minutes from SEC acceptance to verified, overrides used, value changes by reason.
  - AC: stats are kept per week so the record shows a trend.
- **E7c-S3 Alerts.** As the operator, I want to know when a release fails before anyone asks.
  - AC: alerts (email or Telegram) when a universe release fails extraction or verification, or verification takes longer than 60 minutes.

**Out of scope:** the data API (E5a), webhooks and proof pages (E5b).
**Demo:** `pdm run marketdata stats` on the live VM after a week of earnings releases.

### E4a — XBRL facts and reported metrics (session 7)

**Goal:** reported quarterly, annual and balance-sheet values for the metric catalog, with provenance.
**Depends on:** E3.

- **E4a-S1 Facts loader.** As a developer, I want every company's XBRL facts in Postgres so metrics are computed with SQL and Python, not by re-reading JSON.
  - AC: migration creates `xbrl_facts` (§7).
  - AC: `facts_refresh` handler loads `us-gaap` and `dei` facts from companyfacts JSON (raw file stored first); idempotent.
  - AC: `pdm run marketdata backfill facts` loads all universe companies (per-company API, or the nightly bulk ZIP if faster). (Per-company API, with predecessors; D-081.)
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
  - AC: golden tests for 5 companies × 4 metrics (revenue, net_income, eps_diluted, total_assets) × last 8 quarters, hand-checked against press releases (YAML in `tests/golden/` with the release URL per value). (TOML in `tests/golden/metrics/`, D-084.)

- **E4a-S5 Universe growth (D-035).** As the operator, I want a rule-based way to grow coverage without licensed index lists.
  - AC: `pdm run marketdata universe suggest --top N` ranks domestic filers by their latest `dei:EntityPublicFloat` (from the nightly bulk companyfacts file) and writes a candidate CSV for review; filers with a missing or stale value are listed separately. (From the frames API, with a share-count check for suspect values; D-085.)

**Out of scope:** YTD differencing, Q4, TTM, computed metrics, quality checks (E4b).
**Demo:** `pdm run marketdata metrics show <TICKER> --metric revenue --period Q` prints values with their source accession numbers.

### E4b — Derived metrics, quality checks, cross-checks (session 8)

**Goal:** complete quarterly series, TTM values, ratios and quality flags, and a cross-check of every earnings release against the later filing.
**Depends on:** E4a, E7a.

- **E4b-S1 Derived quarters.** As a user, I want quarterly cash flow even when filings only report year-to-date so every quarter is comparable.
  - AC: §9.4 rules 3–5 implemented (YTD differencing, Q4, no mixed concepts, no derived EPS or average shares). (The first concept with every part, D-087; predecessors fill the periods a company's own filings don't report, D-088.)
  - AC: unit tests with synthetic facts for each rule; golden tests for `operating_cash_flow` and `capex` quarterly for 5 companies.
- **E4b-S2 TTM and computed metrics.** As a user, I want trailing-twelve-month figures and margins so I can compare companies with different year-ends.
  - AC: §9.4 rule 8 and the computed metrics in §9.3 (except price-based ones); tests for gaps and contiguity. (Catalog version 2, unit percent; D-089.)
- **E4b-S3 Quality checks.** As the operator, I want suspicious values flagged so I can review them before a user sees a wrong number.
  - AC: every §9.5 check implemented, each with a unit test that triggers it.
  - AC: `pdm run marketdata quality report` lists flags by company and metric. (D-090.)
- **E4b-S4 Golden set of 10.** As the operator, I want accuracy measured across sectors so we know where the rules break.
  - AC: golden set covers 10 companies including a bank, a January year-end retailer and a September year-end company; core-set metrics first (D-020).
  - AC: all golden tests pass, or each known difference is listed in §16 with its reason. (D-093.)
- **E4b-S5 Recompute.** As the operator, I want to rebuild all metrics from stored facts so rule changes apply to history.
  - AC: `pdm run marketdata normalize --all` rebuilds `financial_metrics` without any SEC call; prints duration; `calc_version` updated. (A command that also cross-checks the releases; D-091.)
- **E4b-S6 Release-vs-filing cross-check (D-028).** As a data evaluator, I want each earnings-release number compared with the audited filing so I can see it was right.
  - AC: when a 10-Q or 10-K for the same quarter is loaded, each `earnings_facts` slot gets `cross_check_status`, `cross_check_value` and `cross_check_accession` per §9.10; mismatches flag `release_vs_filing_mismatch` and appear in the quality report.
  - AC: fixtures for a matching quarter, a quarter whose numbers changed between release and filing, and a slot with no comparable value. (D-092.)

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
  - AC: migrations create `webhook_destinations`, `webhook_events`, `webhook_deliveries`; CLI `marketdata webhooks create|list|disable|test` (the secret is shown once and stored encrypted). (And `enable`; an outbox written where the data changes; D-100 to D-102.)
  - AC: events, envelope, signing, delivery, retries and auto-disable per §9.11; a reference receiver in the tests verifies signatures; the retry schedule is tested with a mocked transport.
- **E5b-S2 Change feed.** As an API integrator, I want every change to data I already have, with the reason.
  - AC: `GET /v1/changes` per §10.2 serves `value_changes`; each new change emits `data.changed`; tested for each reason. (D-103.)
- **E5b-S3 Proof pages.** As a data evaluator, I want to open any number and see where it came from.
  - AC: proof pages per §9.10 for releases and metric values, showing value, proof, SEC links, checks, cross-check and change history; Basic auth with an `api`-scope key. (D-104.)
- **E5b-S4 Accuracy report.** As a data evaluator, I want published accuracy, not promises.
  - AC: `pdm run marketdata quality accuracy` and `GET /v1/status` publish the §9.10 accuracy figures from live data. (Golden values loaded into the database; D-105.)
- **E5b-S5 Demo guide.** As the operator, I want a repeatable demo for any prospective client: product, engineering, and data or compliance people.
  - AC: `docs/DEMO.md` covers: the API in curl or Postman; a webhook received by a test receiver; a proof page for a live number; a release cross-checked against its 10-Q; the accuracy report and live stats; the integration on one page.

**Out of scope:** MCP (E6), web UI (E15), localization (E7b), sales material (outside the repo, §17).
**Demo:** the API, webhook, proof and accuracy parts of `docs/DEMO.md` on the live VM. (Run on the dev database on 2026-10-30; the live VM waits for OD-1.)

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
  - AC: a scheduler job at 22:30 UTC enqueues `prices_eod`; `pdm run marketdata backfill prices --years 2`; missing days and splits are logged in `docs/EDGE_CASES.md`.
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
  - AC: `pdm run marketdata backfill insiders --quarters 8` uses the SEC Insider Transactions datasets for universe issuers.
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
  - AC: `pdm run marketdata backfill funds --quarters 8` from per-filing XML (or the SEC 13F datasets if simpler).
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
  - AC: `pdm run marketdata usage --since <date>` reports calls per key, endpoint or tool, and day from `api_requests`.

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
| OD-4 | Price vendor | End-of-day (or delayed) prices with display rights for our UI and MCP, and optionally API redistribution; compare cost, attribution rules and history depth (D-031) | E10 |
| OD-6 | Georgian number formatting | Decimal and thousands separators, currency and scale words | E7b (on demand) |
| OD-7 | Product and repository name | `marketdata` is a placeholder | E0 |
| OD-8 | Template ownership | The client renders from our data on its side / the client registers templates with us / both (default: both) | E7b (on demand) |
| OD-9 | Review model for rendered content | Manual approval of every item / auto-approve after N clean items (default: manual) | E7b (on demand) |
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
| D-041 | 2026-10-24 | PDM replaces uv for environments, installs and the lockfile (`pdm.lock`); black, isort and flake8 (with flake8-bugbear, flake8-simplify, pep8-naming, flake8-async, flake8-datetimez) replace ruff. `pdm run lint` is the lint gate and `pdm run fmt` formats. `pyproject.toml` keeps standard `[project]` and `[dependency-groups]` metadata, so the tools can change again without touching dependencies. Supersedes the uv and ruff choices in §5 | The endpoint policy on Guram's managed Mac kills uv and ruff, which are signed by OpenAI OpCo, LLC, and the block is company policy that we don't work around. The replacements are mature, MIT-licensed, run on the system Python, and lock and install the same dependencies. flake8-async also caught an unshielded `await` in a `finally` that the earlier checks missed. |
| D-042 | 2026-10-24 | The SEC client and the tests use `httpx2` instead of `httpx`. Resolves the E0 follow-up on Starlette's TestClient | `httpx2` (pydantic's fork, same API, BSD-3) is maintained: 2.16.0 on 2026-10-21, production/stable. `httpx` has had no release since 0.28.1, only 1.0 dev previews. Starlette 1.10's TestClient imports `httpx2` first and warns when it falls back to `httpx` |
| D-043 | 2026-10-24 | `SEC_USER_AGENT` is required only by processes that call the SEC: the worker, `marketdata sec …` and `scripts/fetch_fixture.py` fail at startup without it (`SecClient.from_settings`). The API, Alembic and the tests start without it. A value that is set is always validated (`<app name> <contact email>`), and an empty value in `.env` counts as not set. Refines §8 "the app refuses to start without it" | The API, MCP server and web UI never call the SEC (CLAUDE.md), so requiring the value there would add a setting they don't use, a placeholder in CI and in every test's settings |
| D-044 | 2026-10-24 | SEC client rules: (1) the process-wide token bucket has a burst of one, so requests start at least 1/`SEC_MAX_RPS` apart; (2) `SEC_MAX_RPS` must be above 0 and at most 8; (3) network errors (timeouts, refused or reset connections) are retried like 429 and 5xx, within the same 5 attempts, and a numeric `Retry-After` longer than the backoff is honoured (capped at 60 s); (4) 403 is not retried and raises `SecForbidden`; (5) the client calls only `https://www.sec.gov` and `https://data.sec.gov`, redirects included | (1) A bucket whose burst equals the rate lets 2 × rate requests into one second (16 at 8/s, over the SEC's 10/s); a burst of one keeps every one-second window at or under the rate and still meets the E1-S1 timing AC (40 requests take about 4.9 s). (2) Guardrail 6 enforced in config. (3) Transient network failures are as common as 5xx. (4) The SEC answers 403 to undeclared tools and to rate breaches, and then blocks for a while, so retrying makes it worse. (5) The User-Agent holds a contact email and must not reach other hosts; OpenFIGI (E9) and the price vendor (E10) get their own clients |
| D-045 | 2026-10-24 | `sec/urls.py` builds every SEC row of the §8 endpoint table and `parse()` maps a URL back to its endpoint. For the insider, 13F and N-PORT data sets it builds the landing pages plus `www_path()` for the ZIP links found there; the ZIP name patterns are verified in E8, E9 and E14. OpenFIGI is a constant marked as not for `SecClient`; the price vendor row has no URL until OD-4 | Guessing the data-set ZIP names now would put unverified URLs in the code; the SEC has renamed those pages and windows before. OpenFIGI and the vendor are not SEC hosts, so the SEC client refuses them (D-044) |
| D-046 | 2026-10-24 | Raw store details: the interface is async (`put` returns whether it wrote); `LocalRawStore` writes a temporary file and hard-links it into place; `S3RawStore` keeps each object's sha256 in its metadata, writes new keys with `If-None-Match: *`, checks the sha256 on `get`, sends checksums only where the API requires them, and runs boto3 in a thread. It is tested with moto in-process rather than MinIO. New optional setting `S3_REGION` | A link or a conditional put makes write-once hold even with two writers. The metadata sha256 lets a re-run compare without downloading. boto3 1.36 started adding CRC32 checksum headers by default, which some S3-compatible services rejected. moto needs no Docker in CI; the MinIO server is AGPL-3.0. B2 needs its region for request signing |
| D-047 | 2026-10-24 | Fetch and store: `RawFetcher.fetch_and_store(url, key)` lives in `ingest/fetch.py` (it writes to the database, and `sec/` must not); it saves to the raw store, then inserts the `raw_objects` row, keeping the first row for a key (its `fetched_at` is when those bytes were first stored). Key layout beyond E1-S3's two patterns: endpoints without a CIK leave it out (`raw/sec/api/company_tickers_exchange/{fetched_at}.json`); extra path parameters come before the time (`…/companyconcept/{cik}/{taxonomy}/{concept}/`, `…/frames/{taxonomy}/{concept}/{unit}/{period}/`, older submissions pages `…/submissions/{cik}/{page}/`); `fetched_at` is written `20261024T205501.123456Z`; CIKs unpadded. Bulk ZIPs are refused until a streaming fetch exists. Migration `0002` adds checks to the §7 table: `sha256` is 64 lowercase hex digits, `size_bytes >= 0` | The raw file first, so a crash never leaves a row without a file. Raw objects are write-once, so a later fetch of the same bytes changes nothing. The plan's layout didn't cover endpoints without a CIK or with more parameters; microseconds keep two fetches of one endpoint from sharing a key, and no colons keeps keys valid on every file system. Bulk files are a gigabyte or more and `fetch_and_store` holds the body in memory |
| D-048 | 2026-10-24 | E1 fixtures: submissions and companyfacts for AAPL (September year end), GOOGL (December; several share classes on one CIK) and TGT (January retailer, 52/53-week years, names its fiscal year after the year it starts). Submissions files are kept whole (about 150 KB each, unchanged SEC bytes). companyfacts files are trimmed to the §9.3 catalog concepts plus `dei:EntityCommonStockSharesOutstanding` and `dei:EntityPublicFloat` (3.2–3.8 MB down to 0.4–0.9 MB). `tests/fixtures/sec/MANIFEST.json` records each file's URL, fetch time, original size and sha256, trim rule, and saved size and sha256 | Untrimmed submissions keep every column and the whole `filings.recent` window that E3 will parse, at little cost. companyfacts beyond the catalog isn't used before E4a. The manifest gives fixtures the same provenance as served values, and `test_fixtures.py` checks it |
| D-049 | 2026-10-25 | Python 3.15 (3.15.1 on this date), the latest release, for development, CI and deployments: `requires-python = ">=3.15,<3.16"`, `.python-version` 3.15, and black and mypy target 3.15. Moving to a newer Python is a new decision here. Supersedes the version in D-001 (Python stays the language) | Guram wants the latest release. 3.12 has had security fixes only since 2025-04-02; 3.15 (released 2026-10-09) gets bug fixes until 2028-10 and security fixes until 2031-10. Checked before switching: every locked package installs on 3.15 at the same version (85 packages unchanged); every compiled package has a native 3.15 build except PyYAML (compiled from source, dev-only for now) and black (runs as plain Python); `pdm run lint` and all 215 tests pass; GitHub's runners have 3.15.1 |
| D-050 | 2026-10-25 | OD-3 resolved: the starter universe is the 39 tickers in `data/universe.csv` (31 domestic filers, 4 ETFs, 4 foreign private issuers), each with a one-line note saying why it is there. Every ticker, CIK, class and fiscal year end was checked against the SEC's own files fetched through the SEC client on 2026-10-25: both ticker lists, the submissions JSON of each company, and NetIncomeLoss values (companyconcept) for which companies report in thousands. One row per company: its other tickers load with it. The loader checks the file before fetching anything (header `ticker,note`, canonical tickers, no duplicates, a note on every row) and reports every problem with its line number | §9.1's required kinds are covered: bank JPM; insurer TRV (also BRK-B and UNH); REIT O; January year-end retailers TGT, WMT and LULU; September year ends AAPL, V and DIS; reports in thousands O, LULU, ACN and DECK. The other picks bring edge cases found in the data: XOM's ticker moved to a new CIK, Costco's 12- and 16-week quarters, J&J's 0103 year end, Shopify's move from 40-F to 10-K, SpaceX's SPCX collision, GLD's 10-K filings, BABA's missing fiscal year end, JPM's 26,404 filings a year (EDGE_CASES.md) |
| D-051 | 2026-10-25 | Ticker rules for the security master (§9.2): (1) canonical spelling: upper case, `.` and `/` become `-`, surrounding parentheses are dropped; stored tickers and lookups both use it, and every exchange-list ticker is already in that form; (2) entries that still aren't tickers (`NONE.`, an empty fund symbol) are skipped and reported; (3) the security type comes from the suffix: `-P` or `-P<letter>` preferred, `-WT`, `-WT<letter>`, `-UN`, `-RI` and `-WI` other, any other one-letter suffix a share class of common stock (`share_class` holds the letter), any other suffix other, no suffix common; fund-list entries are `fund`; `adr` is never inferred; (4) `data/security_overrides.csv` (`ticker,security_type,source_url`; setting `SECURITY_OVERRIDES_FILE`) corrects tickers the rules get wrong, each with the SEC page that shows the type; an override for a ticker no list has is reported; (5) a ticker listed in both lists under different CIKs keeps the exchange list's entry and the other is reported; the same CIK in both lists gives one `fund` security | The SEC lists carry no security type, and plain tickers are not always common stock: the 10-Q cover pages show GOOGM and GOOGN are depositary shares of Alphabet's mandatory convertible preferred and AMJB and VYLD are JPMorgan ETNs. Hand-kept overrides with a source keep the four known universe cases right without guessing; reading cover-page XBRL automatically is a follow-up (§16). For SPCX the exchange list is the current one: SpaceX listed in 2026, while the fund list still has an old series |
| D-052 | 2026-10-25 | Filer classes (§9.1) are decided by: (1) the SEC fund list; (2) otherwise the newest periodic report in `filings.recent`: the 10-K and 10-Q family (with /A, 10-KT, 10-QT) domestic, 20-F and 40-F (with /A) foreign private issuer, N-CSR, N-CSRS and NPORT-P (with /A) fund; (3) a domestic-form filer with SIC 6221 and no 8-K item 2.02 in its recent filings is a fund; (4) with no periodic report, a 6-K gives foreign private issuer, else other. Only universe companies are classified (each needs its submissions JSON); others stay `unknown` unless the fund list makes them `fund`. Older submissions pages are never read for this | Taking the newest report handles filers that switch: Shopify moved from 40-F and 6-K to 10-K in 2025. `filings.recent` always holds at least a year, so an active filer's annual and quarterly reports are in it (JPMorgan: 26,404 filings in that year). Rule (3) comes from the data: GLD, IAU, IBIT, ProShares Trust II and USO file 10-K and 10-Q with SIC 6221 and never report results, while the operating companies checked (COIN, MSTR, StoneX) have other SIC codes and file item 2.02. Classifying every listed company would need about 8,000 submissions files a day, or the bulk file and a streaming fetch (§16) |
| D-053 | 2026-10-25 | Company names and the E2 schema (§7): `companies.name` is the EDGAR name, from submissions JSON for universe companies and from the exchange list for the rest, and may be null for registrants listed only in `company_tickers_mf.json`. companyfacts' `entityName` is not used. Migration `0003` also checks the shape of `fiscal_year_end` (`MMDD`) and `ticker` (canonical, D-051), requires `valid_to > valid_from`, and allows one current row per ticker (partial unique index). A load writes only rows that changed, so `updated_at` moves only with a value | Resolves the E1 name-source follow-up. The exchange list and submissions agree for all 48 companies compared (`TARGET CORP`); only companyfacts says `Target Corporation`, and E2 doesn't load companyfacts. The fund list has no names, and fetching the submissions of all 1,159 fund registrants daily to fill them isn't worth it: null says honestly that the name isn't loaded. A display name from `dei:EntityRegistrantName` can come with E4a |
| D-054 | 2026-10-25 | Ticker history (§9.2, E2-S4): a load compares the current rows with the snapshot taken on its date (`as_of`). A ticker no list has any more is closed (`valid_to = as_of`); a ticker another CIK lists now is closed and opened again for that CIK; `valid_from` is the date of the first snapshot that showed the pair (the first load gives every row that day). A row opened on `as_of` itself changes in place or is deleted, since `valid_to` must be after `valid_from`. A load refuses, changing nothing, a snapshot older than the stored rows, and one that would end more than 5% of either list's current tickers (at least 50) unless run with `--force`. Companies are never deleted. A universe ticker that no list has keeps its company in the universe, found through its latest stored row, with a warning; a universe ticker that moves is followed to its new CIK, with a warning | Closed rows keep what a ticker meant on any past day, which later epics need for old filings. A cut-short list from the SEC would otherwise end thousands of tickers and reopen them the next day with new dates; unparseable and empty lists are already refused. Judging each list on its own rows stops a cut-short exchange list (10,400 rows) hiding under the fund list's 28,600. Following a moved ticker is right for a reorganisation (XOM's holding company in 2026) and wrong for a reused one (FB now belongs to a ProShares series), so the operator is told to check the file |
| D-055 | 2026-10-25 | Code layout and conventions from E2: pure security-master code in `normalize/tickers.py`, `normalize/universe.py` and `normalize/security_master.py`, with fetching and writing in `ingest/security_master.py`; domain exceptions (`InvalidParameter`, `NotFound` so far) in `service/errors.py`; service functions take an open `AsyncConnection` as their first argument, e.g. `resolve_ticker(conn, text)`. Tests of service functions that read PostgreSQL are integration tests on rows seeded by a security-master load from fixtures; the pure rules beneath them have unit tests | `normalize/` is the package for pure transformations (§6), and the ticker and filer rules are shared by ingest and the service layer. A connection argument lets the API, MCP server and web UI share one pool and lets tests run inside a rolled-back transaction. E2-S3's acceptance criteria name unit tests; the lookup itself needs the database, so its cases (case, separators, two share classes, a closed ticker, an ETF, a foreign issuer) are integration tests in `tests/integration/test_resolve_ticker.py` |
| D-056 | 2026-10-25 | The universe grows from 39 to 74 tickers with 35 companies Guram asked for: AMD, TEAM, AUR, BBAI, CRWV, DKNG, FIG, FUBO, GEVO, HUMA, IBM, INDI, IONQ, KLAR, MRVL, MELI, MBOT, MU, MP, NBIS, SPYI, NFLX, NU, SMR, QNT, RIVN, RBLX, CRM, SKHY, SOFI, SONY, TTWO, TMQ, U and VST. Each was checked against the SEC's files as D-050's list was; seven more he asked for were already in it (GOOGL, AMZN, INTC, MSFT, NVDA, SHOP, UNH). After the live load the classes are 60 domestic filers, 9 foreign private issuers and 5 funds. AUROW (Aurora's warrants) joins `data/security_overrides.csv`. D-050's checks stand, and growth by public float (D-035) still comes with E4a-S5 | Coverage of companies the operator follows, for demos and discovery. The cost grows with it: E3 polls 74 submissions files every 10 minutes (about 440 requests an hour, far under the limit), and E7a's four-quarter backfill grows from about 120 to about 240 releases of domestic filers before the live run. The new companies also bring edge cases: listings in 2025 and 2026 (FIG, KLAR, QNT, SKHY), small caps reporting in thousands, FuboTV's changed fiscal year and Nasdaq's warrant spelling (EDGE_CASES.md) |
| D-057 | 2026-10-25 | `filings.accepted_at` comes from submissions `acceptanceDateTime` (UTC), checked per file (`normalize/filings.py`): a file is not trusted if any acceptance time is more than 5 minutes after it was fetched, or if more than max(3, 5%) of its filings since 2005 (leaving out filings the SEC makes itself, accession numbers 99999999…) were accepted outside 06:00–22:15 Eastern. An untrusted file's listings are stored with a null time, filled by the next file that passes; a stored time never changes, and a later file that disagrees is logged (`accepted_at_differs`). E7a takes an earnings release's own `accepted_at` from the filing index page it fetches anyway and compares. Resolves the E1 follow-up | The four accessions agree with their index pages in every fetch since 2026-10-25. E1's "+8 h" was one bad copy: on 2026-10-24 the SEC served Apple's whole submissions JSON, and its page 001, with every time late by the Eastern offset (4 h in summer, 5 in winter), and corrected it the next day without changing another byte. That file had 653 of its 994 filings outside EDGAR's hours; the other 288 stored files (95 companies) had at most 1. Index pages for every filing would cost about 600,000 requests for a five-year backfill (JPMorgan alone 130,000) |
| D-058 | 2026-10-25 | Task queue rules (E3-S1, S2): a task is unique by (kind, key); enqueueing returns the existing task while it is queued, running or waiting to retry, and a done or dead task runs again only with `force=True` (new payload, fresh attempt count). Keys: `filings_watch` and `filings_backfill` by CIK, enqueued with force; `security_master` by date, without force, so a refused load stays dead and the next day's is a new task. Default retries: 5 attempts, waiting 1, 5, 15 and 60 minutes; kinds may set their own. A claim locks a task for 5 minutes and the worker renews the lock every minute; the attempt count fences a worker whose lock ran out. A handler raises `TaskRefused` when retrying can't help: the task is dead at once and logged as an error (`task_dead`). SIGTERM stops claiming and waits up to 60 seconds; attempts still running go back to the queue without counting. Resolves the E0 follow-up on re-runs | Stable keys keep periodic work to one row per company or day and stop it from piling up when the worker is slow. Waits that grow to an hour ride out an SEC outage without hammering it. Fencing makes a slow worker's late result harmless, and every handler is an idempotent upsert anyway |
| D-059 | 2026-10-25 | The watcher stores a submissions response only when its bytes differ from the file it processed last (`watch_state`, migration 0004); an unchanged file is neither stored nor parsed, and only its check time is recorded. Narrows D-047 ("every response is stored") for polling | Polling the 74 universe companies every 10 minutes fetches 15.1 MB a poll: storing every response would be 2.2 GB a day, about 790 GB a year. Storing changed files only is about 53 GB a year (20,284 versions: one per 10-minute window with an acceptance in the year to 2026-10-24), 50 GB of it JPMorgan (10,756 versions of 4.66 MB). Gzip at rest would make it 5.6 GB, and conditional requests would save bandwidth (§16) |
| D-060 | 2026-10-25 | `filings` (§7) is keyed by (accession_no, cik): one row per filing and listing company, with the listing's `file_number` (migration 0004). `cik` is the company whose submissions list the filing. Resolves the E2 follow-up | 29 accessions in the universe's recent filings (37 after the five-year backfill) are listed under two universe companies, e.g. Disney's 13D on FuboTV, Berkshire's 13G on Apple, Amazon's filings on Rivian, NVIDIA's on CoreWeave. XOM's 10-Q 0000034088-26-000093 is one filing by two co-registrants. Listings of one filing differ only in act, file number and film number, and the file number tells a 13D's subject from its filer (D-061) |
| D-061 | 2026-10-25 | Schedule 13D and 13G (E3-S6): universe submissions use `SC 13D`, `SC 13D/A`, `SC 13G`, `SC 13G/A` (to 2024-12-16) and `SCHEDULE 13D`, `SCHEDULE 13D/A`, `SCHEDULE 13G`, `SCHEDULE 13G/A` (13G/A from 2024-07-08, 13D from 2024-12-18); both sets are labelled. An issuer's submissions list the 13D and 13G filed about it, and the filer's list them too. The subject company's listing has act 34 and a `005-` file number, the filer's has neither (1,969 and 597 listings, no exception), so the §9.6 labels are returned only for a listing whose file number starts with `005-` | The labels describe the filing from the side of the company whose shares were bought; on the filer's page ("An investor crossed 5%…" on Disney's page, about Disney's own stake in FuboTV) they would be wrong. Checked on the stored submissions of all 74 universe companies, 2026-10-25 |
| D-062 | 2026-10-25 | One process calls the SEC at a time: the worker, `marketdata sec get` and `marketdata ingest security-master` hold a PostgreSQL advisory lock (the SEC slot) while they run, and a second refuses to start and names the holder. `scripts/fetch_fixture.py` has no database, so it stays a rule (don't run it beside the worker); its new `--from-raw` option copies a stored response without any request. Resolves the E1-S1 follow-up | The rate limit is a token bucket per process (D-044), so two processes would together exceed 8 requests a second, and a 403 block on an earnings night would cost the track record. A lock on the existing database needs no shared limiter; advisory locks belong to one database, so the tests never block the dev database |
| D-063 | 2026-10-25 | Routing and history (E3-S4, S5; §4): a company's first watch stores its recent filings and routes nothing; history goes through each epic's backfill. Later new filings route `facts_refresh` for the 10-K and 10-Q family (with transition reports) and `earnings_extract` for an 8-K or 8-K/A with item 2.02, both for domestic filers only (§9.1); `form4_parse` for 4 and 4/A only on the issuer's listing (no file number); `f13_parse` only for curated managers (E9). Filings are inserted and their tasks enqueued in one transaction, under a lock per company. `marketdata backfill filings --years N` queues one task per universe company with its start date; the worker watches the company, then reads the older pages back to that date, and pages never route | Without a first-watch baseline, the first poll would enqueue every earnings 8-K in each company's recent window (Apple's goes back to 2015). Forms 3, 4 and 5 are the reverse of 13D/13G: issuer listings have no file number (29,694), a reporting owner's has the issuer's (282; Berkshire 159), so the issuer's listing is the one to parse. The backfill runs in the worker so the SEC is called from one process (D-062) |
| D-064 | 2026-10-25 | §9.6 gains the eight 8-K items it lacked: 1.04 mine safety, 2.04 an obligation made due sooner or larger (high), 3.03 changed rights of shareholders, 5.01 change in control (high), 5.04 trading halt in benefit plans, 5.05 code of ethics, 5.06 shell company status, 5.08 deadline for director nominations. Any other item is returned as "8-K item <code>" (normal), never dropped | They occur in universe filings: 3.03 in 43, 5.01 in 15 (FuboTV's change in control on 2025-10-30), 5.08 in 8, 5.06 in 6, 5.04 in 4, 5.05 in 3, 1.04 and 2.04 once each. Dropping them would hide a change in control |
| D-065 | 2026-10-25 | Scheduler (E3-S3): APScheduler 3.11.3 (MIT; pulls in tzlocal 5.4.5, MIT) in `tasks/scheduler.py` only, with a mypy override because it ships no type hints. The scheduler process only enqueues: `filings_watch` for each universe company every 10 minutes and at start, and `security_master` daily at 06:00 UTC, or at start when started later that day. The `security_master` task wraps E2's load: a refused snapshot (D-054) or a broken universe file is `TaskRefused`; a company whose submissions failed makes the task retry | §5 already named APScheduler; 3.11.3 came out on 2026-06-28 and 4.0 is still an alpha (4.0.0a6, 2025). Enqueueing at start means a restarted scheduler doesn't wait 10 minutes or skip the day's load |
| D-066 | 2026-10-26 | lxml 6.1 parses press releases (E7a-S2, §5): one parse gives the document text and its tables as grids (colspan and rowspan expanded, `$` and `(` merged with the number after them, `)` and `%` with the one before), with `types-lxml` for mypy. EDGAR's SGML wrapper is stripped before parsing | One library for text and tables; BSD-3 with wheels for Python 3.15. selectolax has no table model |
| D-067 | 2026-10-26 | Task priority (E7a-S1; resolves the §16 E3-S1 follow-up): `tasks.priority` (migration `0005`) is 10 for live work routed from the watcher, 0 for periodic work and −10 for backfills; workers claim the highest priority, then the earliest `run_after`, then the oldest task; queueing a task again keeps the higher priority | A four-quarter backfill (242 releases) must never delay a live earnings 8-K in the season |
| D-068 | 2026-10-26 | Predecessor CIKs (E7a-S1; resolves the §16 E2-S4 follow-up for earnings): `data/predecessors.csv` (`cik,predecessor_cik,source_url,note`; setting `PREDECESSORS_FILE`) lists companies whose history is filed under an earlier CIK, with the SEC filing that shows the succession (XOM: 2115436 after 34088, 8-K12B 0001193125-26-291990). The security-master load stores the predecessor's company (classified, out of the universe) and `company_predecessors`; the earnings backfill loads the predecessor's filings first; releases stay under the CIK that filed them, and `earnings show` and amendments look across the group | Three of XOM's last four releases are under 34088, which the watcher doesn't poll. Each release keeps the CIK of its filing, so every proof link stays right |
| D-069 | 2026-10-26 | Earnings backfill (E7a-S5): `marketdata backfill earnings --quarters N` queues one `earnings_backfill` task per domestic universe company for the worker (the only SEC caller, D-062); each queues an `earnings_extract` task, at backfill priority, for every 8-K and 8-K/A with item 2.02 filed in the last 3N months. `--force` runs stored releases again | The shape of the filings backfill (D-063). A forced run is how new rules and overrides reach stored releases |
| D-070 | 2026-10-26 | Filing documents are fetched once (E7a-S1): an index page or exhibit already in the raw store is read from it | Filed documents don't change. Two forced runs of all 242 releases made no SEC request for documents |
| D-071 | 2026-10-26 | Release status `no_statement` (E7a-S4, migration `0006`): an 8-K with item 2.02 whose exhibits have no revenue, net income or EPS line at all (Tesla's deliveries reports, an announcement, a filing with no exhibit). An exhibit printed as pictures may hold a statement the rules can't read, so its release is `failed` (E7a-S5). The hit rate leaves `no_statement` filings out, and the report lists them for a check by hand | Item 2.02 doesn't make an earnings release (11 of 242 filings). Counting those as failures would understate the rules; taking unreadable releases for them would overstate the rules |
| D-072 | 2026-10-26 | Only banks (SIC 6000–6099) lose the revenue slot (§9.9; resolves the §16 E2 follow-up for E7a); insurers, REITs, lenders and holding companies keep it | UnitedHealth (6324), Berkshire (6331), Travelers (6331) and Realty Income (6798) print total revenues; a bank's net revenue isn't comparable. §9.4 rule 10 is E4a's to change |
| D-073 | 2026-10-26 | Quarter columns (E7a-S3; the §9.9 part of the §16 Costco follow-up): a column is the quarter when its heading says three months, a quarter ("Q3", "Third Quarter", "4th Qtr.", "FQ4-26", "3Q26") or 12 to 17 weeks. The current column has the latest date; the prior-year column is 350 to 380 days earlier or, for headings without a day, the same quarter a year earlier. Change, ratio and percentage columns hold no period. A period end comes from the text ("quarter ended …") only when that date falls in the column's fiscal quarter, at most 130 days before the filing; otherwise it stays null | Costco's 12- and 16-week quarters, Micron's 14-week quarter and headings without a day (JPMorgan, lululemon) are all quarters; a guessed date would be wrong proof |
| D-074 | 2026-10-26 | Fiscal labels (resolves the §16 E2 J&J follow-up): D-008 holds, except that a fiscal year ending on January 1 to 7 takes the calendar year before; quarters are numbered by the months since the previous year end, rounded | A 52/53-week year that follows the calendar (J&J's 2026 ends 2027-01-03) would be labelled a year late. Years ending late in January keep D-008's label (Target's year to 2026-01-31 is fiscal 2026) |
| D-075 | 2026-10-26 | Extraction overrides are TOML: `data/extraction_overrides.toml` (setting `EXTRACTION_OVERRIDES_FILE`, read with tomllib). Each `[[override]]` names a CIK and a slot, why, and the company's own SEC filing that shows it (`source_url`), and gives one of `row_label`, `table` (with `doc_name`) or `not_applicable`; `accession_no` limits it to one release. A line a company's releases don't present gets `not_applicable`, saying what they print instead, and the slot is served as not applicable with that reason. The hit-rate report counts the overrides used | No YAML parser at runtime (PyYAML has no 3.15 build; §16 D-049 follow-up); TOML is in the standard library. 16 overrides: eleven companies print no operating income line, Berkshire no revenue or diluted EPS, Shopify and Vistra no EPS |
| D-076 | 2026-10-26 | The EPS sanity check (§9.9 check 3): diluted EPS × diluted shares within 2% of net income or $0.005 per share, whichever is larger; net income available to common shareholders when the table prints it; the share count read at the table's scale or as printed. A miss fails EPS only | Printed EPS is rounded to the cent (10% at $0.05); JPMorgan's EPS is after preferred dividends; BigBear.ai prints whole share counts under "in thousands" |
| D-077 | 2026-10-26 | Reasons for earnings value changes (E7a-S4, D-027): `correction` when a release runs again and a served value changes (detail: "rules version 1 to 2", or that the rules are the same and an override, the text rules or the company's details changed); `amendment` when an 8-K/A for the same company (or its predecessor) and quarter changes a value of the stored release, which stays stored (`entity_key.amends` names it). A slot that newly passes or fails is a change too | Clients see why a number moved. The version-2 run wrote 14 corrections (Travelers' Q4 and Berkshire's net income) |
| D-078 | 2026-10-27 | Golden earnings files are TOML (`tests/golden/earnings/*.toml`, one per company): values as printed with the statement's scale and the exhibit's URL; `differences` lists slots the rules don't serve, each also in §16. `tests/unit/test_golden_earnings.py` runs the rules and checks over the committed fixtures with the production overrides | The overrides' format (D-075); printed values are what a reader checks against the exhibit |
| D-079 | 2026-10-27 | Rules version 2 (E7a-S5): a bullet is never a table's title line, and when neither the title lines nor the headings state a scale, a parenthesised note in the paragraph right above the table may | Travelers' Q4 highlights sat under a bullet mentioning "adjusted book value"; Berkshire states its scale only in the paragraph above its table. Each change has a crafted test and a golden release |
| D-080 | 2026-10-27 | OD-10 decided by its proposed rule: the E7a hit rate is 81.2% (698 of the 860 slots that apply passed, in the 231 releases with a statement), below 90%, so the LLM fallback (E12) is needed. Its first target is the releases printed as pictures: 100 of the 162 missed slots (UnitedHealth, Tesla, NuScale, Roblox, MercadoLibre, Aurora, SpaceX's Q2). On releases whose exhibits have tables the rules pass 91.8% (698 of 760). When E12 is built is open (§16); E7c runs on the rules | `marketdata extract report`, 2026-10-27, rules version 2, over the four-quarter backfill of the 60 domestic companies: 242 filings (complete 186, partial 8, failed 37, no statement 11); revenue 83.0%, operating income 78.6%, net income 82.3%, diluted EPS 80.4%; overrides used on 60 slots. The other misses: NextEra's per-segment tables (20), Trilogy Metals' annual-only tables (12), real changes over 1000% (12), two 8-Ks that aren't results releases (8), Q4 releases without a quarterly statement (7), EPS that doesn't match net income (3). Golden set: 35 releases hand-checked, every served value equal |
| D-081 | 2026-10-28 | Facts loader (E4a-S1): per-company companyfacts (not the bulk ZIP), each response kept in the raw store as served on every refresh; every us-gaap and dei fact, in every unit and from every form, in `xbrl_facts` (migration `0007`, with checks and a reference to companies), values as Decimal, the CIK taken as an integer or a string of digits. `facts_refresh` is routed with force, since it is keyed by company and a finished task otherwise never runs again (D-058); a refresh loads the company and its predecessors (D-068), rebuilds their metrics, and retries while a recent XBRL 10-K or 10-Q of the group, listed less than an hour ago, is missing from the files; an older one is logged (`facts_filing_missing`). `marketdata backfill facts [--ticker]`; `backfill earnings` gains `--ticker` | 60 refreshes took about 30 seconds in the worker; the ZIP is over a gigabyte and `fetch_and_store` can't stream it. XOM's joint Q2 10-Q has its facts under 2115436 only, so both CIKs are refreshed. companyfacts can lag a filing, and a refresh running when the next one is listed would miss it for a quarter. 1,149,093 facts for 61 CIKs, 485 MB with indexes; about 1 GB of files a year |
| D-082 | 2026-10-28 | Metric catalog version 1 (E4a-S2): §9.3 plus `RevenuesNetOfInterestExpense` after `Revenues`, `NetIncomeLossAvailableToCommonStockholdersBasic` before `ProfitLoss` (flag `net_income_to_common`), `PaymentsToAcquireProductiveAssets` for capex and `LongTermDebtAndCapitalLeaseObligations` for long-term debt; flags `net_income_includes_nci` and `cash_includes_restricted` come from the concept used. Concepts are chosen per period | A survey of the 60 companies compared each catalog concept's quarters with E7a's 559 verified release slots: the plan's order gave SoFi's fee income (153.6 million) as revenue instead of total net revenue (1,218.7 million) and Realty Income's net income with minority interests; Amazon, NVIDIA and Visa tag capex as productive assets; Coca-Cola, ExxonMobil and Accenture tag long-term debt with leases. KO's and V's NetIncomeLoss is there (the E2 gap was companyconcept's). 13 companies tag no OperatingIncomeLoss, as their releases print none |
| D-083 | 2026-10-28 | Period rules (E4a-S3): durations quarter 80–120 days, six months 165–200, nine months 250–290, year 350–380; the latest filing wins, ties to the latest accession; metrics read the 10-K and 10-Q family only, transition reports included; fiscal year ends over history are the ends of a company's 10-K annual periods and of its transition reports, then the nominal year end counted from the last real one. `fiscal_quarter` keeps E7a's labels | Costco's 12-, 16- and 17-week quarters and 24- and 36-week year-to-date periods; proxy statements and 8-Ks tag NetIncomeLoss and would otherwise be the latest filing; FuboTV changed its year end without a transition report, Deckers with a 10-QT in 2014 |
| D-084 | 2026-10-28 | Reported metrics (E4a-S4): `financial_metrics` (migration `0008`; a null value needs a flag); rows for three-month quarters and fiscal years, and instants at every quarter end (Q1–Q4) and year end (FY, the same value as Q4), a quarter end being the end of any period the company reports; the cover-page share count sits at the end of its filing's period; banks' revenue and gross profit null with `not_applicable_financials` for every period of their other flows; two quarters ending on the same day give the later filing. A rebuild logs `restatement` when a later filing changes a value and `correction` when the calc version (catalog, then rules: 1.1) changed or the facts did without a later filing; a row without facts is kept. Rows stay under each filer's CIK; the service layer merges predecessors by period, the company winning (`metrics show`). Golden files are TOML in `tests/golden/metrics/` (D-078's format) | Releases and filings agree: all 164 golden values (AAPL, TGT, COST, JPM, XOM; revenue, net income, diluted EPS, total assets; the last 8 quarters) equal their press releases. A value as of the cover date at the quarter end is what §9.3's note implies; keeping each filer's rows keeps every proof link right (D-068) |
| D-085 | 2026-10-28 | Universe growth (E4a-S5; refines D-035 and the S5 AC): the public float comes from the SEC's frames API, not the nightly ZIP: `public_float_load` (`marketdata backfill public-float`) fetches nine quarterly instant frames of `dei:EntityPublicFloat` and of `dei:EntityCommonStockSharesOutstanding` into `frame_facts` (migration `0009`), raw first. `marketdata universe suggest --top N` ranks filers with a current listed common ticker by their latest float; known funds and foreign issuers are left out and other classes marked "not checked"; a company without a value takes its predecessor's; stale values (over two years), suspect ones (over 10,000 dollars a share on the filer's latest cover-page share count, or 0) and universe companies without a value go to a second CSV; floats over 1,000 dollars a share are ranked with a note | 18 requests of about a megabyte instead of a gigabyte. Ranked as given, the first twelve of 500 were scale errors (Cabot's 4.4 quadrillion dollars); with the check 309 are suspect and the top of the list is NVIDIA, Microsoft, Apple, Amazon, Alphabet. A 10-K's own share count is rarely in a frame, so the filer's latest count checks it; a lower cutoff would drop Booking or NVR |
| D-086 | 2026-10-28 | Rules version 3 (E7a fix, from E4a's survey; refines D-073 and §9.9): columns that compare years side by side ("2025 2026", then "2025 2026" again) are a run with one period length. When a column that took its length from the label column or the title disagrees with one of its run naming its own, the headings are out of line with the columns: the period headings, in the order printed, go to the runs in order and the check report says so; with more runs than headings the table has no quarter. A column of a run without a length takes the run's; columns that each name their own period (interleaved quarter and full-year columns) are read as placed. A table's own title naming the statement of operations, income or earnings decides over the lines above it. A table whose latest quarter column ended after the filing, or more than 130 days before it (`RELEASE_LAG`), is not the release's statement, and the check report says why. Net income "attributed to" the company counts like "attributable to" | Rivian's Q2 2026 statement prints "Three Months Ended" in the label column and "Six Months Ended" one column left of its years, so the six months were served as the quarter (revenue 3,039 million instead of 1,658). Meta's statement of income was skipped in all five releases because a sentence about non-GAAP measures and a page number close the page above it; the highlights table read instead put the 2026 column under "2025" in Q1 2026 (dated 2025-03-31, no prior year). Accenture's "Three Months Ended" spans only its 2025 columns (no prior year in Q1 FY26). Gevo prints "Net loss attributed to Gevo, Inc.", so the loss before its minority's share was served. The forced re-run of the 262 stored releases through the worker (one SEC request, for XOM's predecessor's submissions) wrote 16 corrections, every new value equal to the 10-Q's XBRL fact; the hit rate stays 82.5%. The longest real gap from a quarter's end to its release is 61 days |
| D-087 | 2026-10-29 | Derived quarters (E4b-S1, §9.4 rules 3–5; rules version 2): a quarter no three-month fact reports is the difference of two facts with the same start whose difference is a quarter: six months less three and nine less six (`derived_ytd`), the year less nine months (`derived_q4`), else the year less its first three quarters (reported or derived, of the same concept). A derivation reads the first concept in the metric's order that has every part; it is null with `concept_mismatch` only when the parts exist under different concepts alone. A reported three-month fact always wins. Per-share and average-share metrics are never derived: an unreported fourth quarter is null with `eps_q4_not_reported`, average shares included | Rule 3's "first concept that has a fact for the period", read for a derived quarter: reading each part's own first concept would null quarters that one concept can derive. Dev database: 4,513 `derived_ytd` and 8,762 `derived_q4` rows; 102 `concept_mismatch` rows (P&G tags depreciation and Costco dividends under one concept in 10-Qs and another in the 10-K). Every derived quarter of the golden set equals the difference of the printed columns, apart from the rounding listed in §16 |
| D-088 | 2026-10-29 | Predecessors in derivations (E4b-S1, S5; refines D-084): a company's rebuild reads its predecessors' facts for the periods its own facts don't report, and keeps the rows that read one of its own filings, with the value-less rows at a period one of those covers. Each predecessor's rows are still built from its own facts alone, and the service merges them as before | XOM's second quarter of 2026 needs the six months (2115436's joint 10-Q) and the first quarter (34088's 10-Q). Reading every predecessor fact took the catalog's first concepts from the predecessor at periods both report (cash at 2025-06-30: 14,352 million instead of the successor's 15,711), so three of the successor's rows were no longer given (EDGE_CASES.md) |
| D-089 | 2026-10-29 | TTM and computed metrics (E4b-S2; catalog version 2, calc version 2.2): TTM is four contiguous quarters with values (`derived_ttm`), else null with `ttm_gap` and the null quarters' flags, or with a quarter's own flag when it doesn't apply (`not_applicable_financials`, `non_usd_reporter`); a quarter end has a TTM row once four quarters reach it; instants copy the quarter end's row; average shares have none; TTM EPS sums four quarters' EPS. `COMPUTED` in the catalog: free cash flow, gross, operating and net margins, revenue and diluted-EPS growth. Margins and growth are percent to one decimal (unit `percent`, migration `0010`); growth compares the period of the same type ending 350 to 380 days earlier, null with `sign_change` across a change of sign, no row from zero; a margin needs positive revenue; null inputs give null with their flags; concept flags carry over. Gross profit and total liabilities are computed only where not tagged | Percent to one decimal matches the earnings change (§9.9), so a release's change and the metric compare directly. Matching the year before by period end works across 53-week years and FuboTV's changed year end, where fiscal labels don't. TTM EPS is null wherever a fourth quarter's EPS isn't reported (§16). Dev database: 39,040 `derived_ttm` and 35,028 `computed` rows |
| D-090 | 2026-10-29 | Quality checks (E4b-S3, §9.5): `balance_mismatch` on total assets, liabilities and equity; `quarters_dont_sum` leaves out per-share and average-share metrics; `outlier_jump` as written (more than 5 times or under a fifth of the previous period of the same type), a zero not compared, percentages left out; `stale_data` decided when read, from the latest filing among the company's metric rows, in the quality report and on the newest value of a series, never stored. Every code is described in `normalize/quality.py` and has a triggering test (`test_quality_checks.py` names each; `price_not_licensed` comes with E10). `marketdata quality report [--ticker] [--flag]` lists flags by company and metric, the stale companies and the cross-check | A stored `stale_data` would turn wrong as days pass without a rebuild. The facts' filing dates make KO, V and NEE stale, whose July 10-Qs companyfacts lacks; `filings` would call them fresh while their values are six months old. A year's EPS is no sum of its quarters'. Dev database: 15,176 of 178,054 rows flagged; `outlier_jump` 4,311, mostly volatile flows (free cash flow 403 quarters, income tax 370, operating cash flow 321), noisy as written (§16); `quarters_dont_sum` caught P&G's fiscal 2012–2014 revenue (EDGE_CASES.md) |
| D-091 | 2026-10-29 | `marketdata normalize --all` (or `--ticker`, with the company's predecessors) is a command, not a group: it rebuilds each company with stored facts (`ingest/facts.py rebuild_all`) in its own transaction with its value changes, makes no SEC request and needs no SEC settings, prints its duration, then cross-checks the releases | §12 names `normalize --all`, which a Typer group can't take. Dev database: 61 CIKs, 178,054 rows in 6 to 11 s; the rebuild from calc version 1.1 to 2.2 changed no stored value, and a second run writes nothing |
| D-092 | 2026-10-29 | Release-vs-filing cross-check (E4b-S6, D-028, §9.10): a release's quarter is its stated period end, matched within 7 days to a filing's period, or, when it states none, the latest period a 10-Q or 10-K reports that ended at most 90 days before the release and whose fiscal quarter its label names (the release row keeps its null period end). The value is the quarter as the first filing reporting it gave it: the metric rules over the facts filed up to that day (a Q4 is the 10-K's year less nine months). `not_comparable` when either side has no value or the filing is listed without facts in companyfacts; `pending` while none is listed; the filing's value is kept on a not-comparable slot for investigation. A company is checked with its predecessors after every facts refresh, every stored release and in `normalize --all`, and only changed slots are written; migration `0011` checks the columns | The first filing, not the latest, so that a restatement a year later isn't counted against the release. The 11 releases without a stated end (J&J, Berkshire, lululemon) all match where both sides have a value. On the 262 stored releases: 703 matched, 2 mismatched (EDGE_CASES.md), 319 not comparable (205 slots the release doesn't serve, 44 of 8-Ks without a statement, 45 fourth-quarter EPS the 10-K doesn't report, 10 of KO's and V's 10-Qs missing from companyfacts and Visa's EPS per class, 15 failed), 24 pending; match rate 99.7%. Rivian's, Meta's and Gevo's releases (D-086) match their 10-Qs exactly |
| D-093 | 2026-10-29 | Golden set of 10 (E4b-S4): AAPL, TGT, COST, JPM, XOM (E4a's), PG, NVDA, O, TRV, DE, the last 8 quarters and the years between, core set first (D-020), each value read from the release text stored in `documents` and each derived quarter given as the difference of two printed columns. A period's `known_differences` names values the filings give otherwise, or not at all, with the reason; each is in §16, and the test checks that it still differs. The trimmed companyfacts fixtures copied from the raw store count as E4's share of the fixture budget (2 MB) | 658 values equal their releases; 26 known differences: companies' rounding between quarterly and year-to-date columns (NVDA, DE, PG), Costco's year in its 10-K, Realty Income's 2024 net income before preferred dividends, the XOM successor's cash including restricted cash, JPMorgan's long-term debt concept. The fixtures total 7.3 MB |
| D-094 | 2026-10-29 | `outlier_jump` (§9.5) compares only revenue, total assets, total equity and shares outstanding, each with the previous period of the same type; a zero isn't compared. Rules version 3 (calc version 2.3). Supersedes the metric scope in D-090; resolves the §16 E4b-S3 follow-up (Guram chose this option at the E5a kickoff) | On every metric the check flagged 4,311 of 178,054 rows on the dev database (2,748 quarters, 803 years, 760 TTM), mostly flows that swing around zero. Comparing a quarter with the same quarter a year earlier flagged more quarterly rows (3,194), so seasons weren't the cause. On the four metrics where a jump points to a scale error it flags 291 rows (equity 123, shares 75, revenue 54, assets 39). The rebuild changed no stored value |
| D-095 | 2026-10-29 | API access (E5a-S1): a key is `mdk_` and 32 random bytes (URL-safe base64), printed once by `marketdata keys create` and stored as its sha256 in lowercase hex (checked by migration `0012`, with `rate_per_min` above 0). Every /v1 data route depends on the key check through its router, so none can be added without it; `/v1/status` (E7c, E5b) will have a router of its own. No key, an unknown key and a revoked key are 401; a key whose scope isn't `api` is 403; a key that made `rate_per_min` requests in the minute before is 429 with `Retry-After`: the window is the key's `api_requests` rows of the last 60 seconds, those answered 429 left out (index `api_requests_by_key`). Every /v1 request writes one `api_requests` row after the response, on its own connection: the key when it was found (also for a revoked or refused one), the start time, method, path without the query string, status and duration. `clients create` twice changes nothing; `keys revoke` keeps the first revocation time | A random key needs no salt or slow hash; a prefix makes leaked keys easy to find. A router-level dependency documents the header in `/docs`. Counting in the request log works across API processes and restarts without a new table; refused requests left out let a client recover by waiting a minute. The log carries tickers and parameters only (guardrail 2) |
| D-096 | 2026-10-29 | The envelope (E5a-S1, S2): service functions return `Served` (data, sources, `as_of`, quality flags) and the API adds the disclaimer, so the MCP server (E6) can return the same. Sources are the filings behind the values, each once, newest first, with the folder URL of §10.1; form and filing date come from `filings`, or from the facts of filings older than the five-year filings backfill (index `xbrl_facts_by_accession`). `as_of` is the newest `updated_at` among the rows used. A flag names its metric or slot, period end and ticker. Numbers are written from `Decimal` digit for digit by the API's own JSON writer, never through a float, and the schema describes them as numbers (pydantic would say string). A parameter that fails validation is 400 `invalid_parameter` (FastAPI's default is 422, which §10.1 keeps for `not_applicable`); an unknown path is 404 `not_found`, a wrong method 405 `method_not_allowed` (a code §10.1 lacked); a server error is 500 `internal` with no details. Logging is set up when the API server starts, not when the app object is built | 1,214 of the 2,974 filings metric rows cite are older than the filings table. The rows' `updated_at` moves only when a row changes, so `as_of` says when the data last changed. A comparison or a feed holds several companies, so a flag needs its ticker. Building apps in tests reconfigured structlog under loggers other tests capture; three tests failed only in the full run |
| D-097 | 2026-10-29 | Earnings in the API (E5a-S2, §10.2): the release object is the company (`ticker`, `cik`, `company`), `period`, `release` and `facts`, each fact with its value, `check_status`, the reasons a value is null, flags and the §9.10 proof. `earnings/latest` is the company's newest results release whatever its status: a release that failed verification is served with null values and their reasons, `release.status` saying so (Guram's choice at the kickoff). `/v1/earnings` lists complete and partial releases, newest first by acceptance time. A release that states no period end takes the period of the 10-Q or 10-K its values were cross-checked with (`filings.period_of_report`) and that quarter's fiscal labels, with `period_end_source: filing` and the filing's accession number (11 releases: J&J, Berkshire, lululemon); otherwise `period_end_source` is `release`. A predecessor's release belongs to the company now, under its primary ticker; `release.cik` keeps the filer. A release asked for by accession number is served whatever its status. Resolves the §16 E4b-S6 and E7a-S5 follow-ups on releases without a stated period end | Serving only verified releases as the latest would answer 404 for the 8 domestic companies with none (NextEra, Tesla, MercadoLibre, Roblox, Aurora, NuScale, Trilogy Metals, Microbot) and show an older quarter as the latest wherever the newest release failed. The feed matches the `earnings.verified` webhook (E5b). A period end from the filing is the quarter the cross-check matched, not an estimate, and its source is named |
| D-098 | 2026-10-29 | Lists and company data (E5a-S2, S3): lists take `limit` (1 to 100, default 20) and an opaque cursor, URL-safe base64 of the last item's sort key, returned as `data.next_cursor`. Financials: `limit` is periods per metric (1 to 40, default 8), metrics comma-separated, the core set (D-020) by default, rows by metric as asked, each newest first. Snapshot: identity and class, every current ticker, the latest quarter with metrics, the newest TTM value of each core metric (`not_reported` names core metrics no filing reports), the latest release, and the three latest annual, quarterly and current reports (10-K, 10-Q, 8-K, 20-F, 40-F, 6-K and amendments) and 5% stakes about the company: a 13D or 13G only on the listing it is about (D-061), 13G amendments not at all. Compare: 2 to 5 tickers, TTM by default, each metric's newest value per company by ticker, null where none of its filings reports it; one company named twice is refused. Coverage: every current ticker of a universe company, by ticker, with its class and data kinds. Filings and financials merge predecessors (D-084). With no ticker asked for, a company is shown under its primary ticker: common stock first, then the shortest, then the first in order. `price` and `price_date` come with E10 | JPMorgan's newest filings are 424B2 notes (over 26,000 a year) and its own 13G amendments on other companies, so a plain "latest three filings" said nothing about JPMorgan. Keyset cursors keep pages stable while filings arrive. TTM compares companies whose fiscal years end in different months |
| D-099 | 2026-10-29 | Not applicable (E5a-S4): earnings (the latest release and the feed of one company), the snapshot, financials and comparisons answer 422 `not_applicable` with the capability's reason (§9.1) when the data kind doesn't apply: a fund, a foreign private issuer, a company outside the universe or one not classified yet. The message names the ticker and company. Filings, coverage and a release asked for by accession number answer for every class | A client asking about SPY or TSM learns why there is no data rather than getting a 404; coverage lists every class with its reasons; filings apply to all filers |
| D-100 | 2026-10-30 | Webhooks are an interface (E5b-S1): the code that changes the data writes the event (`ingest/announce.py`, through `db/repositories/`) in the same transaction, with the task that publishes it (`webhook_publish`, keyed by the event id); publishing builds the body with the API's own service functions and queues the deliveries. `webhooks/` reaches its tables through `service/`, like `api/`, and the layer test enforces it. Supersedes the §6 line "`ingest/` and `webhooks/` → `db/repositories/`" (Guram's choice at the kickoff) | The `earnings.verified` payload must be the API's release object, which `service/` builds; §4's diagram already drew webhooks beside REST and MCP. An outbox in the change's transaction can neither announce a rolled-back change nor miss a committed one, and needs no polling |
| D-101 | 2026-10-30 | What announces what (E5b-S1): `earnings.verified` once per release (a unique index on type and subject for live events) when a release the watcher routed when it was filed is complete or partial; `earnings_releases.live` keeps that (from the routed task's `live` payload, never cleared), so a backfill or a re-run announces nothing (its changed values go out as `data.changed`). `financials.updated` once per facts refresh that added a period or changed a stored value, naming the company, filings, metrics and periods; a row whose source alone moved doesn't count. `data.changed` once per `value_changes` row, from every path that writes one, `normalize --all` included. `filing.event` per new filing on a routed watch with a high-importance §9.6 label. Publishing runs at priority 10, deliveries at −5: after live work and the watches (0), before backfills (−10) | Announcing backfills would send a year of old releases when a company joins the universe. A summary keeps `financials.updated` small (a first refresh writes thousands of rows) while the API serves the rows with proof; each new 10-Q files the year-ago quarter again, so source-only changes would be noise (EDGE_CASES.md). A slow receiver must never hold up a filing |
| D-102 | 2026-10-30 | Webhook delivery (§9.11, E5b-S1): the envelope adds `data.proof_url` (PUBLIC_BASE_URL and the object's proof page) and the disclaimer; `created` is ISO 8601 UTC and `api_version` `v1`; the body is written once when published (json keeps the text) and every attempt sends those bytes. A secret is `whsec_` and 32 random bytes, printed once, stored as a Fernet token under WEBHOOK_ENCRYPTION_KEY; the HMAC is keyed with the secret as written. Only globally reachable addresses take deliveries; the name is resolved at every attempt and the connection pinned to the checked address, the name kept for TLS; no redirects. In dev only, http and https to this machine are allowed (the local demo; Guram's choice). A failure is a delivery whose three attempts all failed: 50 in a row switch the destination off with an error log, a delivered event resets the count, and `marketdata webhooks enable` switches it on again. A test event carries the newest real object of its type to one destination, at most one a minute (`last_test_at`); the CLI waits up to 20 s for the worker's first attempt | Clients open the proof page from the event. Fixed bytes keep each signature reproducible. Pinning closes the gap between the address check and the connection (DNS rebinding). Counting attempts would switch a destination off after a 20-minute outage in earnings season; counting deliveries tolerates restarts, as the demo's first delivery showed |
| D-103 | 2026-10-30 | The change feed (E5b-S2): `GET /v1/changes` lists `value_changes` newest first by `change_id`, with `limit` and `cursor` (D-098), by company (its predecessors too), reason and time. An item names the company, what changed (an earnings fact's release, slot and field, an 8-K/A's amended release; a metric's period), the old and new values, the reason and detail, and the value served now with its unit, reasons, flags and proof. The `data.changed` object is this item | Newest first like `/v1/earnings`; a client replays by `change_id`. The value served now and its proof let a client check a change without another request, and show when a value changed again |
| D-104 | 2026-10-30 | Proof pages (E5b-S3): rendered from the API's service functions with Jinja2, autoescaped, with no script, font or image (CSP `default-src 'none'`) and `Cache-Control: private, no-store`; HTTP Basic auth with any user name and an `api`-scope key as the password; logged in `api_requests` and counted against the key's rate limit; errors are pages, a 401 with `WWW-Authenticate`; left out of the API reference | A page and the API can't disagree when the same service builds both. Browsers prompt for Basic auth themselves. Pages behind a key must not be kept by browsers or proxies |
| D-105 | 2026-10-30 | The accuracy report (E5b-S4, §9.10): `marketdata quality accuracy` loads the golden files (GOLDEN_DIR, `tests/golden`) into `golden_values` (migration `0014`, replaced as a whole) and reports, as `GET /v1/status` does: the golden-set match rate per slot and metric (a known difference is a miss, a value that doesn't apply isn't counted, a release or company not stored is counted apart); the cross-check match rate with every mismatch; the extraction pass rate; the median and 90th-percentile minutes from acceptance to the first verification over live releases only (interpolated like `percentile_cont`); value changes by entity and reason over 90 days. D-028's 0.5% stays: a mismatch the filing's rounding explains (CoreWeave's 10-K in millions) is listed, not softened; a rounding-aware tolerance waits for XBRL `decimals` (Guram's choice). `/v1/status` has its own router without a key (D-095), the envelope with no sources, and reuses an answer for a minute | The API reads clean tables, not test files. On the dev database the backfill's verifications give a median of 291,089 minutes (202 days), which says nothing about speed (EDGE_CASES.md). A published rate that quietly widens its tolerance isn't a trust signal. The endpoint needs no key, so its work is bounded |
| D-106 | 2026-10-30 | Logged tracebacks name frames and lines but carry no local values, in every environment (`log.py`: the rich formatter in dev and the dict transformer in live and prod, both with `show_locals=False`); the decrypted signing secret also stays in a `SecretStr` inside `deliver()` | Found in the demo: structlog 26.1 shows every frame's locals by default, and a failed delivery logged `deliver()`'s plaintext secret to the dev log (deleted; the demo's destination switched off and its key revoked). Secrets never reach logs (§6) |

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
| 2026-10-24 | E0-S1, S5 | On Guram's managed Mac (Microsoft Defender, Kandji, Carbon Black), every binary signed with Developer ID "OpenAI OpCo, LLC (2DC432GLL2)" is killed at launch (exit 137); Astral's uv and ruff are signed that way. Resolved by D-041 (PDM, black, isort, flake8). If IT later allows these binaries, moving back to uv and ruff is a new decision | — |
| 2026-10-24 | E0-S5 | Done: pushed to `origin` (github.com/guramkashmadze/martketdata; Guram kept the name) and the first CI run on main is green | — |
| 2026-10-24 | E0 | The GitHub repository is public, so the code, this plan and `CLAUDE.md` are readable by anyone. Decide whether it should be private; no secrets are committed | Guram |
| 2026-10-24 | E0 | ruff rules with no flake8 equivalent were dropped in D-041: the `RUF` set and `UP` (pyupgrade). Add `pyupgrade --py315-plus` as a check if old-style syntax starts to appear | Later |
| 2026-10-24 | E0 | `Dockerfile` and the `api`, `worker`, `scheduler` and `caddy` Compose services: E0's compose file has only `postgres` | E7c |
| 2026-10-24 | E0 | `tasks` has `unique (kind, key)`, so a finished task can't be enqueued again by a plain insert (E0's insert returns the existing row). E3 must decide how a re-run works, for example resetting a done row to `queued` | E3 |
| 2026-10-24 | E0 | Starlette warns that using `httpx` with its TestClient is deprecated and suggests `httpx2`. Check `httpx2`'s status and license when E1 picks the SEC HTTP client | E1 |
| 2026-10-24 | E0 | Plan text: D-034 and §9.14 say the UI goes public when "OD-5" allows, but OD-5 was the employment check (D-036); this probably means OD-13 | Next plan revision |
| 2026-10-24 | E1 | Done: the Starlette TestClient warning is gone; the SEC client and the tests use `httpx2` (D-042) | — |
| 2026-10-24 | E1 live check | Choose the source of `filings.accepted_at`: submissions `acceptanceDateTime` is 4 or 8 hours off the index page's Eastern "Accepted" time, depending on the filing (EDGE_CASES.md, four accessions). Test whichever source is chosen against those accessions | E3 |
| 2026-10-24 | E1-S5 | Company name source: submissions says `TARGET CORP`, companyfacts says `Target Corporation`. Also classify Alphabet's extra tickers `GOOGM` and `GOOGN` | E2 |
| 2026-10-24 | E1-S1 | The SEC rate limit is per process (D-007). `marketdata sec get` or `scripts/fetch_fixture.py` running beside a worker can push the platform total above 8/s. Don't fetch by hand during a backfill, or add a limiter shared through PostgreSQL | E3 or E11 |
| 2026-10-24 | E1-S3 | Bulk ZIPs (`companyfacts.zip`, `submissions.zip`; a gigabyte or more) are refused by `fetch_and_store`, which holds the body in memory. Add a streaming fetch and streaming `put` (S3 multipart) if an epic needs them | When needed |
| 2026-10-24 | E1-S2 | Check that the chosen production store (R2 or B2) honours `If-None-Match: *` on PutObject and works with the `when_required` checksum settings | E11 |
| 2026-10-24 | E1-S3 | `raw_objects.fetched_at` is the first fetch of those bytes; there is no "last fetched" time. Add a column (§15) if freshness reports need it | E7c or E11 |
| 2026-10-24 | E1 | Tests that build `Settings` must pass `_env_file=None` (see `make_settings` in the tests); otherwise they read the developer's `.env`, which broke one test once `SEC_USER_AGENT` was set | All epics |
| 2026-10-24 | E1 | httpx2 logs an INFO line ("HTTP Request: GET …") for every request, next to our own `sec_response` debug line. Lower the `httpx2` logger to WARNING in `log.py` if it gets noisy in live runs | E7c |
| 2026-10-24 | E1 | Tooling: `pdm add` rewrites the dependency arrays in `pyproject.toml` and drops their inline comments (restored by hand in E1); check the comments after every `pdm add`. PDM is now installed with Homebrew (2.29.3, on 2026-10-25), and `pdm install --check`, `pdm run lint` and all 215 tests pass with it | Any epic that adds a dependency |
| 2026-10-24 | E1 push | Fixed: the first two CI runs on E1 ([40138306918](https://github.com/guramkashmadze/martketdata/actions/runs/40138306918), [40180710384](https://github.com/guramkashmadze/martketdata/actions/runs/40180710384)) failed in `test_sec_get_help_works_without_settings`. Typer forces coloured help when `GITHUB_ACTIONS` is set, so `--key` was split by escape codes; the test now strips them, and the suite passes with `GITHUB_ACTIONS=true`. Found through the new failure annotations, since job logs need a sign-in. The S3 concurrent-put test was also moved to the local store, because moto checks `If-None-Match` and writes in two steps. That change is still right, but it wasn't the cause. For later CLI tests: check help text with the escape codes stripped | — |
| 2026-10-25 | D-049 | PyYAML 6.0.3 has no Python 3.15 build yet, so PDM compiles it from source. Today it is dev-only (via flake8-async's libcst and moto). E7a needs a YAML parser at runtime for `data/extraction_overrides.yaml`: check for 3.15 builds then, and make sure the E7c Docker image can compile it or use a parser that has them. black also has no compiled 3.15 build and runs as plain Python (same output, a little slower) | E7a, E7c |
| 2026-10-25 | D-049 | Done: Homebrew had installed python@3.15 only as PDM's dependency, so `brew autoremove` could have deleted the interpreter `.venv` uses. Guram ran `brew install python@3.15`, which marks it as installed on request | — |
| 2026-10-25 | D-049 | A new Python comes out every October. Decide each year whether to move (a §15 entry), after checking that the locked packages publish builds for it | Each October |
| 2026-10-25 | E2-S1 | Done: GOOGM and GOOGN (Alphabet's extra tickers from the E1 follow-up) are depositary shares of its 6.25% Series A and B mandatory convertible preferred; `data/security_overrides.csv` types them `preferred` (D-051) | — |
| 2026-10-25 | E2-S1 | Security types from the cover page: every 10-K and 10-Q tags its registered securities (`dei:Security12bTitle`, `dei:TradingSymbol`, `dei:SecurityExchangeName`), which would type every listed security of a universe company without overrides. companyfacts omits these dimensional facts, so this needs the filing's XBRL instance. The cover page also writes JPM's preferreds as `JPM PR D` while the ticker list says `JPM-PD` | E4a or E9 |
| 2026-10-25 | E2-S2 | Done: company name source. `companies.name` is the EDGAR name from submissions and the exchange list (D-053) | — |
| 2026-10-25 | E2-S2 | Classify every listed company, not only the universe: today the other ~9,100 companies stay `unknown` unless the fund list makes them `fund` (D-052). Needs the nightly bulk `submissions.zip` and a streaming fetch (see the E1-S3 follow-up) | Later |
| 2026-10-25 | E2-S2 | JPMorgan's submissions JSON is 4.66 MB (26,404 filings in one year) and Royal Bank of Canada's 0.76 MB. E3's watcher polls each universe company every 10 minutes and stores every response (D-047), about 670 MB a day for JPMorgan alone: consider conditional requests or skipping identical bytes | E3 |
| 2026-10-25 | E2-S4 | Predecessor CIKs: XOM's history (the FY2025 10-K, earlier 10-Qs and earnings 8-Ks) is under Exxon Mobil Corp (34088), not ExxonMobil Holdings Corp (2115436), which took the ticker on 2026-07-01 (8-K12B); DIS's CIK 1744489 was registered in 2018 as TWDC Holdco 613 Corp. E4a's 8-quarter series and E7a's four-quarter backfill must follow the predecessor: record predecessor links (from 8-K12B filings, or by hand) | E4a, E7a |
| 2026-10-25 | E2-S4 | One accession under two CIKs: XOM's Q2 10-Q 0000034088-26-000093 is listed in the submissions of both 34088 and 2115436, but `filings` (§7) has `accession_no` as its key and one `cik` | E3 |
| 2026-10-25 | E2 | J&J's fiscal 2026 ends 2027-01-03 (`fiscalYearEnd` 0103), so D-008 labels it 2027. Decide how a 52/53-week year that ends in early January is labelled, and test it with J&J | E4a |
| 2026-10-25 | E2 | Costco's 12-week quarters and 16-week fourth quarter: its year-to-date facts (168 and 252 days) and a derived Q4 (112 days) fall outside §9.4's duration ranges, and §9.9's quarter columns don't name "12 weeks" or "16 weeks". Widen the ranges or classify by week count, and test with Costco | E4a, E7a |
| 2026-10-25 | E2 | Net income concept per company: KO and V return an empty units object for `NetIncomeLoss`, CAT hasn't used it in a 10-K or 10-Q since 2011, MNST returns 404. §9.3's `NetIncomeLoss; ProfitLoss` order may need more concepts; check every universe company | E4a |
| 2026-10-25 | E2 | §9.4 rule 10 and §9.9 treat SIC 6000–6999 as banks and insurers, which catches UnitedHealth (6324), Berkshire Hathaway (6331) and REITs (6798), whose revenue is meaningful. Name the bank and insurer SIC codes instead | E4a, E7a |
| 2026-10-25 | E2 live run | The first live `marketdata ingest security-master` failed on its first SEC request after the client's five attempts (about 23 s). Its message was lost because that run's stderr was discarded; the next run, an hour later, worked. Transient SEC failures happen: E3's queue retries tasks, and E7c's alerts should cover a security-master load that keeps failing | E3, E7c |
| 2026-10-25 | E2 live run | Likely cause of that failure: on a later live load the first request to www.sec.gov timed out on connect (`sec_retry … error='ConnectTimeout'`) and the retry a second later worked. E3 and E7c should expect connect timeouts on the first request of a run | E3, E7c |
| 2026-10-25 | E2 (D-056) | FuboTV moved its fiscal year end to September after a change in control in 2025: its latest 10-K covers 2024, followed only by 10-Qs. E4a's period logic must handle a changed fiscal year end and its transition period | E4a |
| 2026-10-25 | E3 | Done: how a finished task runs again (E0 follow-up): `force=True` queues a done or dead task again (D-058) | — |
| 2026-10-25 | E3 | Done: the source of `filings.accepted_at` (E1 follow-up): submissions `acceptanceDateTime`, checked per file (D-057) | — |
| 2026-10-25 | E3 | Done: one process calls the SEC at a time (E1-S1 follow-up): the SEC slot lock (D-062) | — |
| 2026-10-25 | E3 | Done: polling volume (E2-S2 follow-up): unchanged submissions files are neither stored nor parsed (D-059); one accession under two CIKs (E2-S4 follow-up): `filings` is keyed by (accession_no, cik) (D-060); transient SEC failures (E2 live run): the queue retries tasks (D-058) | — |
| 2026-10-25 | E3-S4 (D-057) | E7a should take `earnings_releases.accepted_at` from the filing index page's Eastern "Accepted" time (converted with zoneinfo), compare it with `filings.accepted_at`, and log a mismatch as an edge case | E7a |
| 2026-10-25 | E3 (D-059) | Raw store growth: submissions alone are about 53 GB a year (JPMorgan 50 GB). Compress stored API responses (gzip: about 5.6 GB a year), or put the live run's raw store on R2/B2 from the start | E7c, E11 |
| 2026-10-25 | E3 (D-059) | Conditional requests (If-Modified-Since or ETag) would cut polling traffic, about 2.2 GB a day uncompressed; `SecClient` would need request and response headers, and data.sec.gov's 304 support must be checked first | E11 |
| 2026-10-25 | E3-S1 | The queue is first in, first out: a large backfill queued before an earnings 8-K delays it. Add a priority (live work before backfills) before E7a's backfill and the live run share the queue | E7a, E7c |
| 2026-10-25 | E3-S2 | The worker logs one httpx2 INFO line per SEC request (444 an hour from watches alone): lower the `httpx2` logger (see the E1 follow-up) | E7c |
| 2026-10-25 | E3-S6 (D-061) | A company's own stake filings (Disney's 13D on FuboTV, Berkshire's 13G on Apple) have no label on its page: one of their own ("Reported its stake in <company>") needs the subject's name or CIK from the filing's header or `primary_doc.xml` | E5a or later |
| 2026-10-25 | E3-S4 (D-063) | E8: parse Form 4 from the issuer's listing (no file number); a listing with a file number is the company's own trade in another company's shares | E8 |
| 2026-10-25 | E3 | The watcher polls only universe CIKs, so a predecessor's CIK (XOM's 34088) isn't watched; covered by the E2-S4 predecessor follow-up | E4a, E7a |
| 2026-10-27 | E7a | Done: queue priority (E3-S1 follow-up, D-067); predecessor CIKs for earnings (E2-S4 follow-up, D-068; E4a still needs them for facts); J&J's fiscal label (E2 follow-up, D-074); banks only lose revenue in §9.9 (E2 follow-up, D-072; §9.4 rule 10 is E4a's); Costco's weeks in §9.9's quarter columns (D-073; §9.4's durations are E4a's); no YAML parser needed at runtime (D-049 follow-up, D-075); `earnings_releases.accepted_at` from the index page, compared with the listing: equal for all 242 filings (E3-S4 follow-up) | — |
| 2026-10-27 | E7a-S4 (§9.9) | The ±1000% sanity rule fails 12 real values, each in the source at the right scale: Micron's fiscal Q3 and Q4 2026 operating income, net income and EPS, Figma's Q3 2025 operating income, net income and EPS, IonQ's Q3 2025 net income and EPS, Gevo's Q3 2025 revenue. Decide whether to keep failing them or to serve them with a flag when every other check passes | Guram, before E7c |
| 2026-10-27 | E7a-S5 (D-080) | Releases printed as pictures (UnitedHealth, Tesla, NuScale, Roblox, MercadoLibre, Aurora and SpaceX's Q2 2026): 100 of the 162 missed slots. Their text is a hidden layer without table structure: an LLM reading it (with a quote check that works on that layer) or a reader for the layer. Also decide when E12 is built, relative to M1 | E12; Guram |
| 2026-10-27 | E7a-S3 | NextEra prints one statement table per period with segment columns (FPL, NEER, Corporate and Other, NextEra Energy): its 5 releases fail | E12 or a later rules version |
| 2026-10-27 | E7a-S5 | No stated period end: J&J's releases, three of lululemon's and Berkshire's say "third quarter" or "second quarter" without the day, so `period_end` and the fiscal labels are null. E4b's cross-check with the 10-Q could take them from the filing | E4b |
| 2026-10-27 | E7a-S5 | Filings that print no quarterly statement count as failed: Gevo's and Vistra's Q4 releases (full year only, or a summary), Trilogy Metals' (selected annual information), and 8-Ks with item 2.02 that are no results release (FuboTV's shareholder letter, IonQ's acquisition announcement) | E12 |
| 2026-10-27 | E7a-S5 | A `not_applicable` override covers all of a company's releases: if the company starts printing the line, the slot stays not applicable. Review the overrides with the report each season | E7c |
| 2026-10-27 | E7a-S5 | Spot-check about ten golden releases by hand against their exhibits (offered at the E7a pre-flight) | Guram |
| 2026-10-27 | E7a-S1 | None of the 242 filings splits its income statement over two tables; the continuation rule is covered by a crafted test only | E7c (watch) |
| 2026-10-27 | E7a-S5 | `marketdata extract report` counts every stored release; give it `--since` or per-season numbers once the live run adds releases | E7c |
| 2026-10-27 | E7a-S5 | Alert the operator when a live release is `failed` or `partial` (status, flags and notes are in its check report) | E7c |
| 2026-10-27 | E7a | E4a's golden files: TOML like E7a's (D-078) instead of the YAML §12 names | E4a |
| 2026-10-27 | E7a | E7a's commits are local: CI hasn't run on them, and pushing waits for Guram's OK | Guram |
| 2026-10-27 | E7a | Done: E7a pushed to `origin` with Guram's OK; CI on main is green ([run 40541236490](https://github.com/guramkashmadze/martketdata/actions/runs/40541236490), commit `39da1d8`) | — |
| 2026-10-28 | E4a | Done: predecessor CIKs for facts (E2-S4 follow-up: refreshes load them, the service merges them, D-081, D-084); KO's and V's net income concept (E2 follow-up: NetIncomeLoss is in companyfacts, D-082); §9.4 rule 10 names banks only (E2 follow-up, D-084); Costco's durations (E2 follow-up, D-083); FuboTV's changed year end (E2 follow-up, D-083); E4a's golden files in TOML (E7a follow-up, D-084); bulk ZIPs not needed for E4a (E1-S3 follow-up stands for other uses) | — |
| 2026-10-28 | E4a-S1 (survey) | E7a serves wrong values for Rivian's Q2 2026 release (the six months as the quarter, 0001874178-26-000053) and dates Meta's Q1 2026 release a year early with no prior-year values (0001628280-26-028364); Gevo's net loss is read before minority interests. Fix the column choice and the period date, then re-run with `backfill earnings --force` (corrections are logged, D-077) | E7a fix, before E7c |
| 2026-10-28 | E4a-S1 | companyfacts lacks some XBRL 10-Qs for months (KO's and V's of 2026-07-29, NEE's of 2026-07-24 and 2026-10-21): read the filing's own XBRL instance (or its Financial Report) when the API doesn't list it; the same parser would give dimensional facts: shares outstanding per class (META, NKE, BRK, V and nine others have none in companyfacts), segments and the cover-page security types (E2-S1 follow-up) | E4b or later |
| 2026-10-28 | E4a-S4 | E4b's derived Q4 must not replace a reported three-month Q4 (Accenture's 10-K, Target's equity statements; 34 CIKs have one) | E4b |
| 2026-10-28 | E4a-S4 | `marketdata normalize --all` (E4b-S5) should rebuild metrics from stored facts without SEC requests; until then `backfill facts` rebuilds them and stores a new companyfacts file per company | E4b |
| 2026-10-28 | E4a-S3 | When FuboTV files its first 10-K for a September year, its comparatives (Hulu + Live TV's years to September) will sit beside old FuboTV's December year ends, and quarter labels between them may shift. Check its labels after that 10-K (expected December 2026) | E4b |
| 2026-10-28 | E4a-S5 | The public float load reads nine quarters, so a filer whose latest float is older (or, like AMD's fiscal 2025 10-K, dated a year early) is missing rather than stale; 309 suspect floats are listed for review, and those below 10,000 dollars a share (CleanSpark, HIVE, Select Water, Repay: thousandfold errors on low-priced shares) are ranked with a note. Review `var/universe_candidates.csv` before adding companies | Guram |
| 2026-10-28 | E4a | E4a's commits are local: CI hasn't run on them, and pushing waits for Guram's OK | Guram |
| 2026-10-28 | E4a | Done: E4a pushed to `origin` with Guram's OK; CI on main is green ([run 40637015310](https://github.com/guramkashmadze/martketdata/actions/runs/40637015310), commit `f7f20ec`) | — |
| 2026-10-28 | E7a fix (D-086) | Done: the E4a-S1 survey's E7a follow-up. Rivian's Q2 2026 release serves the quarter (revenue 1,658 million, prior year 1,303), Meta's Q1 2026 is dated 2026-03-31 (fiscal 2026 Q1) with its prior year, and Gevo's net loss is the amount attributed to Gevo (Q1 and Q2 2026); Accenture's Q1 FY26 prior year, missing for the same kind of reason, was found on the way. The version-3 re-run wrote 16 corrections | — |
| 2026-10-28 | E7a fix | Heading rows shifted inside the columns are still read as placed: in Meta's Q1 2026 highlights "2025" sits over the 2026 values and "% Change" over the 2025 ones. Only the filing-date check keeps such a table out, so a shift that leaves the date plausible would show only in E4b's cross-check. Aligning heading rows with the year row in `html_tables.py` would cover it | E4b or a later rules version |
| 2026-10-28 | E7a fix | `period_label` holds only the column's own headings, so a period printed in the label column is left out: 11 releases are labelled with a bare year, e.g. Meta's Q1 2026 "2026" (its other releases say "Three Months Ended September 30, 2025"). E5a's `period.label` could add the label column's heading | E5a |
| 2026-10-28 | E7a fix | The E7a fix commit is local: CI hasn't run on it, and pushing waits for Guram's OK | Guram |
| 2026-10-29 | E7a fix | Done: the E7a fix pushed to `origin` with Guram's OK; CI on main is green ([run 40762179787](https://github.com/guramkashmadze/martketdata/actions/runs/40762179787), commit `f423f11`) | — |
| 2026-10-29 | E4b | Done: a derived Q4 never replaces a reported three-month Q4 (E4a-S4 follow-up, D-087); `marketdata normalize --all` rebuilds from stored facts with no SEC request (E4a-S4 follow-up, D-091); releases with no stated period end take it from the filing for the cross-check (E7a-S5 follow-up, D-092); the cross-check found no release value shifted by heading rows (E7a fix follow-up): its two mismatches are a 10-K in millions and a year changed in the 10-K | — |
| 2026-10-29 | E4b-S4 (D-093) | Known differences in the golden set, each checked to still differ: Costco's fiscal 2026 operating cash flow (release 15,825 million, 10-K 15,817; the fourth quarter 4,692 against 4,684); NVIDIA's printed quarters against its year-to-date facts (operating cash flow Q3 FY25 17,629 against 17,627, Q4 FY25 16,628 against 16,629 and free cash flow 15,551 against 15,552, Q3 FY26 23,750 against 23,751, Q4 FY26 36,190 against 36,188; capex Q2 FY26 1,894 against 1,895, Q3 FY26 1,637 against 1,636); Deere's Q4 FY24 revenue (11,143 against 11,144); P&G's Q4 FY26 revenue and operating income (21,203 and 3,949 against 21,204 and 3,950); Realty Income's 2024 net income (Q3 261.8 and the year 847.9 million to common stockholders against `NetIncomeLoss`, before preferred dividends, 269.5 and 860.8); XOM's cash at 2024-12-31 (quarter and year: 23,029 against 23,187) and 2025-06-30 (14,352 against 15,711), the successor tagging cash with restricted cash; JPMorgan's long-term debt (no row: see the next item) | Known |
| 2026-10-29 | E4b-S4 | JPMorgan tags its long-term debt as `LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities` (460,523 million at 2026-06-30) and hasn't used `LongTermDebt` since 2014, so the catalog gives the bank no long-term debt. Survey the universe for the concept, then add it in a new catalog version | Next catalog version |
| 2026-10-29 | E4b-S3 (EDGE_CASES.md) | Concepts are chosen per period (D-082), so a stray fact of the first concept wins a period: P&G's fiscal 2014 10-K tags `Revenues` of 28.4 to 29.4 billion for fiscal 2012 to 2014 while net sales were about 83 billion; flagged `quarters_dont_sum`. §9.4 rule 3 says per metric and company: choose one concept per company (or per filing) and decide what happens when a company changes concepts | Next rules version |
| 2026-10-29 | E4b-S3 (D-090) | `outlier_jump` as written flags 4,311 rows, mostly volatile flows (free cash flow 403 quarters, income tax 370, operating cash flow 321, net income 276) that jump with seasons and around zero, so its scale errors are hard to see. Compare a quarter with the same quarter a year earlier, or keep it to revenue, assets, equity and share counts | Guram, before E5a serves flags |
| 2026-10-29 | E4b-S6 (D-092) | D-028's 0.5% tolerance doesn't allow for a 10-K reported in millions beside 10-Qs in thousands: CoreWeave's fourth-quarter 2025 operating loss is a mismatch of 0.7% that the year's rounding explains. companyfacts has no `decimals`; the filing's XBRL instance has (see the E4a-S1 follow-up on reading instances) | E5b (accuracy report) or later |
| 2026-10-29 | E4b-S2 (D-089) | TTM EPS sums four quarters' EPS, and a fourth quarter's EPS is never derived, so TTM EPS is null at every quarter end for companies whose 10-Ks don't report it; E10's `pe_ttm` needs it. Options: the fiscal year's EPS at year ends only, or TTM net income over average diluted shares (a §15 decision) | E10 |
| 2026-10-29 | E4b-S6 | A release that states no period end keeps a null `period_end` in `earnings_releases`; the cross-check finds the quarter from the filing. E5a could serve that period (with its source) so J&J's, Berkshire's and lululemon's releases carry dates | E5a |
| 2026-10-29 | E4b-S5 | `normalize --all` and a worker's facts refresh can rebuild the same company at once; the upserts are idempotent but a value change could be logged twice. An advisory lock per CIK would serialize them | E11 |
| 2026-10-29 | E4b | Not done: the Planning follow-up to cross-check golden sets with Financial Datasets (its account has no credits), and FuboTV's labels after its first September 10-K (E4a-S3 follow-up: that 10-K is expected in December 2026) | Later; E7c |
| 2026-10-29 | E4b | E4b's commits are local: CI hasn't run on them, and pushing waits for Guram's OK | Guram |
| 2026-10-29 | E4b | Done: E4b pushed to `origin` with Guram's OK; CI on main is green ([run 40822815612](https://github.com/guramkashmadze/martketdata/actions/runs/40822815612), commit `dba6f3a`) | — |
| 2026-10-29 | E5a | Done: `outlier_jump` decided before the API serves flags (E4b-S3 follow-up, D-094); releases without a stated period end are served with the period of the filing they were cross-checked with (E4b-S6 and E7a-S5 follow-ups, D-097) | — |
| 2026-10-29 | E5a (from the E7a fix follow-up) | Not done in E5a: `period.label` with the label column's heading. The API must not parse documents, so it needs the extractor to store the whole label. J&J's four stored labels also carry the amount column's heading ("THIRD QUARTER 2026 Amount"). Both belong in the next E7a rules version | Next E7a rules version |
| 2026-10-29 | E5a (from the E3-S6 follow-up) | Not done: a label for a company's own stake filings (Disney's 13D on FuboTV) needs the subject's name from the filing's header, which isn't stored. Until then the snapshot leaves them out (D-098) and `/filings` lists them without a label | E8 or later |
| 2026-10-29 | E5a-S1 | `api_requests` grows by one row per request with no retention; usage reports and a retention rule (or monthly partitions) come with metering | E11 |
| 2026-10-29 | E5a-S1 | The rate limit counts requests once they are logged, after the response, so concurrent requests can pass the limit by the number in flight; a counter updated before the handler would close that if clients abuse it | E11 |
| 2026-10-29 | E5a | `/v1/status` doesn't exist yet: the key check leaves it out by design (D-095), and E7c (basic) or E5b (accuracy) adds it | E7c, E5b |
| 2026-10-29 | E5a | E6 should call the same service functions: each returns `Served`, whose sources give the MCP tools their SEC links (D-096) | E6 |
| 2026-10-29 | E5a demo | The dev database has the client `demo` with its two demo keys revoked; `marketdata keys create demo` gives a new one | Guram |
| 2026-10-29 | E5a | E5a's commits are local: CI hasn't run on them, and pushing waits for Guram's OK | Guram |
| 2026-10-29 | E5a | Done: E5a pushed to `origin` with Guram's OK; CI on main is green ([run 40930722018](https://github.com/guramkashmadze/martketdata/actions/runs/40930722018), commit `d698656`) | — |
| 2026-10-30 | E5b | Done: `/v1/status` (E5a follow-up, D-105); the accuracy report's tolerance for a 10-K in millions (E4b-S6 follow-up): kept at 0.5% with CoreWeave listed as a mismatch, a rounding-aware tolerance waiting for XBRL `decimals` (the E4a-S1 follow-up on reading instances) | — |
| 2026-10-30 | E5b-S1 | `webhook_events` and `webhook_deliveries` grow with no retention, like `api_requests`: purge published events after a period | E11 |
| 2026-10-30 | E5b-S1 | A destination switched off after 50 failures, and a dead delivery, only log an error: route both to E7c's alerts | E7c |
| 2026-10-30 | E5b-S1 | Signing-secret rotation: a `webhooks rotate` command with both secrets valid for a while (a receiver checks either) | When a client asks |
| 2026-10-30 | E5b-S2 | A rebuild that moves many values sends one `data.changed` per change to every destination (thousands after a calc version that changes values): batch them or cap deliveries if a client asks | E11 |
| 2026-10-30 | E5b-S1 | `earnings_releases.live` comes from the routed task's payload: if a backfill had queued the same accession first, its payload stays and the release isn't announced. Unlikely, since the watcher routes only new filings | E7c (watch) |
| 2026-10-30 | E5b-S1 | Deliveries share the worker's slots with SEC work; with many destinations give them a concurrency cap of their own | E11 |
| 2026-10-30 | E5b | For the live VM: set PUBLIC_BASE_URL and WEBHOOK_ENCRYPTION_KEY, ship `tests/golden` (GOLDEN_DIR) and the proof templates in the image, and put the reference receiver behind HTTPS for the webhook demo | E7c |
| 2026-10-30 | E5b demo | The dev database has destination 1 (client `demo`, the local receiver) switched off with a secret no key reads any more (the demo's encryption key was deleted), and the demo key 3 revoked | Guram |
| 2026-10-30 | E5b | E5b's commits are local: CI hasn't run on them, and pushing waits for Guram's OK | Guram |
| 2026-10-31 | E5b | Done: E5b pushed to `origin` with Guram's OK; CI on main is green ([run 41175252635](https://github.com/guramkashmadze/martketdata/actions/runs/41175252635), commit `e13283e`) | — |

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

Then implement story by story. After each story, run pdm run lint (black, isort, flake8, mypy) and pdm run pytest, and show me the results and how the acceptance criteria are met.
Append every edge case you hit to docs/EDGE_CASES.md as you go.
```

---

## Appendix C — End-of-session checklist

- [ ] Every completed story's acceptance criteria shown (test names or command output).
- [ ] `pdm run lint` (black, isort, flake8, `mypy --strict`) and `pdm run pytest` green.
- [ ] Migrations added and applied; downgrade checked where practical.
- [ ] `.env.example` and §11 updated for any new setting.
- [ ] No LLM calls added outside E12/E13; no licensed data added without a §15 entry.
- [ ] Any change to stored values writes `value_changes` (tested).
- [ ] Edge cases appended to `docs/EDGE_CASES.md`.
- [ ] §2 Status updated; decisions in §15; leftovers and known differences in §16.
- [ ] Commit with message `E<id>: <short summary>`.
- [ ] Final message tells me what the next session should start with and any decision I need to make first.
