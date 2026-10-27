"""Metric catalog from PLAN.md §9.3, versioned with CATALOG_VERSION (E4a, D-005).

Pure. Each metric has a kind (a flow over a period, or an instant on a date), a unit and the
XBRL concepts that report it, in priority order: for each period the first concept with a fact
is used (§9.4 rule 3). Concepts are us-gaap unless their taxonomy says dei. A concept may carry
a quality flag that every value taken from it gets (ProfitLoss includes minority interests).

`dei:EntityCommonStockSharesOutstanding` is dated at the cover page, weeks after the period
end; it is counted at the end of the period its filing reports (`cover_page`).

Version 1 is §9.3 plus four concepts the E4a survey of the 60 universe companies called for
(D-082): `RevenuesNetOfInterestExpense` (SoFi's total net revenue), the net income available
to common stockholders (Realty Income tags no NetIncomeLoss), `PaymentsToAcquireProductiveAssets`
(Amazon's, NVIDIA's and Visa's capital expenditure) and
`LongTermDebtAndCapitalLeaseObligations` (Coca-Cola's, ExxonMobil's and Accenture's long-term
debt).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

CATALOG_VERSION = "1"

type Kind = Literal["flow", "instant"]
type Unit = Literal["USD", "shares", "USD/shares"]

# Quality flags set from the concept a value comes from (§9.5).
NET_INCOME_INCLUDES_NCI = "net_income_includes_nci"
NET_INCOME_TO_COMMON = "net_income_to_common"
CASH_INCLUDES_RESTRICTED = "cash_includes_restricted"


@dataclass(frozen=True)
class Concept:
    name: str
    taxonomy: Literal["us-gaap", "dei"] = "us-gaap"
    flag: str | None = None  # set on every value taken from this concept
    cover_page: bool = False  # dated at the cover page: counted at its filing's period end

    @property
    def qualified(self) -> str:
        return self.name if self.taxonomy == "us-gaap" else f"{self.taxonomy}:{self.name}"


@dataclass(frozen=True)
class Metric:
    name: str
    kind: Kind
    unit: Unit
    concepts: tuple[Concept, ...]
    core: bool = False  # the core set (D-020): golden tests and API defaults first
    per_share: bool = False  # per-share or average-share: never derived by subtraction (E4b)
    note: str = ""


def _concepts(*names: str) -> tuple[Concept, ...]:
    return tuple(Concept(name) for name in names)


METRICS: tuple[Metric, ...] = (
    Metric(
        "revenue",
        "flow",
        "USD",
        _concepts(
            "Revenues",
            "RevenuesNetOfInterestExpense",
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "RevenueFromContractWithCustomerIncludingAssessedTax",
            "SalesRevenueNet",
        ),
        core=True,
        note="Banks (SIC 6000-6099) have none: not_applicable_financials (§9.4 rule 10)",
    ),
    Metric(
        "cost_of_revenue",
        "flow",
        "USD",
        _concepts("CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"),
    ),
    Metric(
        "gross_profit",
        "flow",
        "USD",
        _concepts("GrossProfit"),
        note="Else computed as revenue - cost_of_revenue (E4b)",
    ),
    Metric("rnd_expense", "flow", "USD", _concepts("ResearchAndDevelopmentExpense")),
    Metric("sga_expense", "flow", "USD", _concepts("SellingGeneralAndAdministrativeExpense")),
    Metric("operating_income", "flow", "USD", _concepts("OperatingIncomeLoss"), core=True),
    Metric(
        "interest_expense",
        "flow",
        "USD",
        _concepts("InterestExpense", "InterestExpenseNonoperating"),
    ),
    Metric(
        "pretax_income",
        "flow",
        "USD",
        _concepts(
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",  # noqa: E501
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",  # noqa: E501
        ),
    ),
    Metric("income_tax", "flow", "USD", _concepts("IncomeTaxExpenseBenefit")),
    Metric(
        "net_income",
        "flow",
        "USD",
        (
            Concept("NetIncomeLoss"),
            Concept("NetIncomeLossAvailableToCommonStockholdersBasic", flag=NET_INCOME_TO_COMMON),
            Concept("ProfitLoss", flag=NET_INCOME_INCLUDES_NCI),
        ),
        core=True,
        note="Attributable to the company; after preferred dividends when flagged to common",
    ),
    Metric("eps_basic", "flow", "USD/shares", _concepts("EarningsPerShareBasic"), per_share=True),
    Metric(
        "eps_diluted",
        "flow",
        "USD/shares",
        _concepts("EarningsPerShareDiluted"),
        core=True,
        per_share=True,
    ),
    Metric(
        "shares_diluted_avg",
        "flow",
        "shares",
        _concepts("WeightedAverageNumberOfDilutedSharesOutstanding"),
        per_share=True,
        note="Average over the period",
    ),
    Metric(
        "depreciation_amortization",
        "flow",
        "USD",
        _concepts("DepreciationDepletionAndAmortization", "DepreciationAndAmortization"),
    ),
    Metric(
        "operating_cash_flow",
        "flow",
        "USD",
        _concepts("NetCashProvidedByUsedInOperatingActivities"),
        core=True,
        note="10-Q values are usually year-to-date: quarters derived in E4b",
    ),
    Metric(
        "capex",
        "flow",
        "USD",
        _concepts(
            "PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"
        ),
        core=True,
        note="Year-to-date in 10-Qs",
    ),
    Metric(
        "dividends_paid",
        "flow",
        "USD",
        _concepts("PaymentsOfDividends", "PaymentsOfDividendsCommonStock"),
        note="Year-to-date in 10-Qs",
    ),
    Metric(
        "buybacks",
        "flow",
        "USD",
        _concepts("PaymentsForRepurchaseOfCommonStock"),
        note="Year-to-date in 10-Qs",
    ),
    Metric(
        "cash",
        "instant",
        "USD",
        (
            Concept("CashAndCashEquivalentsAtCarryingValue"),
            Concept(
                "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
                flag=CASH_INCLUDES_RESTRICTED,
            ),
        ),
        core=True,
    ),
    Metric("current_assets", "instant", "USD", _concepts("AssetsCurrent")),
    Metric("total_assets", "instant", "USD", _concepts("Assets")),
    Metric("current_liabilities", "instant", "USD", _concepts("LiabilitiesCurrent")),
    Metric(
        "total_liabilities",
        "instant",
        "USD",
        _concepts("Liabilities"),
        note="Else computed (E4b) from LiabilitiesAndStockholdersEquity and total equity",
    ),
    Metric(
        "long_term_debt",
        "instant",
        "USD",
        _concepts(
            "LongTermDebtNoncurrent", "LongTermDebt", "LongTermDebtAndCapitalLeaseObligations"
        ),
        core=True,
    ),
    Metric(
        "total_equity",
        "instant",
        "USD",
        _concepts(
            "StockholdersEquity",
            "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
        ),
        core=True,
    ),
    Metric(
        "shares_outstanding",
        "instant",
        "shares",
        (
            Concept("EntityCommonStockSharesOutstanding", taxonomy="dei", cover_page=True),
            Concept("CommonStockSharesOutstanding"),
        ),
        core=True,
        note="The dei count is as of the cover page, after the period end",
    ),
)

BY_NAME: Mapping[str, Metric] = {metric.name: metric for metric in METRICS}
CORE = frozenset(metric.name for metric in METRICS if metric.core)

# Read by E4b's computed metrics and checks, not served as metrics: the balance check
# (§9.5 balance_mismatch) and total_liabilities' fallback.
SUPPORTING: tuple[Concept, ...] = _concepts(
    "LiabilitiesAndStockholdersEquity",
    "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
)


def concepts_by_taxonomy() -> dict[str, tuple[str, ...]]:
    """Every concept the catalog reads, by taxonomy, in catalog order and without repeats."""
    seen: dict[str, dict[str, None]] = {"us-gaap": {}, "dei": {}}
    for concept in [*(c for metric in METRICS for c in metric.concepts), *SUPPORTING]:
        seen[concept.taxonomy][concept.name] = None
    return {taxonomy: tuple(names) for taxonomy, names in seen.items()}
