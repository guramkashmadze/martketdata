# Golden reported metrics (E4a-S4, PLAN.md §9.3, §9.4)

One TOML file per company (D-078's format, D-084): AAPL (September year end, 52/53 weeks),
TGT (January retailer), COST (12- and 16-week quarters), JPM (a bank: no revenue) and XOM
(history under its predecessor CIK 34088). Each `[[period]]` holds the values one earnings
release prints for a quarter (`type = "Q"`) or a fiscal year (`type = "FY"`), read by hand from
the exhibits at `url` and given in millions of dollars (diluted EPS as printed).

- The last 8 quarters as of the fixtures (2026-10-28): revenue, net income and diluted EPS
  where the filings report three months (fourth-quarter flows are derived in E4b), and total
  assets at every quarter end.
- `not_applicable` lists metrics that are null with a flag (a bank's revenue, §9.4 rule 10).
- A quarter whose own release isn't stored is checked against the prior-period columns of the
  next one (JPMorgan's third quarter of 2024, in its fourth-quarter supplement).

`tests/unit/test_golden_metrics.py` builds the metrics from the committed companyfacts
fixtures (`fixtures`, merged with the predecessor's like the service layer) and compares
every value and fiscal label.
