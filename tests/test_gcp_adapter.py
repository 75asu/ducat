"""GCP adapter: table naming, window defaults, and the FOCUS -> CostRow mapping.

Uses a stubbed BigQuery client so the test needs no credentials and no network.
"""

from __future__ import annotations

import datetime as dt
import sys
import types

import pytest

from ducat.adapters import available, get
from ducat.adapters.gcp import GcpAdapter


def _install_fake_bigquery(rows, captured):
    """Register a fake google.cloud.bigquery so the adapter's import succeeds."""

    class _Param:
        def __init__(self, name, _type, value):
            self.name, self.value = name, value

    class _JobConfig:
        def __init__(self, query_parameters=None, maximum_bytes_billed=None):
            self.query_parameters = query_parameters or []
            self.maximum_bytes_billed = maximum_bytes_billed

    class _Job:
        def result(self):
            return rows

    class _Client:
        def __init__(self, project=None):
            captured["project"] = project

        def query(self, sql, job_config=None):
            captured["sql"] = sql
            captured["job_config"] = job_config
            return _Job()

    bq = types.SimpleNamespace(
        Client=_Client, QueryJobConfig=_JobConfig, ScalarQueryParameter=_Param
    )
    google = types.ModuleType("google")
    cloud = types.ModuleType("google.cloud")
    cloud.bigquery = bq  # type: ignore[attr-defined]
    google.cloud = cloud  # type: ignore[attr-defined]
    sys.modules["google"] = google
    sys.modules["google.cloud"] = cloud
    sys.modules["google.cloud.bigquery"] = bq  # type: ignore[assignment]


def test_registered():
    assert "gcp" in available()
    assert isinstance(get("gcp"), GcpAdapter)


def test_table_assembled_from_parts_focus():
    t = GcpAdapter()._table(
        {"project": "p", "dataset": "d", "billing_account_id": "0A0A0A-0B0B0B-0C0C0C"}
    )
    # hyphens become underscores in the table suffix
    assert t == "p.d.gcp_billing_export_focus_0A0A0A_0B0B0B_0C0C0C"


def test_table_assembled_standard():
    t = GcpAdapter()._table(
        {"project": "p", "dataset": "d", "billing_account_id": "AAA-BBB", "source": "standard"}
    )
    assert t == "p.d.gcp_billing_export_v1_AAA_BBB"


def test_explicit_table_wins():
    assert GcpAdapter()._table({"table": "x.y.z"}) == "x.y.z"


def test_missing_parts_is_a_clear_error():
    with pytest.raises(RuntimeError, match="project"):
        GcpAdapter()._table({"dataset": "d"})


def test_bad_source_rejected():
    with pytest.raises(RuntimeError, match=r"focus.*standard"):
        GcpAdapter().fetch({"table": "x.y.z", "source": "nope"})


def test_window_rejects_inverted_range():
    with pytest.raises(RuntimeError, match="after"):
        GcpAdapter()._window({"from": "2026-08-01", "to": "2026-07-01"})


def test_focus_rows_map_to_costrow():
    captured: dict = {}
    _install_fake_bigquery(
        [
            {
                "day": dt.date(2026, 7, 15),
                "billing_account": "0A0A0A-0B0B0B-0C0C0C",
                "account_name": "acme-prod",
                "sub_account": "acme-prod",
                "service": "Compute Engine",
                "region": "us-west4",
                "sku": "ABC-123",
                "currency": "USD",
                "billed_cost": 0.0,  # credit-funded: nothing payable
                "list_cost": 4321.5,  # but real consumption at list rate
            }
        ],
        captured,
    )
    rows = GcpAdapter().fetch(
        {"table": "p.d.t", "source": "focus", "from": "2026-07-01", "to": "2026-07-31"}
    )
    assert len(rows) == 1
    r = rows[0]
    assert r.provider == "gcp"
    assert r.service == "Compute Engine"
    assert r.billed_cost == 0.0
    assert r.list_cost == 4321.5  # the number that matters on credits
    assert r.period_start == dt.date(2026, 7, 15)
    assert r.sub_account == "acme-prod"
    assert r.region == "us-west4"
    # partition pruning must be present or a scrape scans the whole table
    assert "ChargePeriodStart" in captured["sql"]
    assert captured["job_config"].maximum_bytes_billed > 0


def test_standard_sql_unnests_credits():
    captured: dict = {}
    _install_fake_bigquery([], captured)
    GcpAdapter().fetch({"table": "p.d.t", "source": "standard"})
    sql = captured["sql"]
    # billed_cost must add the (negative) credits, else every number is inflated
    assert "UNNEST(credits)" in sql
    assert "usage_start_time" in sql


def test_query_failure_is_isolated_not_fatal():
    """A broken billing account must not blank the whole board."""
    captured: dict = {}
    _install_fake_bigquery([], captured)
    import google.cloud.bigquery as bq  # type: ignore

    class _Boom:
        def __init__(self, project=None):
            pass

        def query(self, *a, **k):
            raise RuntimeError("permission denied")

    bq.Client = _Boom  # type: ignore[attr-defined]
    assert GcpAdapter().fetch({"table": "p.d.t"}) == []
