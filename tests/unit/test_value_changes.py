"""E7a-S4: which served values a run changes, and why (D-027, D-077)."""

from decimal import Decimal

from marketdata.normalize.changes import Served, amendment_changes, correction_changes

A = "0000320193-26-000018"


def _served(net: str | None, prior: str | None = "23434", version: str = "1") -> list[Served]:
    return [
        Served("revenue", Decimal(109417), Decimal(94036), version),
        Served("net_income", Decimal(net) if net else None, Decimal(prior) if prior else None,
               version),
    ]  # fmt: skip


def test_an_unchanged_run_changes_nothing() -> None:
    assert correction_changes(A, _served("29789"), _served("29789")) == []


def test_a_new_rules_version_that_changes_a_value_is_a_correction() -> None:
    [change] = correction_changes(A, _served("29789"), _served("29790", version="2"))

    assert change.entity_key == {"accession_no": A, "slot": "net_income", "field": "value"}
    assert (change.old_value, change.new_value) == (Decimal(29789), Decimal(29790))
    assert (change.reason, change.detail) == ("correction", "rules version 1 to 2")


def test_a_value_that_newly_fails_or_passes_is_a_change() -> None:
    failed = correction_changes(A, _served("29789"), _served(None, None))
    fixed = correction_changes(A, _served(None, None), _served("29789"))

    assert [(c.entity_key["field"], c.old_value, c.new_value) for c in failed] == [
        ("value", Decimal(29789), None),
        ("prior_value", Decimal(23434), None),
    ]
    assert [(c.old_value, c.new_value) for c in fixed] == [
        (None, Decimal(29789)),
        (None, Decimal(23434)),
    ]
    assert "unchanged: an override" in fixed[0].detail


def test_an_8k_a_that_changes_a_value_is_an_amendment() -> None:
    amended = "0000320193-26-000019"

    [change] = amendment_changes(amended, A, _served("29789"), _served("29790"))

    assert change.entity_key == {
        "accession_no": amended,
        "slot": "net_income",
        "field": "value",
        "amends": A,
    }
    assert change.reason == "amendment"
    assert change.detail == f"8-K/A {amended} amends the earnings release {A}"
