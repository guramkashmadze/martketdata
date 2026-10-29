# Golden metrics (E4a-S4, E4b-S1, E4b-S4; PLAN.md §9.3, §9.4)

One TOML file per company (D-078's format, D-084). Each `[[period]]` holds the values one
earnings release prints for a quarter (`type = "Q"`) or a fiscal year (`type = "FY"`), read by
hand from the exhibits at `url` (the release text stored in `documents`, row by row) and given in
millions of dollars (diluted EPS as printed; margins and growth in percent to one decimal).

Ten companies, the last 8 quarters as of the fixtures (2026-10-28) and the years between, core
set first (D-020): revenue, operating income, net income, diluted EPS, operating cash flow,
capex, cash, long-term debt, total equity, and free cash flow, margins and year-over-year
growth computed by hand from the printed values.

- AAPL (September year end, 52/53 weeks), TGT (January retailer), COST (12- and 16-week
  quarters), JPM (a bank: no revenue), XOM (history under its predecessor CIK 34088): E4a's
  revenue, net income, diluted EPS and total assets; E4b adds the rest of the core set.
- PG (June year end), NVDA (January year end, capex as productive assets), O (a REIT: net income
  to common stockholders), TRV (an insurer), DE (November year end, 52/53 weeks).
- A quarter that a release prints only year to date is the difference of two printed columns
  (six months less three, nine less six, the year less nine), given in the comment above it.
  A fourth quarter printed as three months is the value the 10-K's year less nine months gives.
- `not_applicable` lists metrics that are null with a flag (a bank's revenue, §9.4 rule 10).
- `known_differences` lists values where the filings give another value, or none, with the
  reason; each is listed in PLAN.md §16. The test checks that they still differ, so a fix is
  noticed: companies' own rounding between quarterly and year-to-date columns (NVDA, DE, PG),
  Costco's year restated in its 10-K, Realty Income's 2024 net income before preferred
  dividends, ExxonMobil's cash including restricted cash in the successor's joint 10-Q, and
  JPMorgan's long-term debt under a concept the catalog doesn't list.
- A quarter whose own release isn't stored is checked against the prior-period columns of a
  later one (JPMorgan's third quarter of 2024, in its fourth-quarter supplement).

`tests/unit/test_golden_metrics.py` builds the metrics from the committed companyfacts
fixtures (`fixtures`: the company's, then its predecessors', whose facts help derive its
quarters) and compares every value and fiscal label.
