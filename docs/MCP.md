# MCP guide

The platform's data in AI chats (E6): Claude, ChatGPT, Gemini and other clients that speak the
Model Context Protocol connect to `/mcp` with an API key and answer questions about US public
companies with numbers from SEC filings, each with its proof and its SEC link. The server only
reads: it never calls a model, and nothing it answers is written by one (D-014).

Information from public filings. Not investment advice.

## What you need

- **The server.** `/mcp` is part of the API: `pdm run uvicorn marketdata.api.app:app --port 8000`
  serves it at `http://localhost:8000/mcp`. On the live VM (OD-1) it is `https://<live host>/mcp`;
  set `PUBLIC_BASE_URL` to that address, as the server accepts only this machine's host name and
  the public one (DNS rebinding protection, D-107).
- **An API key** with the `api` scope, sent in the `X-API-Key` header, the same key as `/v1`:

```bash
pdm run marketdata clients create my-ai-client
```

```bash
pdm run marketdata keys create my-ai-client
```

  The key is printed once. Each tool call counts against the key's requests per minute (120 by
  default) together with its `/v1` requests, and is logged in `api_requests` with method `MCP`
  and the tool's name as the path. The log holds the key, the tool, the status and the time:
  never the conversation, which the AI client doesn't send, only the tool's arguments.

The transport is Streamable HTTP. The server keeps no sessions and answers JSON; it speaks the
2025 protocol revisions (2024-11-05 to 2025-11-25, which open with `initialize`) and 2026-07-28.

Check it with curl:

```bash
curl -s http://localhost:8000/mcp -H "X-API-Key: $MARKETDATA_API_KEY" -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"lookup_company","arguments":{"query":"berkshire"}}}'
```

Without a key the answer is `401` with `{"error":{"code":"unauthorized","message":"Send your API
key in the X-API-Key header."}}`; a revoked key is `401`, a `review` key `403`, and a key over its
rate `429` with `Retry-After`.

## Connecting a client

Keep the key out of files you commit: each client below can read it from an environment variable
or a prompt. Checked on 2026-10-31: the server with the official Python SDK's client over HTTP
(both protocol generations) and with curl; Claude Code's flags from `claude mcp add --help`. The
other clients' settings come from their own documentation of that date and weren't run here.

### Claude Code

```bash
claude mcp add --transport http marketdata http://localhost:8000/mcp --header "X-API-Key: $MARKETDATA_API_KEY"
```

`--scope user` makes it available in every project; `/mcp` inside Claude Code shows whether it
is connected and lists the tools.

### Claude Desktop

Claude Desktop's own connectors (Settings, Connectors, Add custom connector) connect from
Anthropic's cloud, so they reach only a server on the public internet: the live VM, not
`localhost`. For the live VM, add a custom connector with the URL `https://<live host>/mcp`,
choose "No sign in" and add a request header `X-API-Key` with the key; Claude stores it
encrypted.

For a server on this machine, Claude Desktop starts the `mcp-remote` bridge (npm), which talks
to `/mcp` and passes the header. In `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "marketdata": {
      "command": "npx",
      "args": [
        "-y", "mcp-remote", "http://localhost:8000/mcp",
        "--transport", "http-only",
        "--header", "X-API-Key:${MARKETDATA_API_KEY}"
      ],
      "env": {"MARKETDATA_API_KEY": "mdk_..."}
    }
  }
}
```

The header is written without a space after the colon and the key comes from `env`, because
Claude Desktop on Windows splits arguments at spaces (mcp-remote's README). If mcp-remote refuses
the plain `http` address, add `"--allow-http"` to `args`. Restart Claude Desktop after editing;
its logs are in `~/Library/Logs/Claude/mcp*.log`.

### Cursor

In `.cursor/mcp.json` (this project) or `~/.cursor/mcp.json` (every project), with
`MARKETDATA_API_KEY` set in the environment Cursor starts from:

```json
{
  "mcpServers": {
    "marketdata": {
      "url": "http://localhost:8000/mcp",
      "headers": {"X-API-Key": "${env:MARKETDATA_API_KEY}"}
    }
  }
}
```

### VS Code

In `.vscode/mcp.json`; VS Code asks for the key when the server first starts and keeps it in its
secret storage:

```json
{
  "inputs": [
    {"type": "promptString", "id": "marketdata-key", "description": "marketdata API key", "password": true}
  ],
  "servers": {
    "marketdata": {
      "type": "http",
      "url": "http://localhost:8000/mcp",
      "headers": {"X-API-Key": "${input:marketdata-key}"}
    }
  }
}
```

### MCP Inspector

The protocol's own test client (Node 22.19 or later). Its CLI lists the tools:

```bash
npx @modelcontextprotocol/inspector --cli http://localhost:8000/mcp --transport http --method tools/list --header "X-API-Key: $MARKETDATA_API_KEY"
```

Without `--cli` it opens a web UI at `http://localhost:6274`: choose Streamable HTTP, enter the
URL and add the `X-API-Key` header in the server's settings. A server saved there keeps its
headers in plain text in `~/.mcp-inspector/mcp.json`.

## The tools

Every answer is JSON text: `data`, `sources` (the filings the values read, each with its form,
filing date and SEC link), `as_of` (when the newest row used last changed) and the disclaimer.
Numbers keep every digit. A metric value carries a short proof: the filing it came from
(`accession_no`; for a derived, trailing or computed value the latest filing it reads), the
method and the XBRL concept, named once for a series whose values share it, and its quality
flags. The full proof of any value is one `/v1` call or proof page away (D-108).

| Tool | Use it for | Arguments | Limits |
| --- | --- | --- | --- |
| `lookup_company` | A ticker from a name, a ticker or a CIK, and whether the company is covered | `query` | 10 matches |
| `company_snapshot` | "Tell me about …": identity, latest quarter, trailing twelve months of the 16 core metrics, latest release, three latest filings | `ticker` | |
| `get_financials` | History and trends | `ticker`, `metrics`, `period` (Q, FY or TTM; Q by default), `limit` (periods, 8 by default) | 12 periods, 16 metrics, 40 values |
| `recent_filings` | What a company filed or announced, with plain-language labels | `ticker`, `form`, `days` (90 by default) | 20 filings, 365 days |
| `compare_companies` | Two to five companies side by side, never ranked | `tickers`, `metrics`, `period` (TTM by default) | 25 values |
| `latest_earnings` | The latest results release, each value with its full proof and the cross-check against the 10-Q or 10-K | `ticker` | |

Without `metrics`, `get_financials` and `compare_companies` use revenue, operating income, net
income, diluted EPS and free cash flow. Without `form`, `recent_filings` lists annual,
quarterly and current reports and 5% stakes in the company, not prospectuses or insider forms
(D-110). Amounts are in base units: USD, shares, USD per share; margins and growth are percents.

A tool that can't answer says why, as the API does: `invalid_parameter` (with what to change),
`not_found`, or `not_applicable` for a fund, a foreign private issuer or a company outside the
universe ("SPY (SPDR S&P 500 ETF TRUST): A fund: fundamentals don't apply."). A missing or
unverified value is `null` with its flag or reasons, never an estimate.

`get_financials` for Apple's revenue and free cash flow, two quarters (dev database,
2026-10-31; the second source is the first quarter's 10-Q, which the year-to-date free cash
flow of the second quarter reads):

```json
{"data": {"ticker": "AAPL", "cik": 320193, "company": "Apple Inc.", "period_type": "Q",
  "metrics": [
    {"metric": "revenue", "unit": "USD", "concept": "RevenueFromContractWithCustomerExcludingAssessedTax",
     "values": [
       {"period_end": "2026-06-27", "fiscal": "FY2026 Q3", "value": 109417000000, "method": "reported",
        "accession_no": "0000320193-26-000020", "flags": []},
       {"period_end": "2026-03-28", "fiscal": "FY2026 Q2", "value": 111184000000, "method": "reported",
        "accession_no": "0000320193-26-000013", "flags": []}]},
    {"metric": "free_cash_flow", "unit": "USD", "concept": null,
     "values": [
       {"period_end": "2026-06-27", "fiscal": "FY2026 Q3", "value": 31914000000, "method": "computed",
        "accession_no": "0000320193-26-000020", "flags": []},
       {"period_end": "2026-03-28", "fiscal": "FY2026 Q2", "value": 26731000000, "method": "computed",
        "accession_no": "0000320193-26-000013", "flags": []}]}]},
 "sources": [
   {"accession_no": "0000320193-26-000020", "form": "10-Q", "filed_at": "2026-07-31",
    "url": "https://www.sec.gov/Archives/edgar/data/320193/000032019326000020/"},
   {"accession_no": "0000320193-26-000013", "form": "10-Q", "filed_at": "2026-05-01",
    "url": "https://www.sec.gov/Archives/edgar/data/320193/000032019326000013/"},
   {"accession_no": "0000320193-26-000006", "form": "10-Q", "filed_at": "2026-01-30",
    "url": "https://www.sec.gov/Archives/edgar/data/320193/000032019326000006/"}],
 "as_of": "2026-10-29T17:18:53.249611Z",
 "disclaimer": "Information from public filings. Not investment advice."}
```

## Example questions

Five questions and the tool calls they should lead to. The client chooses the calls; a client
that already knows a ticker skips `lookup_company`. The answers are numbers with their SEC
links, never a view on what to do with them.

1. "How did Apple's revenue and free cash flow change over the last 8 quarters?" (the E6 demo)
   `get_financials(ticker="AAPL", metrics=["revenue", "free_cash_flow"], period="Q", limit=8)`:
   16 values and the 10-Qs and 10-K they come from.
2. "What did NVIDIA report in its latest earnings release, and did the 10-Q confirm it?"
   `latest_earnings(ticker="NVDA")`: on the dev database the release of fiscal 2027's second
   quarter, revenue 96,221,000,000 USD, 105.9% over the year before, each of the four values
   `matched` against the 10-Q, with the rows as printed.
3. "Compare the operating and net margins of Coca-Cola and Procter & Gamble over the last
   twelve months."
   `compare_companies(tickers=["KO", "PG"], metrics=["operating_margin", "net_margin"])`: each
   company's own latest twelve months (their quarters end on different dates); Coca-Cola's
   values carry `stale_data`, as its latest 10-Q in the SEC's data is older than 120 days.
4. "Has JPMorgan filed anything important in the last 30 days?"
   `recent_filings(ticker="JPM", days=30)`: its reports and 8-Ks with labels such as "Other
   events" and their importance, without the thousands of structured notes it files.
5. "რამდენი იყო Berkshire Hathaway-ის წმინდა მოგება ბოლო ოთხ კვარტალში?" (Georgian: "What was
   Berkshire Hathaway's net income in the last four quarters?")
   `lookup_company(query="Berkshire Hathaway")`, then
   `get_financials(ticker="BRK-A", metrics=["net_income"], period="Q", limit=4)`: four quarters
   from FY2025 Q3 to FY2026 Q2, the fourth quarter of 2025 derived from the 10-K less nine
   months (`derived_q4`). Names are matched in the Latin alphabet, so the client looks up
   "Berkshire Hathaway", not its Georgian spelling; the answer's wording is the client's.

Questions about a fund ("What is SPY's revenue?") get `not_applicable` with the reason. The
tools answer with filed numbers only: none ranks, scores or recommends.
