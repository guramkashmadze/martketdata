"""Insider trades (PLAN.md §9.7, E8): what a parsed Form 4 stores, and its transaction codes.

Pure: takes parsed filings and stored lines and returns records and decisions.
"""

from dataclasses import dataclass
from datetime import date

from marketdata.normalize.form4 import Form4

# The SEC's Form 4 transaction codes (General Instruction 8), in plain words. Other codes are
# stored and served as filed.
CODE_LABELS = {
    "P": "Purchase (open market or private)",
    "S": "Sale (open market or private)",
    "A": "Grant or award",
    "D": "Disposition to the issuer",
    "F": "Shares withheld or delivered to pay an exercise price or tax",
    "M": "Exercise or conversion of a derivative (exempt)",
    "C": "Conversion of a derivative",
    "X": "Exercise of an in-the-money or at-the-money derivative",
    "O": "Exercise of an out-of-the-money derivative",
    "E": "Expiration of a short derivative position",
    "H": "Expiration or cancellation of a long derivative position with value received",
    "G": "Gift",
    "I": "Discretionary transaction",
    "J": "Other acquisition or disposition (see footnotes)",
    "K": "Equity swap or similar instrument",
    "L": "Small acquisition",
    "U": "Disposition in a change-of-control tender",
    "W": "Acquisition or disposition by will or inheritance",
    "Z": "Deposit into or withdrawal from a voting trust",
}


@dataclass(frozen=True)
class FilingRecord:
    """A parsed Form 4 and where it came from: the issuer's listing and the stored XML."""

    accession_no: str
    filed_at: date
    doc_name: str  # the XML in the filing folder
    raw_key: str
    form4: Form4

    @property
    def owner_ciks(self) -> tuple[int, ...]:
        """The reporting owners' CIKs, sorted: a 4/A amends a filing by the same owners."""
        return tuple(sorted({owner.cik for owner in self.form4.owners}))
