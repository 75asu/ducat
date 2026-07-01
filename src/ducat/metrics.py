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

# Scrape (serve) mode keeps a `month` label so a single scrape carries every
# month's cost, and a `billing_account_name` so multi-account/project views are
# readable. A pull-based exporter can't backdate samples, so the time dimension
# lives as a label; Grafana filters/aggregates on it.
SCRAPE_LABELS = ("provider", "billing_account", "billing_account_name", "service", "currency", "month")

LabelKey = tuple[str, str, str, str]


def _key(row: CostRow) -> LabelKey:
    return (row.provider, row.billing_account, row.service, row.currency)


def _month(d: date) -> str:
    return d.strftime("%Y-%m")


def _account_name(row: CostRow) -> str:
    return row.billing_account_name or row.billing_account


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


def aggregate_by_month(
    rows: list[CostRow],
) -> dict[tuple[str, str, str, str, str, str], tuple[float, float]]:
    """Sum (net, list) per (provider, account, account_name, service, currency, month).

    One series per month so a single scrape exposes the whole history; Grafana
    slices by the `month` label.
    """
    out: dict[tuple[str, str, str, str, str, str], list[float]] = defaultdict(lambda: [0.0, 0.0])
    for row in rows:
        key = (
            row.provider,
            row.billing_account,
            _account_name(row),
            row.service,
            row.currency,
            _month(row.period_start),
        )
        acc = out[key]
        acc[0] += row.billed_cost
        acc[1] += row.list_cost
    return {k: (v[0], v[1]) for k, v in out.items()}


def build_registry(rows: list[CostRow]) -> CollectorRegistry:
    """A fresh registry with net + list cost per (label set, month) for scrape mode."""
    registry = CollectorRegistry()
    g_net = Gauge(METRIC_NET, "Billed (net) cost in USD by provider/account/service/month.", SCRAPE_LABELS, registry=registry)
    g_list = Gauge(METRIC_LIST, "List (pre-discount) cost in USD by provider/account/service/month.", SCRAPE_LABELS, registry=registry)
    for labels, (net, gross) in aggregate_by_month(rows).items():
        g_net.labels(*labels).set(net)
        g_list.labels(*labels).set(gross)
    return registry
