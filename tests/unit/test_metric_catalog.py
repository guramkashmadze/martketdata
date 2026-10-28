"""E4a-S2: the metric catalog (PLAN.md §9.3) in code, versioned, with the core set marked."""

from marketdata.normalize.metric_catalog import (
    BY_NAME,
    CATALOG_VERSION,
    CORE,
    METRICS,
    concepts_by_taxonomy,
)


def test_every_metric_has_a_concept_a_kind_and_a_unit() -> None:
    for metric in METRICS:
        assert metric.concepts, metric.name
        assert metric.kind in ("flow", "instant"), metric.name
        assert metric.unit in ("USD", "shares", "USD/shares"), metric.name
    assert len(BY_NAME) == len(METRICS) == 26
    assert CATALOG_VERSION == "2"  # version 2 adds the computed metrics (E4b)


def test_the_core_set_is_the_one_d020_names() -> None:
    assert CORE == {
        "revenue",
        "operating_income",
        "net_income",
        "eps_diluted",
        "operating_cash_flow",
        "capex",
        "cash",
        "long_term_debt",
        "total_equity",
        "shares_outstanding",
    }


def test_concepts_keep_the_order_of_section_9_3_with_the_survey_additions() -> None:
    names = [concept.name for concept in BY_NAME["net_income"].concepts]
    revenue = [concept.name for concept in BY_NAME["revenue"].concepts]

    assert names == [
        "NetIncomeLoss",
        "NetIncomeLossAvailableToCommonStockholdersBasic",  # Realty Income (D-082)
        "ProfitLoss",
    ]
    assert revenue[:2] == ["Revenues", "RevenuesNetOfInterestExpense"]  # SoFi (D-082)
    assert [c.flag for c in BY_NAME["net_income"].concepts] == [
        None,
        "net_income_to_common",
        "net_income_includes_nci",
    ]
    shares = BY_NAME["shares_outstanding"].concepts[0]
    assert (shares.qualified, shares.cover_page) == ("dei:EntityCommonStockSharesOutstanding", True)


def test_the_concept_list_has_each_concept_once() -> None:
    listed = concepts_by_taxonomy()

    assert listed["dei"] == ("EntityCommonStockSharesOutstanding",)
    assert len(listed["us-gaap"]) == len(set(listed["us-gaap"]))
    assert "LiabilitiesAndStockholdersEquity" in listed["us-gaap"]  # read by E4b's checks
