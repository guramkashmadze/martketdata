"""The `marketdata` admin CLI.

Command groups are registered up front so `marketdata --help` shows the whole tool; each epic
fills in its group. Commands load settings themselves, so `--help` works without a .env.
"""

import typer

app = typer.Typer(
    help="marketdata: trusted, verified data from SEC filings. Information, not investment advice.",
    no_args_is_help=True,
)

# Group name, help text, and the epic that adds its first command (PLAN.md §12).
_GROUPS = [
    ("sec", "Fetch from the SEC through the rate-limited client.", "E1"),
    ("ingest", "Load the security master and filings into the database.", "E2"),
    ("universe", "Load and grow the covered universe.", "E2"),
    ("tasks", "List, retry and purge queued tasks.", "E3"),
    ("backfill", "Load history: filings, earnings, facts, prices, insiders, funds.", "E3"),
    ("extract", "Earnings extraction and the hit-rate report.", "E7a"),
    ("stats", "Freshness and track-record statistics.", "E7c"),
    ("normalize", "Rebuild metrics from stored XBRL facts.", "E4b"),
    ("quality", "Quality checks and the accuracy report.", "E4b"),
    ("clients", "Create and list API clients.", "E5a"),
    ("keys", "Create, list and revoke API keys.", "E5a"),
    ("webhooks", "Manage webhook destinations and send test events.", "E5b"),
    ("usage", "API and MCP usage per client.", "E11"),
]

for _name, _help, _epic in _GROUPS:
    app.add_typer(
        typer.Typer(no_args_is_help=True),
        name=_name,
        help=f"{_help} (from {_epic})",
    )
