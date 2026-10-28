# Golden metrics (E4a-S4, E4b-S1, E4b-S4; PLAN.md §9.3, §9.4)

One TOML file per company (D-078's format, D-084). Each `[[period]]` holds the values one
earnings release prints for a quarter (`type = "Q"`) or a fiscal year (`type = "FY"`), read by
hand from the exhibits at `url` and given in millions of dollars (diluted EPS as printed).

- E4a's five companies, the last 8 quarters as of the fixtures (2026-10-28): AAPL (September
  year end, 52/53 weeks), TGT (January retailer), COST (12- and 16-week quarters), JPM (a bank:
  no revenue) and XOM (history under its predecessor CIK 34088): revenue, net income and diluted
  EPS where the filings report three months, and total assets at every quarter end.
- E4b-S1: operating cash flow and capex for AAPL, TGT, COST, XOM and PG. Releases print the
  cash-flow statement year to date, so a quarter is the difference of two printed columns
  (six months less three, nine less six, the year less nine), given in the comment above it;
  ExxonMobil prints its quarters too.
- `not_applicable` lists metrics that are null with a flag (a bank's revenue, §9.4 rule 10).
- `known_differences` lists values where the release and the filing differ (each in PLAN.md
  §16 with its reason); the test checks that they still differ, so a fix is noticed.
- A quarter whose own release isn't stored is checked against the prior-period columns of a
  later one (JPMorgan's third quarter of 2024, in its fourth-quarter supplement).

`tests/unit/test_golden_metrics.py` builds the metrics from the committed companyfacts
fixtures (`fixtures`: the company's, then its predecessors', whose facts help derive its
quarters) and compares every value and fiscal label.
