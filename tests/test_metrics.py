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


def test_build_registry_buckets_by_month():
    reg = build_registry(_rows())
    # billing_account_name falls back to billing_account ("acme"); series are
    # split per month so June copilot (10+5) and May copilot (12) are distinct.
    base = {
        "provider": "github",
        "billing_account": "acme",
        "billing_account_name": "acme",
        "service": "copilot",
        "currency": "USD",
    }
    jun = {**base, "month": "2026-06"}
    may = {**base, "month": "2026-05"}
    assert reg.get_sample_value(METRIC_NET, jun) == 15.0
    assert reg.get_sample_value(METRIC_LIST, jun) == 15.0
    assert reg.get_sample_value(METRIC_NET, may) == 12.0
    actions = {**base, "service": "actions", "month": "2026-06"}
    assert reg.get_sample_value(METRIC_NET, actions) == 0.0
    assert reg.get_sample_value(METRIC_LIST, actions) == 40.0


def test_build_registry_uses_account_name():
    reg = build_registry([CostRow("aws", "111122223333", "EC2", 0.0, date(2026, 6, 1),
                                  list_cost=50.0, billing_account_name="india1")])
    labels = {"provider": "aws", "billing_account": "111122223333", "billing_account_name": "india1",
              "service": "EC2", "currency": "USD", "month": "2026-06"}
    assert reg.get_sample_value(METRIC_LIST, labels) == 50.0
