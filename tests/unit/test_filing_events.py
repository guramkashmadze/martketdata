"""E3-S6: §9.6 labels in code, hidden events left out, 13D and 13G only on the listing of the
company they are about (D-061), unknown 8-K items kept with their code (D-064)."""

import pytest

from marketdata.normalize.filing_events import (
    ITEM_LABELS,
    FilingEvent,
    Importance,
    filing_events,
)

# PLAN.md §9.6 as written, plus D-064's additions.
PLAN_ITEMS = {
    "1.01": ("Signed a major agreement", "normal"),
    "1.02": ("Ended a major agreement", "normal"),
    "1.03": ("Bankruptcy or receivership", "high"),
    "1.05": ("Material cybersecurity incident", "high"),
    "2.01": ("Completed an acquisition or sale of assets", "high"),
    "2.02": ("Quarterly or annual results", "high"),
    "2.03": ("Took on a major financial obligation", "normal"),
    "2.05": ("Restructuring or exit costs", "normal"),
    "2.06": ("Material impairment", "high"),
    "3.01": ("Delisting notice or listing-rule issue", "high"),
    "3.02": ("Unregistered sale of shares", "normal"),
    "4.01": ("Changed auditor", "normal"),
    "4.02": ("Past financial statements can no longer be relied on", "high"),
    "5.02": ("Director or executive change", "normal"),
    "5.03": ("Changed bylaws or fiscal year", "normal"),
    "5.07": ("Shareholder vote results", "normal"),
    "7.01": ("Regulation FD disclosure", "normal"),
    "8.01": ("Other events", "normal"),
    "9.01": ("Financial statements and exhibits", "hidden"),
}
ADDED_ITEMS = {"1.04", "2.04", "3.03", "5.01", "5.04", "5.05", "5.06", "5.08"}
ACTIVIST = "An investor crossed 5% and may seek to influence the company"


def test_the_item_table_is_section_nine_six_plus_the_added_items() -> None:
    assert {code: (label.text, label.importance) for code, label in ITEM_LABELS.items()} == {
        **PLAN_ITEMS,
        **{code: (ITEM_LABELS[code].text, ITEM_LABELS[code].importance) for code in ADDED_ITEMS},
    }
    assert ITEM_LABELS["5.01"].text == "Change in control of the company"


def test_hidden_items_are_not_returned() -> None:
    events = filing_events("8-K", ("2.02", "9.01"), "001-36743")

    assert events == [FilingEvent("2.02", "Quarterly or annual results", "high")]


def test_an_unknown_item_keeps_its_code() -> None:
    assert filing_events("8-K", ("6.03",), None) == [FilingEvent("6.03", "8-K item 6.03", "normal")]


@pytest.mark.parametrize("form", ["8-K/A", "8-K12B"])
def test_amendments_and_successor_forms_get_item_labels(form: str) -> None:
    assert [event.item_or_form for event in filing_events(form, ("5.02", "5.02"), None)] == ["5.02"]


@pytest.mark.parametrize("form", ["SC 13D", "SCHEDULE 13D"])
def test_a_13d_is_labelled_for_the_company_it_is_about(form: str) -> None:
    assert filing_events(form, None, "005-89078") == [FilingEvent(form, ACTIVIST, "high")]


def test_the_filers_own_listing_gets_no_label() -> None:
    assert filing_events("SCHEDULE 13D", None, None) == []


@pytest.mark.parametrize(
    ("form", "label", "importance"),
    [
        ("SCHEDULE 13D/A", "An investor seeking influence changed its stake", "normal"),
        ("SC 13G", "A passive investor holds more than 5%", "normal"),
        ("SCHEDULE 13G", "A passive investor holds more than 5%", "normal"),
    ],
)
def test_other_stake_filings(form: str, label: str, importance: Importance) -> None:
    assert filing_events(form, None, "005-33632") == [FilingEvent(form, label, importance)]


@pytest.mark.parametrize("form", ["SC 13G/A", "SCHEDULE 13G/A", "10-Q", "4"])
def test_hidden_forms_and_forms_without_events(form: str) -> None:
    assert filing_events(form, None, "005-33632") == []
