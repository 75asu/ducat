from datetime import date

from ducat.focus import CostRow
from ducat.metrics import (
    METRIC_LIST,
    METRIC_NET,
    aggregate,
    aggregate_by_period,
    build_registry,
)


def _rows():
    return [
        CostRow("github", "acme", "copilot", 10.0, date(2026, 6, 1), list_cost=10.0),
        CostRow("github", "acme", "copilot", 5.0, date(2026, 6, 1), list_cost=5.0),
        CostRow("github", "acme", "actions", 0.0, date(2026, 6, 1), list_cost=40.0),
        CostRow("github", "acme", "copilot", 12.0, date(2026, 5, 1), list_cost=12.0),
    ]


def test_aggregate_sums_net_and_list():
    agg = aggregate(_rows())
    assert agg[("github", "acme", "copilot", "USD")] == (27.0, 27.0)  # 10+5+12
    assert agg[("github", "acme", "actions", "USD")] == (0.0, 40.0)


def test_aggregate_by_period_splits_months():
    agg = aggregate_by_period(_rows())
    assert agg[(("github", "acme", "copilot", "USD"), date(2026, 6, 1))] == (15.0, 15.0)
    assert agg[(("github", "acme", "copilot", "USD"), date(2026, 5, 1))] == (12.0, 12.0)


def test_build_registry_exposes_both_gauges():
    reg = build_registry(_rows())
    labels = {"provider": "github", "billing_account": "acme", "service": "copilot", "currency": "USD"}
    assert reg.get_sample_value(METRIC_NET, labels) == 27.0
    assert reg.get_sample_value(METRIC_LIST, labels) == 27.0
    actions = {**labels, "service": "actions"}
    assert reg.get_sample_value(METRIC_NET, actions) == 0.0
    assert reg.get_sample_value(METRIC_LIST, actions) == 40.0
