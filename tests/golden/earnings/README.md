# Golden earnings releases (E7a-S5, PLAN.md §9.9)

One TOML file per company. Each `[[release]]` holds the four slots of one earnings release as
printed in its income statement: the quarter, then the same quarter a year earlier, read by
hand from the exhibit at `url` and multiplied by `scale` (per-share values are never scaled).

- `not_applicable` lists the slots the release doesn't present (an override in
  `data/extraction_overrides.toml`) or that don't apply (a bank's revenue, D-072).
- `differences` lists slots where the rules don't serve the printed value, with the reason;
  each is also listed in PLAN.md §16. A listed slot that starts passing fails the test, so the
  entry gets removed.
- `period_end = "not stated"` when no exhibit states the quarter's end date.

`tests/unit/test_golden_earnings.py` runs the rules and the checks over the committed
fixtures (`tests/fixtures/sec/filings/`) and compares every slot.
