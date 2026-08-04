"""Tests for the GCP Console "Cost table" CSV importer.

These are written against the real file shape observed in an August 2026 export,
including the two traps that produce plausible-but-wrong dashboards:

  1. the trailing `Total` row, which double-counts the whole file if not skipped
  2. a gross-only export (savings unticked), where reporting billed == list claims
     a bill was paid in full that credits had actually absorbed
"""

from __future__ import annotations

from datetime import date

import pytest

from ducat.adapters import gcp_csv
from ducat.adapters.gcp import GcpAdapter
from ducat.focus import CostRow

HEADER = (
    "Billing account name,Billing account ID,Project name,Project ID,Project hierarchy,"
    "Service description,Service ID,SKU description,SKU ID,Consumption model description,"
    "Credit type,Cost type,Usage start date,Usage end date,Usage amount,Usage unit,"
    "Unrounded Cost ($),Cost ($)"
)

PREAMBLE = """Invoice number,1234567890,
Invoice date,2026-06-30,
Due date,2026-07-30,
Billing ID,0000-0000-0000,
Billing account ID,0A0A0A-0B0B0B-0C0C0C,
Currency,USD,
Currency exchange rate,1,
Total amount due,$5.72,
"""


def _gross_only() -> str:
    """A savings-unticked export: usage rows only, empty Credit type."""
    return "\n".join(
        [
            PREAMBLE.rstrip("\n"),
            HEADER,
            # thousands separator in the usage column, real SKU
            (
                "Example Billing Account,0A0A0A-0B0B0B-0C0C0C,acme-prod,acme-prod,example.com,"
                "Compute Engine,6F81,E2 Instance Core running in Las Vegas,ABC1,Default,,Usage,"
                '2026-06-01,2026-06-30,"6,282.708",hour,1000.005,1000.01'
            ),
            (
                "Example Billing Account,0A0A0A-0B0B0B-0C0C0C,acme-sandbox,acme-dev2,example.com,"
                "Cloud SQL,9662,Enterprise Plus N RAM in Iowa,DEF2,Default,,Usage,"
                "2026-06-01,2026-06-30,10,gibibyte,500.5,500.50"
            ),
            # spans a month boundary: month must still come from the invoice date
            (
                "Example Billing Account,0A0A0A-0B0B0B-0C0C0C,acme-prod,acme-prod,example.com,"
                "Security Command Center,FBF2,SCC Premium for Compute Engine,GHI3,Default,,Usage,"
                "2026-05-30,2026-06-29,100,hour,9.49,9.49"
            ),
            # the two summary rows that must be excluded
            ",,,,,,,,,,,Rounding error,,,,,-0.003441,-0.00",
            ",,,,,,,,,,,Total,,,,,1509.995,1510.00",
            "",
        ]
    )


def _credit_itemized() -> str:
    """A savings-ticked export: negative rows carrying Credit type."""
    return "\n".join(
        [
            PREAMBLE.rstrip("\n"),
            HEADER,
            (
                "Example Billing Account,0A0A0A-0B0B0B-0C0C0C,acme-prod,acme-prod,example.com,"
                "Compute Engine,6F81,E2 Instance Core running in Las Vegas,ABC1,Default,,Usage,"
                "2026-06-01,2026-06-30,100,hour,1000.00,1000.00"
            ),
            (
                "Example Billing Account,0A0A0A-0B0B0B-0C0C0C,acme-prod,acme-prod,example.com,"
                "Compute Engine,6F81,E2 Instance Core running in Las Vegas,ABC1,Default,"
                "Promotional credit,Usage,2026-06-01,2026-06-30,100,hour,-999.00,-999.00"
            ),
            "",
        ]
    )


def _write(tmp_path, name: str, body: str):
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


# -- the double-count trap ----------------------------------------------------


def test_total_and_rounding_rows_are_excluded(tmp_path):
    """Including the Total row doubles the file. It has to be dropped."""
    rows = gcp_csv.read_file(_write(tmp_path, "june.csv", _gross_only()))
    usage = [r for r in rows if r.service != gcp_csv.NET_ONLY_SERVICE]
    gross = sum(r.list_cost for r in usage)
    assert gross == pytest.approx(1000.005 + 500.5 + 9.49)
    # the file's own Total row says 1509.995; if we had summed it in we'd see ~3020
    assert gross < 1600, f"Total/Rounding rows leaked into the sum: {gross}"
    assert not any(r.service in ("Total", "Rounding error") for r in rows)


def test_unrounded_column_is_preferred(tmp_path):
    """Per-row rounding drifts the month total, so use the unrounded figure."""
    rows = gcp_csv.read_file(_write(tmp_path, "june.csv", _gross_only()))
    compute = next(r for r in rows if r.service == "Compute Engine")
    assert compute.list_cost == pytest.approx(1000.005)  # not 1000.01


def test_thousands_separators_and_currency_symbols_parse():
    assert gcp_csv._num('"6,282.708"'.strip('"')) == pytest.approx(6282.708)
    assert gcp_csv._num("$5.72") == pytest.approx(5.72)
    assert gcp_csv._num("") == 0.0
    assert gcp_csv._num("-") == 0.0
    assert gcp_csv._num(None) == 0.0


# -- month attribution --------------------------------------------------------


def test_month_comes_from_invoice_date_not_usage_dates(tmp_path):
    """An SCC row billing 2026-05-30 -> 2026-06-29 belongs to the June invoice."""
    rows = gcp_csv.read_file(_write(tmp_path, "june.csv", _gross_only()))
    assert {r.period_start for r in rows} == {date(2026, 6, 1)}
    scc = next(r for r in rows if r.service == "Security Command Center")
    assert scc.period_start == date(2026, 6, 1), "usage start date leaked into the month"


def test_missing_invoice_date_is_an_error(tmp_path):
    body = _gross_only().replace("Invoice date,2026-06-30,", "Invoice date,,")
    with pytest.raises(ValueError, match="Invoice date"):
        gcp_csv.read_file(_write(tmp_path, "bad.csv", body))


def test_not_a_cost_table_is_an_error(tmp_path):
    with pytest.raises(ValueError, match="Cost table"):
        gcp_csv.read_file(_write(tmp_path, "wrong.csv", "a,b,c\n1,2,3\n"))


# -- the credits trap ---------------------------------------------------------


def test_gross_only_file_does_not_claim_the_bill_was_paid(tmp_path):
    """No credit rows means per-service net is unknown, so it must not equal list."""
    rows = gcp_csv.read_file(_write(tmp_path, "june.csv", _gross_only()))
    usage = [r for r in rows if r.service != gcp_csv.NET_ONLY_SERVICE]
    assert all(r.billed_cost == 0.0 for r in usage), (
        "a gross-only export reported billed == list, which would show a fully paid "
        "bill that credits had absorbed"
    )

    # the invoice net is carried exactly once, at account level
    net_rows = [r for r in rows if r.service == gcp_csv.NET_ONLY_SERVICE]
    assert len(net_rows) == 1
    assert net_rows[0].billed_cost == pytest.approx(5.72)
    assert net_rows[0].list_cost == 0.0
    assert net_rows[0].sub_account == ""

    # so the three headline figures are right
    assert sum(r.list_cost for r in rows) == pytest.approx(1000.005 + 500.5 + 9.49)
    assert sum(r.billed_cost for r in rows) == pytest.approx(5.72)


def test_credit_rows_net_out_per_service(tmp_path):
    rows = gcp_csv.read_file(_write(tmp_path, "june.csv", _credit_itemized()))
    assert not any(r.service == gcp_csv.NET_ONLY_SERVICE for r in rows), (
        "an itemized file should not need the account-level fallback"
    )
    assert sum(r.list_cost for r in rows) == pytest.approx(1000.00)
    assert sum(r.billed_cost for r in rows) == pytest.approx(1.00)


# -- directory handling -------------------------------------------------------


def test_missing_dir_is_not_an_error(tmp_path):
    assert gcp_csv.read_dir(tmp_path / "nope") == []


def test_empty_dir_is_not_an_error(tmp_path):
    assert gcp_csv.read_dir(tmp_path) == []


def test_same_month_twice_is_not_double_counted(tmp_path):
    """Exporting a month both with and without savings must not double it."""
    _write(tmp_path, "june-gross.csv", _gross_only())
    _write(tmp_path, "june-net.csv", _credit_itemized())
    rows = gcp_csv.read_dir(tmp_path)
    assert {r.period_start for r in rows} == {date(2026, 6, 1)}
    # the itemized file wins, so list is its 1000.00 rather than the sum of both
    assert sum(r.list_cost for r in rows) == pytest.approx(1000.00)


def test_multiple_months_each_land_once(tmp_path):
    _write(tmp_path, "june.csv", _gross_only())
    may = _gross_only().replace("Invoice date,2026-06-30,", "Invoice date,2026-05-31,")
    _write(tmp_path, "may.csv", may)
    rows = gcp_csv.read_dir(tmp_path)
    assert {r.period_start for r in rows} == {date(2026, 5, 1), date(2026, 6, 1)}


# -- adapter integration: CSV wins over partial BigQuery ----------------------


def test_adapter_drops_bigquery_months_the_csv_covers(tmp_path, monkeypatch):
    """BigQuery's edge months are partial; adding them to a full invoice inflates it."""
    _write(tmp_path, "june.csv", _gross_only())

    partial_june = CostRow(
        provider="gcp",
        billing_account="0A0A0A-0B0B0B-0C0C0C",
        service="Compute Engine",
        billed_cost=0.0,
        list_cost=365.06,  # the real partial figure BigQuery had for June
        period_start=date(2026, 6, 30),
        sub_account="acme-prod",
    )
    july = CostRow(
        provider="gcp",
        billing_account="0A0A0A-0B0B0B-0C0C0C",
        service="Compute Engine",
        billed_cost=0.0,
        list_cost=568.77,
        period_start=date(2026, 7, 1),
        sub_account="acme-prod",
    )

    adapter = GcpAdapter()
    monkeypatch.setattr(adapter, "_fetch_bigquery", lambda opts: [partial_june, july])
    rows = adapter.fetch({"csv_dir": str(tmp_path), "dataset": "x"})

    june = [r for r in rows if r.period_start.month == 6]
    assert all(r.list_cost != pytest.approx(365.06) for r in june), (
        "partial BigQuery June survived alongside the full June invoice"
    )
    assert sum(r.list_cost for r in june) == pytest.approx(1000.005 + 500.5 + 9.49)

    # July is outside the CSV window, so it must be kept
    assert any(r.list_cost == pytest.approx(568.77) for r in rows)


def test_adapter_works_with_no_csv_dir(monkeypatch):
    """The common deployment has no history to import and must be unaffected."""
    row = CostRow(
        provider="gcp",
        billing_account="acct",
        service="Compute Engine",
        billed_cost=1.0,
        list_cost=2.0,
        period_start=date(2026, 7, 1),
    )
    adapter = GcpAdapter()
    monkeypatch.setattr(adapter, "_fetch_bigquery", lambda opts: [row])
    assert adapter.fetch({"dataset": "x"}) == [row]


def test_row_narrower_than_header_is_refused(tmp_path):
    """A misaligned row must fail loudly, not get silently truncated.

    Every row in real exports is exactly header-width. If that stops being true the
    format has moved, and zipping a short row would drop or misalign columns of a
    billing file, understating the month. Refusing to load is the safer failure.
    """
    body = _gross_only().replace(
        "2026-06-01,2026-06-30,10,gibibyte,500.5,500.50",
        "2026-06-01,2026-06-30,10,gibibyte,500.5",  # one field short
    )
    with pytest.raises(ValueError, match="fields but the header has"):
        gcp_csv.read_file(_write(tmp_path, "short.csv", body))
