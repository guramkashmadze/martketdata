# Edge cases

Every parser surprise, override, amendment oddity or failed check that taught us something (D-021).
This file is the evidence that the pipeline is the product: each row is something a client would otherwise have to find and handle on its own.

**How to add an entry**

- Append a row at the bottom; never edit or delete old rows. If a later fix changes how a case is handled, add a new row that refers to the old one.
- **Date:** the day you hit it (YYYY-MM-DD).
- **Epic:** the epic and story, e.g. `E7a-S3`.
- **Company and accession:** ticker, CIK and accession number (with dashes), e.g. `AAPL (320193) 0000320193-24-000123`. Use `—` when no filing is involved.
- **What broke:** what we expected, what the data actually looked like, and the SEC URL where it can be seen.
- **How it was handled:** the rule, override, flag or test that covers it now (file and test name where possible).

| Date | Epic | Company and accession | What broke | How it was handled |
| --- | --- | --- | --- | --- |
