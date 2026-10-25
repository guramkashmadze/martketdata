"""Plain-language labels for 8-K items and 5% stake filings (PLAN.md §9.6; E3-S6).

Pure. Each event has an importance; hidden ones (item 9.01, a Schedule 13G amendment) are never
returned. An 8-K item the table doesn't know is returned with a label that names only its code,
never dropped (D-064).

Schedule 13D and 13G filings have form codes from before and after the SEC's structured format
(`SC 13D` until December 2024, `SCHEDULE 13D` since; D-061). They are labelled only on the
listing of the company they are about, whose file number starts with `005-`. The filer's own
listing (Disney's 13D on FuboTV, Berkshire's 13G on Apple) gets no label: §9.6 describes the
filing from the side of the company whose shares were bought.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

Importance = Literal["high", "normal", "hidden"]
SUBJECT_FILE_NUMBER = "005-"


@dataclass(frozen=True)
class Label:
    text: str
    importance: Importance


# §9.6, with the items D-064 added (1.04, 2.04, 3.03, 5.01, 5.04, 5.05, 5.06, 5.08).
ITEM_LABELS: dict[str, Label] = {
    "1.01": Label("Signed a major agreement", "normal"),
    "1.02": Label("Ended a major agreement", "normal"),
    "1.03": Label("Bankruptcy or receivership", "high"),
    "1.04": Label("Mine safety shutdown order or pattern of violations", "normal"),
    "1.05": Label("Material cybersecurity incident", "high"),
    "2.01": Label("Completed an acquisition or sale of assets", "high"),
    "2.02": Label("Quarterly or annual results", "high"),
    "2.03": Label("Took on a major financial obligation", "normal"),
    "2.04": Label("An event made a financial obligation due sooner or larger", "high"),
    "2.05": Label("Restructuring or exit costs", "normal"),
    "2.06": Label("Material impairment", "high"),
    "3.01": Label("Delisting notice or listing-rule issue", "high"),
    "3.02": Label("Unregistered sale of shares", "normal"),
    "3.03": Label("Changed the rights of shareholders", "normal"),
    "4.01": Label("Changed auditor", "normal"),
    "4.02": Label("Past financial statements can no longer be relied on", "high"),
    "5.01": Label("Change in control of the company", "high"),
    "5.02": Label("Director or executive change", "normal"),
    "5.03": Label("Changed bylaws or fiscal year", "normal"),
    "5.04": Label("Temporary halt on trading in employee benefit plans", "normal"),
    "5.05": Label("Changed or waived its code of ethics", "normal"),
    "5.06": Label("Changed its shell company status", "normal"),
    "5.07": Label("Shareholder vote results", "normal"),
    "5.08": Label("Set the deadline for shareholder director nominations", "normal"),
    "7.01": Label("Regulation FD disclosure", "normal"),
    "8.01": Label("Other events", "normal"),
    "9.01": Label("Financial statements and exhibits", "hidden"),
}

_13D = Label("An investor crossed 5% and may seek to influence the company", "high")
_13D_AMENDMENT = Label("An investor seeking influence changed its stake", "normal")
_13G = Label("A passive investor holds more than 5%", "normal")
_13G_AMENDMENT = Label("A passive 5% holder changed its stake", "hidden")
FORM_LABELS: dict[str, Label] = {
    "SC 13D": _13D,
    "SCHEDULE 13D": _13D,
    "SC 13D/A": _13D_AMENDMENT,
    "SCHEDULE 13D/A": _13D_AMENDMENT,
    "SC 13G": _13G,
    "SCHEDULE 13G": _13G,
    "SC 13G/A": _13G_AMENDMENT,
    "SCHEDULE 13G/A": _13G_AMENDMENT,
}


@dataclass(frozen=True)
class FilingEvent:
    item_or_form: str  # an 8-K item ("2.02") or the form as filed ("SCHEDULE 13D")
    label: str
    importance: Importance  # "high" or "normal"; hidden events are not returned


def filing_events(
    form: str, items_8k: Sequence[str] | None, file_number: str | None
) -> list[FilingEvent]:
    """The events one company's listing of a filing shows, in the order the SEC lists items."""
    if form.startswith("8-K"):  # 8-K, 8-K/A and the successor forms 8-K12B, 8-K12G3, 8-K15D5
        events = []
        for code in dict.fromkeys(items_8k or ()):
            label = ITEM_LABELS.get(code, Label(f"8-K item {code}", "normal"))
            if label.importance != "hidden":
                events.append(FilingEvent(code, label.text, label.importance))
        return events
    stake = FORM_LABELS.get(form)
    if stake is None or stake.importance == "hidden":
        return []
    if not (file_number or "").startswith(SUBJECT_FILE_NUMBER):
        return []  # the filer's own listing
    return [FilingEvent(form, stake.text, stake.importance)]
