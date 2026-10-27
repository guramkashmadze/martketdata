"""The extraction hit-rate report (E7a-S5, PLAN.md §9.9): how the rules did on the stored
earnings releases, per slot and per company, and how many slots needed an override. Its hit
rate decides OD-10, whether to build the LLM fallback (E12).

It reads earnings_releases and earnings_facts as the last run of each release left them. The
hit rate is passed slots over the slots that apply (passed, failed or not found) in releases
with a statement; 8-Ks with item 2.02 that hold no statement are listed for a check by hand.
"""

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from psycopg import AsyncConnection
from pydantic import BaseModel, ConfigDict

from marketdata.db.repositories import companies, earnings, predecessors, securities
from marketdata.db.repositories.earnings import StoredFact, StoredRelease
from marketdata.extract.interface import SLOTS
from marketdata.sec import urls

STATUSES = ("complete", "partial", "failed", "no_statement")


class SlotCounts(BaseModel):
    model_config = ConfigDict(frozen=True)

    passed: int = 0
    failed: int = 0
    not_found: int = 0
    not_applicable: int = 0
    overridden: int = 0  # slots an override pinned to a row or table, or declared not applicable

    @property
    def applicable(self) -> int:
        return self.passed + self.failed + self.not_found

    @property
    def hit_rate(self) -> Decimal | None:
        """Passed slots as a percentage of those that apply, to one decimal."""
        if not self.applicable:
            return None
        rate = Decimal(100 * self.passed) / self.applicable
        return rate.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


class CompanyCounts(BaseModel):
    model_config = ConfigDict(frozen=True)

    cik: int  # the filer: a predecessor's releases are counted under its own CIK
    ticker: str | None  # a predecessor shows the ticker of the company it became
    name: str | None
    successor_cik: int | None  # set for a predecessor (data/predecessors.csv)
    releases: int  # with a statement
    no_statement: int
    slots: SlotCounts  # the four slots of its releases together


class NoStatement(BaseModel):
    model_config = ConfigDict(frozen=True)

    accession_no: str
    cik: int
    ticker: str | None
    accepted_at: datetime | None
    notes: tuple[str, ...]
    url: str  # the filing's index page, to check by hand


class ExtractionReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    releases: int
    statuses: dict[str, int]  # STATUSES order
    rules_versions: tuple[str, ...]
    slots: dict[str, SlotCounts]  # SLOTS order
    total: SlotCounts
    failures: dict[str, int]  # quality flag of each failed slot ("no flag" when it has none)
    overrides: dict[str, int]  # override as described, and how many releases used it
    companies: tuple[CompanyCounts, ...]  # by ticker, then CIK
    no_statement: tuple[NoStatement, ...]


async def extraction_report(conn: AsyncConnection[Any]) -> ExtractionReport:
    releases = await earnings.every_release(conn)
    facts = await earnings.every_fact(conn)
    ciks = sorted({release.cik for release in releases})
    successors = await predecessors.successors(conn, ciks)
    labelled = sorted({*ciks, *successors.values()})
    names = {company.cik: company.name for company in await companies.get_many(conn, labelled)}
    tickers: dict[int, str] = {}
    for security in sorted(
        await securities.list_current_for_ciks(conn, labelled),
        key=lambda row: (row.security_type != "common", row.ticker),
    ):
        tickers.setdefault(security.cik, security.ticker)
    return tally(releases, facts, names=names, tickers=tickers, successors=successors)


def tally(
    releases: Sequence[StoredRelease],
    facts: Iterable[StoredFact],
    *,
    names: Mapping[int, str | None],
    tickers: Mapping[int, str],
    successors: Mapping[int, int],
) -> ExtractionReport:
    by_release: dict[str, list[StoredFact]] = {}
    for fact in facts:
        by_release.setdefault(fact.accession_no, []).append(fact)
    statuses = Counter(release.status for release in releases)
    slots: dict[str, Counter[str]] = {slot: Counter() for slot in SLOTS}
    per_company: dict[int, Counter[str]] = {}
    failures: Counter[str] = Counter()
    overrides: Counter[str] = Counter()
    unread: list[NoStatement] = []

    def ticker(cik: int) -> str | None:
        return tickers.get(cik) or tickers.get(successors.get(cik, 0))

    for release in releases:
        company = per_company.setdefault(release.cik, Counter())
        if release.status == "no_statement":
            company["no_statement"] += 1
            unread.append(
                NoStatement(
                    accession_no=release.accession_no,
                    cik=release.cik,
                    ticker=ticker(release.cik),
                    accepted_at=release.accepted_at,
                    notes=tuple(release.check_report.get("notes", [])),
                    url=urls.filing_index_page(release.cik, release.accession_no),
                )
            )
            continue
        company["releases"] += 1
        used = [
            *release.check_report.get("overrides", []),
            *release.check_report.get("not_applicable_overrides", []),
        ]
        overrides.update(dict.fromkeys(used, 1))
        overridden = {description.split(":", 1)[0] for description in used}
        for fact in by_release.get(release.accession_no, []):
            counts = slots.get(fact.slot)
            if counts is None:
                continue
            for counter in (counts, company):
                counter[fact.check_status] += 1
                counter["overridden"] += int(fact.slot in overridden)
            if fact.check_status == "failed":
                failures.update(fact.quality_flags or ["no flag"])
    by_slot = {slot: _counts(counted) for slot, counted in slots.items()}
    companies_counted = [
        CompanyCounts(
            cik=cik,
            ticker=ticker(cik),
            name=names.get(cik),
            successor_cik=successors.get(cik),
            releases=counted["releases"],
            no_statement=counted["no_statement"],
            slots=_counts(counted),
        )
        for cik, counted in per_company.items()
    ]
    return ExtractionReport(
        releases=len(releases),
        statuses={status: statuses[status] for status in STATUSES},
        rules_versions=tuple(sorted({release.extractor_version for release in releases})),
        slots=by_slot,
        total=_counts(sum(slots.values(), Counter[str]())),
        failures=dict(failures.most_common()),
        overrides=dict(sorted(overrides.items())),
        companies=tuple(
            sorted(companies_counted, key=lambda counted: (counted.ticker or "~", counted.cik))
        ),
        no_statement=tuple(sorted(unread, key=lambda filing: (filing.ticker or "~", filing.url))),
    )


def _counts(counted: Counter[str]) -> SlotCounts:
    return SlotCounts(
        passed=counted["passed"],
        failed=counted["failed"],
        not_found=counted["not_found"],
        not_applicable=counted["not_applicable"],
        overridden=counted["overridden"],
    )
