"""E5a-S3: the response contract every /v1 route keeps (PLAN.md §10.1, §9.10): the envelope,
sources on sec.gov, flags that name their value, and a complete proof object on every value."""

from typing import Any

from marketdata.api.envelope import DISCLAIMER

SEC_FOLDER = "https://www.sec.gov/Archives/edgar/data/"
EARNINGS_PROOF = ("accession_no", "document_url", "source_quote", "scale_text", "method")
METRIC_PROOF = ("accession_no", "filing_url", "method", "calc_version")
INSIDER_PROOF = (  # E8: each Form 4 an insider trade sums
    "accession_no",
    "form",
    "filed_at",
    "filing_url",
    "document_url",
    "lines",
    "method",
    "extractor_version",
)


def check_envelope(body: dict[str, Any]) -> None:
    """The §10.1 envelope, and the §9.10 proof on every value in `data`."""
    assert list(body) == ["data", "sources", "as_of", "quality_flags", "disclaimer"]
    assert body["disclaimer"] == DISCLAIMER
    assert body["as_of"] is None or body["as_of"].endswith("Z")
    for source in body["sources"]:
        assert set(source) == {"accession_no", "form", "filed_at", "url"}
        assert source["url"].startswith(SEC_FOLDER), source
    for flag in body["quality_flags"]:
        assert set(flag) == {"code", "metric", "period_end", "ticker"} and flag["code"]
    values = list(_with_value(body["data"]))
    for value in values:
        check_proof(value)
    for filing in _filings(body["data"]):
        assert filing["accession_no"] and filing["url"].startswith(SEC_FOLDER), filing


def check_proof(item: dict[str, Any]) -> None:
    """A complete proof object; a null value says why."""
    assert "proof" in item, item
    proof = item["proof"]
    if item["value"] is None:
        assert item.get("reasons") or item.get("quality_flags"), item
        return
    if "document_url" in proof:  # an earnings fact
        assert all(proof[key] for key in EARNINGS_PROOF), proof
        assert proof["checks"] and proof["extractor_version"], proof
        cross_check = proof["cross_check"]
        assert set(cross_check) == {"status", "filing_accession_no", "filing_value"}
        if cross_check["status"] in ("matched", "mismatch"):
            assert cross_check["filing_accession_no"] and cross_check["filing_value"] is not None
    elif "filings" in proof:  # an insider trade (E8)
        assert proof["filings"] and proof["checks"], proof
        for filing in proof["filings"]:
            assert all(filing[key] for key in INSIDER_PROOF), filing
            assert filing["filing_url"].startswith(SEC_FOLDER), filing
    else:  # a metric value
        assert all(proof[key] for key in METRIC_PROOF), proof
        assert proof["source_accessions"], proof
        if proof["method"] in ("reported", "derived_ytd", "derived_q4"):
            assert proof["concept"], proof
        assert proof["filing_url"].startswith(SEC_FOLDER)
    assert item["unit"], item


def _with_value(node: Any) -> Any:
    """Every object holding a served value."""
    if isinstance(node, dict):
        if "value" in node:
            yield node
        for child in node.values():
            yield from _with_value(child)
    elif isinstance(node, list):
        for child in node:
            yield from _with_value(child)


def _filings(node: Any) -> Any:
    if isinstance(node, dict):
        if "events" in node:
            yield node
        for child in node.values():
            yield from _filings(child)
    elif isinstance(node, list):
        for child in node:
            yield from _filings(child)
