"""Push sink , remote_write dated cost samples into Mimir/Prometheus.

A bill is a batch number, so we push (not scrape) and stamp each sample with the
day it represents. remote_write carries an explicit per-sample timestamp, which
is exactly what we need; Pushgateway is deliberately avoided (it strips
timestamps and persists until deleted).

`prometheus-remote-writer` is an optional dependency (install `ducat[push]`); it
is imported lazily so scrape-only users need not pull it in.
"""

from __future__ import annotations

from datetime import datetime, time, timezone

from ..config import SinkConfig, env
from ..focus import CostRow
from ..metrics import METRIC_LIST, METRIC_NET, aggregate_by_period


def _epoch_ms(d) -> int:
    dt = datetime.combine(d, time(12, 0), tzinfo=timezone.utc)  # noon UTC, stable
    return int(dt.timestamp() * 1000)


def push(rows: list[CostRow], sink: SinkConfig) -> int:
    if not sink.remote_write_url:
        raise RuntimeError("remote_write: no sink.remote_write.url configured")
    try:
        from prometheus_remote_writer import RemoteWriter
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "remote_write needs the 'push' extra: pip install 'ducat[push]'"
        ) from exc

    headers = {}
    if sink.tenant:
        headers["X-Scope-OrgID"] = sink.tenant

    auth = None
    if sink.username:
        auth = (sink.username, env(sink.password_env, "") or "")

    writer = RemoteWriter(url=sink.remote_write_url, headers=headers, auth=auth)

    # One sample per (service, month) per metric, stamped at the month it bills to.
    series = []
    for (label, period), (net, gross) in aggregate_by_period(rows).items():
        provider, account, service, currency = label
        base = {
            "provider": provider,
            "billing_account": account,
            "service": service,
            "currency": currency,
        }
        ts = _epoch_ms(period)
        series.append({"metric": {"__name__": METRIC_NET, **base}, "values": [net], "timestamps": [ts]})
        series.append({"metric": {"__name__": METRIC_LIST, **base}, "values": [gross], "timestamps": [ts]})
    writer.send(series)
    return len(series)
