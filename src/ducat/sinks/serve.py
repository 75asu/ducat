"""Scrape sink , long-running /metrics endpoint.

The conventional exporter UX: ducat holds the current period's cost as gauges
and Prometheus/Alloy scrapes it. Values refresh on a timer (cost APIs are
daily-fresh, so a slow interval is plenty and keeps per-request billing cheap).
"""

from __future__ import annotations

import time
from wsgiref.simple_server import make_server

from prometheus_client import make_wsgi_app
from prometheus_client.core import REGISTRY

from .. import adapters as adapters_mod
from ..config import Config
from ..metrics import build_registry


class _Refresher:
    """A collector that swaps in a freshly-built registry on each refresh tick."""

    def __init__(self) -> None:
        self._registry = build_registry([])

    def collect(self):
        yield from self._registry.collect()

    def refresh(self, rows) -> None:
        self._registry = build_registry(rows)


def _fetch_all(cfg: Config) -> list:
    rows = []
    for name, opts in cfg.enabled_providers().items():
        rows.extend(adapters_mod.get(name).fetch(opts))
    return rows


def serve(cfg: Config, port: int = 9090, interval: int = 3600) -> None:
    refresher = _Refresher()
    REGISTRY.register(refresher)

    def _tick() -> None:
        try:
            # All months: build_registry buckets them under the `month` label so a
            # single scrape exposes the full history for Grafana to slice.
            refresher.refresh(_fetch_all(cfg))
        except Exception as exc:  # keep serving last-good values on a blip
            print(f"ducat: refresh failed: {exc}", flush=True)

    _tick()
    app = make_wsgi_app()
    httpd = make_server("", port, app)
    print(f"ducat: serving /metrics on :{port} (refresh every {interval}s)", flush=True)

    last = time.monotonic()
    httpd.timeout = 1
    while True:
        httpd.handle_request()
        if time.monotonic() - last >= interval:
            _tick()
            last = time.monotonic()
