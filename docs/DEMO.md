# Demo guide

A repeatable demo for a prospective client (E5b-S5): product people see what the data is and
where it comes from, engineers see the API and webhooks, and data or compliance people see the
proof behind a number, the cross-check against the audited filing and the published accuracy.
Each part takes about five minutes. The outputs below are from the dev database on 2026-10-30,
before the live run (E7c), so no release has been processed live yet.

Information from public filings. Not investment advice.

## Before the demo

Everything runs from the repository with `pdm run`. On the live VM (OD-1) the same commands run
behind Caddy: set `BASE` to its `https://` address instead of the local one.

```bash
docker compose up -d postgres
```

```bash
pdm run alembic upgrade head
```

```bash
pdm run uvicorn marketdata.api.app:app --port 8000
```

In a second terminal, a client and an API key (the key is printed once; keep it for the demo):

```bash
pdm run marketdata clients create demo
```

```bash
pdm run marketdata keys create demo
```

```bash
export BASE=http://127.0.0.1:8000 KEY=mdk_...
```

The dev database needs the history loaded by earlier epics (`backfill filings`, `backfill
earnings`, `backfill facts`); the live VM has it from the live run. `/docs` on the same server is
the API reference, with an example for every route.

## 1. The API in curl (engineers)

Every response has the same envelope: `data`, `sources` (the filings used, with their sec.gov
folders), `as_of`, `quality_flags` and the disclaimer. Numbers are JSON numbers with every digit
of the stored value; each value carries its proof.

Apple's latest earnings release:

```bash
curl -s -H "X-API-Key: $KEY" $BASE/v1/companies/AAPL/earnings/latest | jq '.data | {period, release: .release.accession_no, facts: [.facts[] | {slot, value, unit, quote: .proof.source_quote, cross_check: .proof.cross_check.status}]}'
```

It is the third quarter of fiscal 2026 (ended 2026-06-27), 8-K `0000320193-26-000018`: revenue
109,417,000,000 USD, operating income 35,695,000,000, net income 29,789,000,000, diluted EPS
2.02, each with the row as printed in the press release (`| Total net sales (1) | 109,417 |
94,036 | ...`), the scale words, the checks it passed and its cross-check.

Two years of quarterly financials for the core metrics, with the XBRL concept and filing of each:

```bash
curl -s -H "X-API-Key: $KEY" "$BASE/v1/companies/AAPL/financials?period=Q" | jq '.data.rows[0]'
```

Every change to a value already served, with the reason (D-027):

```bash
curl -s -H "X-API-Key: $KEY" "$BASE/v1/changes?ticker=RIVN" | jq '.data.changes[] | {change_id, slot, old_value, new_value, value, reason, detail}'
```

Rivian's second-quarter 2026 release shows four corrections from rules version 3: version 2 had
read the six-month columns as the quarter (revenue 3,039,000,000 became 1,658,000,000). Nothing
changed silently; a client that stored the old values learns of each change here or by webhook.

What a client should see go wrong:

```bash
curl -s $BASE/v1/companies/AAPL/earnings/latest
```

```bash
curl -s -H "X-API-Key: $KEY" $BASE/v1/companies/SPY/financials
```

The first answers 401 `unauthorized`; the second 422 `not_applicable` ("A fund: fundamentals don't
apply"), never an empty or estimated value. Postman: import `$BASE/openapi.json`.

## 2. A release checked against its 10-Q (data and compliance)

When the 10-Q or 10-K for the quarter is loaded, each release value is compared with its XBRL
value (D-028): equal within 0.5% (EPS within $0.01) is `matched`, otherwise `mismatch`.

```bash
curl -s -H "X-API-Key: $KEY" $BASE/v1/earnings/0000320193-26-000018 | jq '.data.facts[] | {slot, value, filing: .proof.cross_check}'
```

All four of Apple's values match the 10-Q `0000320193-26-000020`. Across the 262 stored
releases, 703 values matched and 2 did not; both are listed with their values:

```bash
pdm run marketdata quality report --flag release_vs_filing_mismatch
```

CoreWeave's fourth-quarter 2025 operating loss differs by 0.7% because its 10-K is in millions
while its 10-Qs are in thousands; indie Semiconductor's 10-K changed the year's numbers a week
after its release (docs/EDGE_CASES.md). A mismatch is shown, never smoothed over.

## 3. The proof page of a number (everyone)

Open in a browser, with any user name and the API key as the password (HTTP Basic auth; in M1
the pages sit behind a key, from E15 they follow the web UI's access rule):

- `$BASE/proof/earnings/0000320193-26-000018`: Apple's release: each value with the row as
  printed, the scale, the checks, the 10-Q's value and the links to the filing and the exhibit
  on sec.gov.
- `$BASE/proof/earnings/0001874178-26-000053`: Rivian's release, with its change history.
- `$BASE/proof/metrics/AAPL/revenue/Q/2026-06-27`: a metric value: the XBRL concept, how it was
  obtained (reported, derived from year-to-date values, summed over four quarters or computed),
  the filings it reads and its history.

Or with curl:

```bash
curl -s -u "demo:$KEY" $BASE/proof/earnings/0000320193-26-000018 -o apple.html
```

The pages load no script, font or image and set no cookie. A value that failed a check is shown
as missing, with why.

## 4. A webhook received by a test receiver (engineers)

Webhooks tell a client when data is ready or changes, so it doesn't poll (§9.11): `earnings.verified`
(a live release is verified), `financials.updated` (a 10-Q or 10-K added or changed metric rows),
`data.changed` (a served value changed) and `filing.event` (a high-importance 8-K item or a
Schedule 13D).

Set `WEBHOOK_ENCRYPTION_KEY` in `.env` (signing secrets are stored encrypted with it):

```bash
pdm run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Add a destination for the client. In dev it may be this machine; live and prod accept only
public `https://` addresses:

```bash
pdm run marketdata webhooks create demo http://127.0.0.1:8765/hook
```

It prints the signing secret once. Start the reference receiver with it, in its own terminal; it
checks each signature as a client should and prints one line per event:

```bash
WEBHOOK_SECRET=whsec_... python3 scripts/webhook_receiver.py --port 8765
```

The worker delivers events (it publishes each one, then sends it to every destination that takes
its type):

```bash
pdm run marketdata worker
```

A test event carries the newest real object of its type with `livemode: false`, one a minute
per destination:

```bash
pdm run marketdata webhooks test 1 --type data.changed
```

On 2026-10-30 the four types were delivered in 3 to 116 ms; the receiver printed:

```
01a18e76-... earnings.verified (test) DECK 0000910521-26-000028 http://127.0.0.1:8000/proof/earnings/0000910521-26-000028
01a18e8d-... data.changed (test) RIVN  http://127.0.0.1:8000/proof/earnings/0001874178-26-000053
```

The first `earnings.verified` attempt had failed (the receiver wasn't listening yet); the queue
tried again about a minute later and it was delivered: three attempts, about 1 and 10 minutes
apart, then the delivery is dead, and 50 failed deliveries in a row switch the destination off.
`pdm run marketdata webhooks list` shows each destination's state and latest attempt.

The body of that `data.changed` event (shortened):

```json
{
  "id": "01a18e8d-02f9-7283-a161-b6c8f68083d5",
  "type": "data.changed",
  "created": "2026-10-30T20:42:48.418302Z",
  "livemode": false,
  "api_version": "v1",
  "data": {
    "object": {
      "change_id": 30, "reason": "correction", "detail": "rules version 2 to 3",
      "entity": "earnings_fact", "ticker": "RIVN", "accession_no": "0001874178-26-000053",
      "slot": "eps_diluted", "field": "value", "period_end": "2026-06-30",
      "old_value": -0.97, "new_value": -0.63, "unit": "USD/shares", "value": -0.63,
      "proof": {"source_quote": "| Net loss per share ... | $ (0.97) | $ (0.63) | ...",
                "checks": ["in_source", "scale", "sanity"],
                "cross_check": {"status": "matched", "filing_accession_no": "0001874178-26-000054",
                                "filing_value": -0.63}}
    },
    "proof_url": "http://127.0.0.1:8000/proof/earnings/0001874178-26-000053"
  },
  "disclaimer": "Information from public filings. Not investment advice."
}
```

Live events need the scheduler too (`pdm run marketdata scheduler`): a watch that finds a new
8-K with item 2.02 announces `filing.event` at once and `earnings.verified` when the release is
verified; a 10-Q announces `financials.updated`; any value change announces `data.changed`.

## 5. The accuracy report and live stats (data and compliance)

```bash
pdm run marketdata quality accuracy
```

On the dev database, 2026-10-30 (shortened):

```
Golden set: 848 hand-checked values from 35 files
  Earnings release slots served exactly: 135 of 138 (97.8%)
  Metric values served exactly: 658 of 684 (96.2%)
  Known differences, each with its reason in the golden files: 29; not applicable: 26
Earnings releases against their 10-Qs and 10-Ks (D-028), slots: 703 matched, 2 mismatch,
  319 not comparable, 24 pending; match rate 99.7%
Extraction (rules version 3): 766 of 928 (82.5%) slots that apply passed every check,
  in 251 releases with a statement
Minutes from SEC acceptance to verified, releases processed live: none yet
Value changes in the last 90 days: earnings values correction 30
```

The golden set is values read by hand from the press releases (tests/golden/); its misses are
listed with their reasons (Micron's changes above 1000%, companies' own rounding between
quarterly and year-to-date columns, JPMorgan's long-term debt concept). The speed figures count
only releases processed live, so they start with the live run (E7c); a backfill's months-late
verification says nothing about speed.

The same report, with how fresh the data is, without a key:

```bash
curl -s $BASE/v1/status | jq '.data | {freshness, golden: .accuracy.golden.earnings, cross_check: .accuracy.cross_check.rate, latency: .accuracy.latency}'
```

## 6. The same data in an AI chat (everyone)

The MCP server at `/mcp` gives AI clients the same data with the same key (E6). Connect Claude
Code to the server started above:

```bash
claude mcp add --transport http marketdata $BASE/mcp --header "X-API-Key: $KEY"
```

Then ask it: "How did Apple's revenue and free cash flow change over the last 8 quarters?" It
calls `get_financials` for both metrics and answers with each quarter's value and the 10-Q or
10-K it came from, with SEC links. `docs/MCP.md` sets up Claude Desktop, Cursor, VS Code and
the MCP Inspector, and lists the tools and example questions, one of them in Georgian.

## The integration on one page

- **Auth.** `X-API-Key: mdk_...` on every `/v1` request (`/v1/status` needs none). The operator
  issues keys (`marketdata keys create`); a key is shown once and stored as its sha256. 401 for
  no, an unknown or a revoked key; 429 with `Retry-After` over the key's requests a minute.
- **Responses.** The envelope `data`, `sources`, `as_of`, `quality_flags`, `disclaimer`. Numbers
  are exact JSON numbers with their unit next to them; read them as decimals (e.g.
  `json.loads(text, parse_float=Decimal)`), not floats. Times are UTC with a `Z`.
- **Proof.** Every value has a `proof`: an earnings value its filing, exhibit URL, the row as
  printed, the scale words, the checks and its cross-check; a metric its filing, XBRL concept,
  method, contributing filings and calc version. Proof pages: `/proof/earnings/{accession_no}`
  and `/proof/metrics/{ticker}/{metric}/{period_type}/{period_end}`.
- **Missing data.** A value that failed a check, or doesn't apply, is `null` with `reasons` or a
  quality flag. Funds and foreign issuers answer 422 `not_applicable` with the reason.
- **Errors.** `{"error": {"code", "message"}}`: `invalid_parameter` 400, `unauthorized` 401,
  `forbidden` 403, `not_found` 404, `method_not_allowed` 405, `not_applicable` 422,
  `rate_limited` 429, `internal` 500.
- **Lists.** `limit` (1 to 100) and the `next_cursor` of the page before, until it is null.
- **Changes.** `GET /v1/changes?since=&ticker=&reason=`, newest first: replay them by
  `change_id` to keep stored values current.
- **Webhooks.** `POST` of the §9.11 envelope with `X-Signature: t=<unix seconds>,v1=<hex
  HMAC-SHA256 of "<t>.<raw body>">`. Check it in constant time and reject a `t` more than five
  minutes off (scripts/webhook_receiver.py); answer 2xx within 10 seconds; dedupe on `id`;
  don't rely on order. Failed deliveries are tried again about 1 and 10 minutes later.
- **AI chats.** MCP at `/mcp` (Streamable HTTP) with the same `X-API-Key`: six read-only tools
  over the same data, compact answers under about 8 KB (`docs/MCP.md`).
- **What we never do.** No advice, ratings, targets or rankings; no end-user data in requests,
  responses or events; we never contact a client's users. Every response says: Information
  from public filings. Not investment advice.
