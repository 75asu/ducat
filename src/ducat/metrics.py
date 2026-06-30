"""Project normalized CostRows onto Prometheus metrics.

Two gauges, same label set (provider / billing_account / service / currency):
  ducat_cost_usd       , BilledCost (what you actually pay, after discounts)
  ducat_list_cost_usd  , ListCost  (pre-discount / public rate , the waste signal)

We keep the label set small on purpose; cost dashboards slice by those four, and
every extra label multiplies cardinality.

Scrape (`serve`) mode sums per label set and exposes the current value. One-shot
(`run`) mode aggregates per (label set, period) and the remote_write sink stamps
each with its own month, producing a monthly time series.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date

from prometheus_client import CollectorRegistry, Gauge

from .focus import CostRow

METRIC_NET = "ducat_cost_usd"
METRIC_LIST = "ducat_list_cost_usd"
LABELS = ("provider", "billing_account", "service", "currency")

LabelKey = tuple[str, str, str, str]


def _key(row: CostRow) -> LabelKey:
    return (row.provider, row.billing_account, row.service, row.currency)


def aggregate(rows: list[CostRow]) -> dict[LabelKey, tuple[float, float]]:
    """Sum (net, list) per label set, across all periods."""
    out: dict[LabelKey, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for row in rows:
        acc = out[_key(row)]
        acc[0] += row.billed_cost
        acc[1] += row.list_cost
    return {k: (v[0], v[1]) for k, v in out.items()}


def aggregate_by_period(rows: list[CostRow]) -> dict[tuple[LabelKey, date], tuple[float, float]]:
    """Sum (net, list) per (label set, period) , one point per service per month."""
    out: dict[tuple[LabelKey, date], list[float]] = defaultdict(lambda: [0.0, 0.0])
    for row in rows:
        acc = out[(_key(row), row.period_start)]
        acc[0] += row.billed_cost
        acc[1] += row.list_cost
    return {k: (v[0], v[1]) for k, v in out.items()}


def build_registry(rows: list[CostRow]) -> CollectorRegistry:
    """A fresh registry with the summed net + list cost for the given rows (scrape mode)."""
    registry = CollectorRegistry()
    g_net = Gauge(METRIC_NET, "Billed (net) cost in USD by provider/service.", LABELS, registry=registry)
    g_list = Gauge(METRIC_LIST, "List (pre-discount) cost in USD by provider/service.", LABELS, registry=registry)
    for (provider, account, service, currency), (net, gross) in aggregate(rows).items():
        g_net.labels(provider, account, service, currency).set(net)
        g_list.labels(provider, account, service, currency).set(gross)
    return registry
