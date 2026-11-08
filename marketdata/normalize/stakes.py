"""5% stakes from Schedules 13D and 13G: who holds, since which filing, and what a run changes
(PLAN.md §9.15, E16-S3).

Pure: takes the filings stored about one company (and its predecessors) and returns holders.

- **Holders.** Filings belong to one holder when they share the filer's CIK or a 13D reporting
  person's CIK, or when one names the other as its previous accession. Fresenius Medical Care
  AG and its US holding company take turns filing one 13D; Frontier Capital Management filed
  under two CIKs; amendments name the filing before them in 141 of 313 (EDGE_CASES.md, E16).
- **Share classes.** A filing's class is the company's security that E9's CUSIP map links its
  CUSIP to (Alphabet's Class A and Class C are two holdings for Vanguard); a CUSIP linked to
  none of them, mistyped or replaced in a reverse split or reorganization, counts as the
  company's primary class.
- **Current stake.** A holder's latest filing about a class (by filing date, then accession
  number) is served and supersedes its earlier ones, whatever the form; there is no window.
- **Exited.** A 13G that ticks item 5, or a filing whose every reporting person reports below 5%
  of the class. Exactly 5.0% without the box is still held: percents are rounded and the SEC's
  threshold is more than 5% (State Street, BlackRock and JPMorgan file 5.0% and say so).
- **Amount.** Reporting persons' amounts overlap (a parent reports its subsidiaries' shares), so
  they are never added: a holder's amount is the largest that one person states, with its
  percent.
- **Changes (D-027).** A filing served before a run and replaced by a later filing of its holder
  (`amendment`), or read otherwise by a new parser version (`correction`), is a change to a
  served stake when its amount, percent or exit differs.
"""

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol

from marketdata.normalize.changes import ValueChange
from marketdata.normalize.schedule13 import Schedule13

EXIT_BELOW = Decimal(5)  # percent of class: a holder whose every person reports less exited

# The checks a stored filing passed (its proof), and the flags of those it failed (§9.15): a
# filing about another company than the listing's is refused, never stored.
ISSUER_MATCHES_LISTING = "issuer_matches_listing"
FORM_MATCHES_LISTING = "form_matches_listing"
PERCENT_IN_RANGE = "percent_in_range"
POWERS_WITHIN_AMOUNT = "powers_within_amount"
FORM_DIFFERS = "form_differs_from_listing"
PERCENT_OUT_OF_RANGE = "percent_out_of_range"
POWERS_EXCEED_AMOUNT = "powers_exceed_amount"
NOT_PARSED = "not_parsed"  # an old-format filing (SC 13D, SC 13G): listed, not read


class PersonStake(Protocol):
    """What the rules read of a reporting person (a parsed or a stored one)."""

    @property
    def position(self) -> int: ...
    @property
    def name(self) -> str: ...
    @property
    def cik(self) -> int | None: ...
    @property
    def amount(self) -> Decimal: ...
    @property
    def percent(self) -> Decimal: ...
    @property
    def sole_voting(self) -> Decimal | None: ...
    @property
    def shared_voting(self) -> Decimal | None: ...
    @property
    def sole_dispositive(self) -> Decimal | None: ...
    @property
    def shared_dispositive(self) -> Decimal | None: ...


@dataclass(frozen=True)
class StakeRecord:
    """A parsed filing and where it came from: the subject company's listing and the stored
    XML, with the checks it passed and the flags of those it failed."""

    accession_no: str
    filed_at: date
    doc_name: str  # the XML in the filing folder
    raw_key: str
    filing: Schedule13
    checks: tuple[str, ...]
    flags: tuple[str, ...]


@dataclass(frozen=True)
class StakeFiling:
    """A stored filing as the rules read it."""

    accession_no: str
    listing_cik: int  # the company, or a predecessor, the filing is about
    form: str
    filed_at: date
    filer_cik: int
    previous_accession: str | None
    cusips: tuple[str, ...]
    ceased_5pct: bool | None
    persons: tuple[PersonStake, ...]
    extractor_version: str = ""

    @property
    def person_ciks(self) -> tuple[int, ...]:
        return tuple(person.cik for person in self.persons if person.cik is not None)


@dataclass(frozen=True)
class Stake:
    """What a filing states for its holder: the largest amount one person reports."""

    holder: str  # the first reporting person's name
    amount: Decimal
    percent: Decimal
    exited: bool


@dataclass(frozen=True)
class Holding:
    """One holder's stake in one class: the filing served and the earlier ones it supersedes."""

    holder_key: str  # the holder's first filing (by filing date, then accession number)
    class_key: str | None  # our ticker of the class; None when the company has no ticker
    accession_no: str  # the latest filing, served
    superseded: tuple[str, ...]  # the earlier filings of the holder about the class, newest first


@dataclass(frozen=True)
class Holdings:
    holdings: tuple[Holding, ...]
    serving: Mapping[str, str]  # every filing: the filing serving its holder and class now

    def served(self) -> set[str]:
        return {holding.accession_no for holding in self.holdings}


def headline(persons: Sequence[PersonStake]) -> PersonStake:
    """The person whose amount is the largest (the first of equals): its amount and percent are
    the holder's, since persons' amounts overlap and are never added."""
    if not persons:
        raise ValueError("a filing without reporting persons has no stake")
    return max(persons, key=lambda person: (person.amount, -person.position))


def is_exited(ceased_5pct: bool | None, persons: Sequence[PersonStake]) -> bool:
    """A 13G that ticks item 5, or every person below 5% of the class."""
    return ceased_5pct is True or all(person.percent < EXIT_BELOW for person in persons)


def stake_of(filing: StakeFiling) -> Stake:
    top = headline(filing.persons)
    return Stake(
        holder=min(filing.persons, key=lambda person: person.position).name,
        amount=top.amount,
        percent=top.percent,
        exited=is_exited(filing.ceased_5pct, filing.persons),
    )


def filing_checks(
    listed_form: str, form: str, persons: Sequence[PersonStake]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The checks a filing passed and the flags of those it failed. The issuer check is passed
    by every stored filing: one about another company is refused."""
    passed, flags = [ISSUER_MATCHES_LISTING], []
    if form == listed_form:
        passed.append(FORM_MATCHES_LISTING)
    else:
        flags.append(FORM_DIFFERS)
    if all(Decimal(0) <= person.percent <= Decimal(100) for person in persons):
        passed.append(PERCENT_IN_RANGE)
    else:
        flags.append(PERCENT_OUT_OF_RANGE)
    if all(_powers_within(person) for person in persons):
        passed.append(POWERS_WITHIN_AMOUNT)
    else:  # Elon Musk's 13Gs on Tesla state the same shares as sole and shared power
        flags.append(POWERS_EXCEED_AMOUNT)
    return tuple(passed), tuple(flags)


def _powers_within(person: PersonStake) -> bool:
    voting = (person.sole_voting or 0) + (person.shared_voting or 0)
    dispositive = (person.sole_dispositive or 0) + (person.shared_dispositive or 0)
    return voting <= person.amount and dispositive <= person.amount


def share_classes(
    links: Iterable[tuple[str, str | None, int | None]], ciks: Collection[int]
) -> dict[str, str]:
    """The classes the CUSIP map tells apart: each (CUSIP, our ticker, its company's CIK) the map
    links to a security of one of these companies, as CUSIP to ticker (D-121)."""
    return {cusip: ticker for cusip, ticker, cik in links if ticker and cik in ciks}


def class_of(filing: StakeFiling, classes: Mapping[str, str], primary: str | None) -> str | None:
    """The class a filing is about: the first of its CUSIPs the map links to one of the
    company's securities, else the company's primary class."""
    for cusip in filing.cusips:
        if cusip in classes:
            return classes[cusip]
    return primary


def holder_keys(filings: Sequence[StakeFiling]) -> dict[str, str]:
    """Each filing's holder: the first filing (by filing date, then accession number) of the
    filings linked to it by a shared filer CIK or 13D reporting person CIK, or by a previous
    accession."""
    parent: dict[tuple[str, str], tuple[str, str]] = {}

    def find(node: tuple[str, str]) -> tuple[str, str]:
        parent.setdefault(node, node)
        root = node
        while parent[root] != root:
            root = parent[root]
        while parent[node] != root:
            parent[node], node = root, parent[node]
        return root

    def union(one: tuple[str, str], other: tuple[str, str]) -> None:
        parent[find(one)] = find(other)

    stored = {filing.accession_no for filing in filings}
    for filing in filings:
        node = ("filing", filing.accession_no)
        find(node)
        union(node, ("cik", str(filing.filer_cik)))
        for cik in filing.person_ciks:
            union(node, ("cik", str(cik)))
        if filing.previous_accession in stored:
            union(node, ("filing", str(filing.previous_accession)))
    first: dict[tuple[str, str], StakeFiling] = {}
    for filing in sorted(filings, key=_filed_order):
        first.setdefault(find(("filing", filing.accession_no)), filing)
    return {
        filing.accession_no: first[find(("filing", filing.accession_no))].accession_no
        for filing in filings
    }


def holdings(
    filings: Sequence[StakeFiling], classes: Mapping[str, str], primary: str | None
) -> Holdings:
    """Each holder's latest filing about each class, and what serves every filing now."""
    keys = holder_keys(filings)
    grouped: dict[tuple[str, str | None], list[StakeFiling]] = {}
    for filing in sorted(filings, key=_filed_order, reverse=True):
        grouped.setdefault(
            (keys[filing.accession_no], class_of(filing, classes, primary)), []
        ).append(filing)
    found = []
    serving: dict[str, str] = {}
    for (holder, class_key), listed in grouped.items():
        latest = listed[0]
        found.append(
            Holding(
                holder_key=holder,
                class_key=class_key,
                accession_no=latest.accession_no,
                superseded=tuple(filing.accession_no for filing in listed[1:]),
            )
        )
        for filing in listed:
            serving[filing.accession_no] = latest.accession_no
    return Holdings(tuple(found), serving)


def stake_changes(
    before: Holdings,
    after: Holdings,
    old: Mapping[str, StakeFiling],
    new: Mapping[str, StakeFiling],
) -> list[ValueChange]:
    """The changes a run makes to served stakes: each filing served before it (in `before`,
    over the filings stored then, `old`) whose holder is now served by a later filing, or by
    itself read otherwise, with another amount, percent or exit. Filings first stored by the
    run were never served and change nothing."""
    changes = []
    for accession in sorted(before.served()):
        now = after.serving.get(accession)
        if now is None or accession not in old or now not in new:
            continue
        was, became = stake_of(old[accession]), stake_of(new[now])
        if (was.amount, was.percent, was.exited) == (became.amount, became.percent, became.exited):
            continue
        replaced = now != accession
        changes.append(
            ValueChange(
                entity_key={"accession_no": accession, "served_from": now},
                old_value=was.amount,
                new_value=became.amount,
                reason="amendment" if replaced else "correction",
                detail=_detail(old[accession], new[now], was, became, replaced),
            )
        )
    return changes


def _detail(old: StakeFiling, new: StakeFiling, was: Stake, became: Stake, replaced: bool) -> str:
    change = f"{_stated(was)} to {_stated(became)}"
    if replaced:
        return (
            f"{new.form} {new.accession_no} filed {new.filed_at.isoformat()} replaces "
            f"{old.form} {old.accession_no} filed {old.filed_at.isoformat()}: {change}"
        )
    return f"parser version {old.extractor_version} to {new.extractor_version}: {change}"


def _stated(stake: Stake) -> str:
    exited = ", exited" if stake.exited else ""
    return f"{stake.amount:,f} shares ({stake.percent}% of the class{exited})"


def _filed_order(filing: StakeFiling) -> tuple[date, str]:
    return filing.filed_at, filing.accession_no


def by_accession(filings: Iterable[StakeFiling]) -> dict[str, StakeFiling]:
    return {filing.accession_no: filing for filing in filings}
