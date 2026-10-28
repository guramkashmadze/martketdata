"""Release-vs-filing cross-check (E4b-S6, D-028, PLAN.md §9.10): each earnings release's slots
against the 10-Q or 10-K for the same quarter.

A company's releases are checked together with its predecessors' (D-068): XOM's releases to
the first quarter of 2026 are under Exxon Mobil Corp (34088), and its 10-Q for the second
quarter under ExxonMobil Holdings (2115436). The check runs

- after a facts refresh, when a new 10-Q or 10-K may have brought a quarter's values;
- after an earnings release is stored, so a release whose filing is already loaded (a re-run,
  an 8-K/A) is checked at once, and a re-extracted value is never left with an old result;
- for every company in `marketdata normalize --all`.

It reads stored rows only and writes the slots whose result changed: `cross_check_status`,
`cross_check_value` (the filing's value), `cross_check_accession` (that filing) and the flag
`release_vs_filing_mismatch`. A release's own values never change (normalize/cross_check.py,
verify/cross_check.py).
"""

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass

import structlog

from marketdata.db.repositories import companies, earnings, filings, predecessors, xbrl_facts
from marketdata.db.repositories.earnings import CrossCheckRow
from marketdata.ingest.fetch import Connect
from marketdata.normalize.cross_check import (
    SLOT_CONCEPTS,
    PeriodicFiling,
    ReleaseSlots,
    check_releases,
)
from marketdata.normalize.filings import PERIODIC_FORMS
from marketdata.verify.cross_check import MISMATCH, flags_after

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class CrossCheckReport:
    releases: int
    statuses: Mapping[str, int]  # slots by cross-check status
    written: int  # slots whose cross-check changed


async def cross_check_company(connect: Connect, cik: int) -> CrossCheckReport:
    """Check the releases of `cik`'s company: `cik` itself, or the company it became, and that
    company's predecessors."""
    async with connect() as conn:
        root = (await predecessors.successors(conn, [cik])).get(cik, cik)
        group = [root, *await predecessors.predecessors_of(conn, root)]
        company = await companies.get(conn, root)
        releases = await earnings.list_releases(conn, group, limit=100_000)
        stored = await earnings.facts_of(conn, [release.accession_no for release in releases])
        facts = [
            fact
            for member in group
            for fact in await xbrl_facts.for_concepts(conn, member, SLOT_CONCEPTS)
        ]
        listed = await filings.of_forms(conn, group, sorted(PERIODIC_FORMS))
    by_release: dict[str, list[earnings.StoredFact]] = {}
    for fact in stored:
        by_release.setdefault(fact.accession_no, []).append(fact)
    slots = [
        ReleaseSlots(
            accession_no=release.accession_no,
            accepted=release.accepted_at.date() if release.accepted_at else None,
            period_end=release.period_end,
            period_label=release.period_label,
            values={
                fact.slot: (fact.unit, fact.value if fact.check_status == "passed" else None)
                for fact in by_release.get(release.accession_no, [])
            },
        )
        for release in releases
    ]
    periodic = [
        PeriodicFiling(filing.accession_no, filing.form, filing.filed_at, filing.period_of_report)
        for filing in listed
    ]
    checks = check_releases(
        slots,
        facts,
        periodic,
        fiscal_year_end=company.fiscal_year_end if company else None,
    )
    current = {(fact.accession_no, fact.slot): fact for fact in stored}
    changed: list[CrossCheckRow] = []
    statuses: Counter[str] = Counter()
    for check in checks:
        for slot in check.slots:
            statuses[slot.status] += 1
            fact = current[(check.accession_no, slot.slot)]
            row = CrossCheckRow(
                check.accession_no,
                slot.slot,
                slot.status,
                slot.filing_value,
                slot.filing_accession,
                flags_after(fact.quality_flags, slot.status),
            )
            before = (
                fact.cross_check_status,
                fact.cross_check_value,
                fact.cross_check_accession,
                tuple(fact.quality_flags),
            )
            if before != (row.status, row.value, row.accession, row.flags):
                changed.append(row)
            if slot.status == MISMATCH and fact.cross_check_status != MISMATCH:
                log.warning(
                    "release_vs_filing_mismatch",
                    cik=root,
                    accession_no=check.accession_no,
                    slot=slot.slot,
                    release=str(fact.value),
                    filing=str(slot.filing_value),
                    filing_accession=slot.filing_accession,
                )
    async with connect() as conn, conn.transaction():
        written = await earnings.set_cross_checks(conn, changed)
    log.info(
        "releases_cross_checked",
        cik=root,
        releases=len(checks),
        written=written,
        **{status: count for status, count in sorted(statuses.items())},
    )
    return CrossCheckReport(len(checks), dict(statuses), written)


async def cross_check_all(connect: Connect) -> CrossCheckReport:
    """Every company with stored releases, each with its predecessors."""
    async with connect() as conn:
        releases = await earnings.every_release(conn)
        ciks = sorted({release.cik for release in releases})
        successors = await predecessors.successors(conn, ciks)
    roots = sorted({successors.get(cik, cik) for cik in ciks})
    reports = [await cross_check_company(connect, root) for root in roots]
    statuses: Counter[str] = Counter()
    for report in reports:
        statuses.update(report.statuses)
    return CrossCheckReport(
        sum(report.releases for report in reports),
        dict(statuses),
        sum(report.written for report in reports),
    )
