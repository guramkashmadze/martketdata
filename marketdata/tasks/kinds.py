"""The task kinds E3 runs itself. Kinds that filings are routed to (facts_refresh,
earnings_extract, form4_parse, f13_parse) are named with the routing rules in
normalize/filings.py, and run once their epic registers a handler (PLAN.md §4)."""

FILINGS_WATCH = "filings_watch"  # key: CIK
FILINGS_BACKFILL = "filings_backfill"  # key: CIK
SECURITY_MASTER = "security_master"  # key: the date, YYYY-MM-DD
