"""E7a-S5: the golden set (tests/golden/earnings/, PLAN.md §9.9). Each release's four slots,
read by hand from its exhibit, against what the rules extract and the checks pass for the
committed fixture, with the production overrides (data/extraction_overrides.toml). A slot is
served with exactly the printed value, doesn't apply, or is a listed difference: never a
different number."""

import tomllib
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from release_fixtures import fixture_release

from marketdata.extract.earnings_rules import RuleExtractor, parse_overrides
from marketdata.extract.interface import SLOTS
from marketdata.normalize.earnings import assemble, not_applicable_slots
from marketdata.verify.number_check import read_number

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = ROOT / "tests" / "golden" / "earnings"
OVERRIDES = parse_overrides((ROOT / "data" / "extraction_overrides.toml").read_text())
SCALES = {"thousands": Decimal(1000), "millions": Decimal(1_000_000)}


def _cases() -> list[Any]:
    cases = []
    for path in sorted(GOLDEN.glob("*.toml")):
        company = tomllib.loads(path.read_text())
        for release in company["release"]:
            cases.append(
                pytest.param(company, release, id=f"{path.stem}-{release['accession_no']}")
            )
    return cases


CASES = _cases()


def test_the_golden_set_has_at_least_30_earnings_releases() -> None:
    releases = [case.values[1] for case in CASES if case.values[1]["status"] != "no_statement"]

    assert len(releases) >= 30
    assert all(release["url"].startswith("https://www.sec.gov/Archives/") for release in releases)


@pytest.mark.parametrize(("company", "golden"), CASES)
def test_a_golden_release(company: dict[str, Any], golden: dict[str, Any]) -> None:
    cik, accession = company["cik"], golden["accession_no"]
    reasons, _ = not_applicable_slots(cik, accession, company.get("sic_code"), OVERRIDES)
    release = replace(
        fixture_release(cik, accession, not_applicable=reasons),
        fiscal_year_end=company["fiscal_year_end"],
    )
    result = RuleExtractor([o for o in OVERRIDES if o.cik == cik]).extract(release)
    record = assemble(
        result,
        {document.doc_name: document.text for document in release.documents},
        accession_no=accession,
        cik=cik,
        accepted_at=None,
        fiscal_year_end=company["fiscal_year_end"],
    )

    assert record.status == golden["status"]
    stated = golden["period_end"]
    assert record.period_end == (stated if isinstance(stated, date) else None)
    if "fiscal" in golden:
        assert f"{record.fiscal_period} {record.fiscal_year}" == golden["fiscal"]
    if golden["status"] == "no_statement":
        return
    differences = golden.get("differences", {})
    for slot in SLOTS:
        fact = record.fact(slot)
        if slot in golden.get("not_applicable", []):
            assert fact.check_status == "not_applicable", slot
        elif slot in differences:
            assert fact.check_status != "passed", f"{slot} passes now: drop its difference"
        else:
            value, prior = golden[slot]
            scale = Decimal(1) if slot == "eps_diluted" else SCALES[golden["scale"]]
            assert fact.check_status == "passed", (slot, fact.quality_flags)
            assert (fact.value, fact.prior_value) == (
                _printed(value) * scale,
                _printed(prior) * scale,
            )


def _printed(text: str) -> Decimal:
    number = read_number(text)
    assert number is not None, text
    return number
