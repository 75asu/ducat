"""Read GCP Console "Cost table" CSV exports into CostRows.

WHY THIS EXISTS, and why it is not a general-purpose feature:

GCP's BigQuery billing export is forward-only. When you enable it, Google backfills
the current month plus the previous month for multi-region datasets and nothing more
(https://docs.cloud.google.com/billing/docs/how-to/export-data-bigquery). Every month
before that window is permanently unavailable from BigQuery, and GCP has no cost API
to fall back on: the Cloud Billing API v1 has no method that returns cost or usage,
unlike AWS Cost Explorer's GetCostAndUsage.

So for an estate that enabled the export late, the only source for history is the
Console's Cost table CSV. That is a one-time import covering a fixed window that can
never grow, not a recurring ingest. Once the BigQuery export's coverage reaches back
to meet the newest CSV month, this path stops contributing anything.

FILE SHAPE (Console export format, verified August 2026):

    Invoice number,<id>,                <- 8 lines of key/value preamble
    ...
    Total amount due,$12.34,            <- the NET, after credits
    Billing account name,Billing account ID,Project name,...   <- the real header
    <account>,<account-id>,<project>,...                       <- charge rows
    ,,,,,,,,,,,Rounding error,,,,,-0.003441,-0.00              <- must be skipped
    ,,,,,,,,,,,Total,,,,,1000.000000,1000.00                   <- must be skipped

The trailing `Total` row restates the whole file, so counting it doubles the month
exactly. That is the sort of error that looks entirely plausible on a dashboard,
which is why it has a dedicated test.

GROSS-ONLY vs FULL FILES: the Console's "Other savings" checkboxes decide whether
credit rows are present. Unticked gives gross usage rows and an empty `Credit type`
on every row; ticked adds negative rows with `Credit type` populated. We handle both,
because a gross-only file that silently reported billed == list would claim the bill
was paid in full when credits had actually absorbed all of it.
"""

from __future__ import annotations

import csv
import pathlib
import sys
from collections import defaultdict
from datetime import date

from ..focus import CostRow


def _say(msg: str) -> None:
    """Operational note to stderr.

    Matches gcp.py rather than using `logging`: ducat's entrypoint configures no
    handlers, so log.info() lands nowhere and `make local-logs` shows an import
    that looks like it never happened.
    """
    print(f"ducat: gcp-csv: {msg}", file=sys.stderr)


_HEADER_FIRST_CELL = "Billing account name"

# `Cost type` values that are file-level summaries, not charges.
_SUMMARY_COST_TYPES = {"Total", "Rounding error"}

# Marker service for the account-level net of a gross-only file. Deliberately
# unmistakable in a service breakdown: it is not a GCP service, and its presence
# tells the reader that per-service credits were not available for that month.
NET_ONLY_SERVICE = "[invoice net, credits not itemized]"


def _num(raw: str | None) -> float:
    """Parse a Console money/usage cell. Handles '1,234.56', '$5.72', '' and '-'."""
    if not raw:
        return 0.0
    s = raw.strip().replace(",", "").replace("$", "")
    if not s or s == "-":
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def _parse_one(path: pathlib.Path) -> tuple[dict[str, str], list[dict[str, str]]]:
    """Split a Cost table CSV into its preamble and its charge rows."""
    with path.open(newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.reader(fh))

    header_idx = next(
        (i for i, r in enumerate(rows) if r and r[0].strip() == _HEADER_FIRST_CELL),
        None,
    )
    if header_idx is None:
        raise ValueError(
            f"{path.name}: no header row starting {_HEADER_FIRST_CELL!r}. "
            "Is this a Cost table export, or a Reports/Cost breakdown CSV?"
        )

    preamble = {r[0].strip(): r[1].strip() for r in rows[:header_idx] if len(r) >= 2}
    header = [h.strip() for h in rows[header_idx]]
    charges = []
    for lineno, r in enumerate(rows[header_idx + 1 :], start=header_idx + 2):
        if not any(c.strip() for c in r):
            continue
        if len(r) != len(header):
            # strict: a row that is not header-width means the export format moved.
            # Zipping it anyway would drop or misalign columns of a billing file and
            # understate the total, which is far worse than refusing to load it.
            raise ValueError(
                f"{path.name}:{lineno}: row has {len(r)} fields but the header has "
                f"{len(header)}. The Cost table export format may have changed."
            )
        charges.append(dict(zip(header, r, strict=True)))
    return preamble, charges


def _invoice_month(preamble: dict[str, str], path: pathlib.Path) -> date:
    """First day of the invoice month.

    Taken from the preamble's Invoice date rather than from usage dates, because
    single rows legitimately span a month boundary. Security Command Center, for one,
    bills a usage window that starts in the previous month, so keying off usage dates
    would scatter a single invoice across two months.
    """
    raw = preamble.get("Invoice date", "")
    try:
        d = date.fromisoformat(raw)
    except ValueError:
        raise ValueError(
            f"{path.name}: could not read 'Invoice date' from the preamble (got {raw!r})"
        ) from None
    return d.replace(day=1)


def read_file(path: pathlib.Path, provider: str = "gcp") -> list[CostRow]:
    """One Cost table CSV -> CostRows, all stamped with the invoice month."""
    preamble, charges = _parse_one(path)
    month = _invoice_month(preamble, path)
    account = preamble.get("Billing account ID", "")
    currency = preamble.get("Currency", "USD") or "USD"
    account_name = ""

    has_credit_rows = False
    # Aggregate in-file: a month's CSV has hundreds of SKU rows and the metrics
    # layer groups them anyway, so collapsing here keeps series count down.
    agg: dict[tuple[str, str, str], dict[str, float]] = defaultdict(
        lambda: {"list": 0.0, "billed": 0.0}
    )

    for row in charges:
        if row.get("Cost type", "").strip() in _SUMMARY_COST_TYPES:
            continue

        account_name = account_name or row.get("Billing account name", "").strip()
        service = row.get("Service description", "").strip() or "(unattributed)"
        project = row.get("Project ID", "").strip()
        sku = row.get("SKU description", "").strip()

        # Prefer the unrounded column. Summing hundreds of rows already rounded to
        # cents drifts the month total away from the invoice; the unrounded figures
        # reproduce the file's own Total exactly.
        amount = _num(row.get("Unrounded Cost ($)") or row.get("Cost ($)"))
        key = (project, service, sku)

        if row.get("Credit type", "").strip():
            # A credit row. Reduces what is actually paid; leaves list cost alone.
            has_credit_rows = True
            agg[key]["billed"] += amount
        else:
            agg[key]["list"] += amount
            agg[key]["billed"] += amount

    rows: list[CostRow] = []
    for (project, service, sku), amounts in agg.items():
        rows.append(
            CostRow(
                provider=provider,
                billing_account=account,
                billing_account_name=account_name or account,
                service=service,
                sub_account=project,
                sku=sku or None,
                list_cost=amounts["list"],
                # A gross-only file cannot attribute credits per service. Reporting
                # billed == list there would show a fully-paid bill that credits had
                # in fact absorbed, so the net is carried once, at account level.
                billed_cost=amounts["billed"] if has_credit_rows else 0.0,
                period_start=month,
                currency=currency,
            )
        )

    gross = sum(r.list_cost for r in rows)

    if not has_credit_rows:
        net = _num(preamble.get("Total amount due"))
        rows.append(
            CostRow(
                provider=provider,
                billing_account=account,
                billing_account_name=account_name or account,
                service=NET_ONLY_SERVICE,
                sub_account="",
                list_cost=0.0,
                billed_cost=net,
                period_start=month,
                currency=currency,
            )
        )
        _say(
            f"{month:%Y-%m} gross ${gross:,.2f} across {len(rows) - 1} rows, "
            f"invoice net ${net:,.2f} carried at account level "
            f"(no credit rows in {path.name})"
        )
    else:
        _say(
            f"{month:%Y-%m} gross ${gross:,.2f} net "
            f"${sum(r.billed_cost for r in rows):,.2f} across {len(rows)} rows "
            f"(credits itemized, {path.name})"
        )

    return rows


def read_dir(directory: str | pathlib.Path, provider: str = "gcp") -> list[CostRow]:
    """Every *.csv in a directory -> CostRows.

    A missing directory is not an error: the common deployment has no history to
    import, and failing there would break a working config. An unreadable file IS
    an error, because silently importing 3 of 4 months understates the bill.
    """
    d = pathlib.Path(directory)
    if not d.is_dir():
        _say(f"csv_dir {d} does not exist, skipping historical import")
        return []

    files = sorted(d.glob("*.csv"))
    if not files:
        _say(f"csv_dir {d} has no *.csv files")
        return []

    # Two files for one month happens when the same month is exported both with and
    # without savings. Summing them would double the month, so keep the richer one.
    by_month: dict[date, tuple[pathlib.Path, list[CostRow], bool]] = {}
    for f in files:
        rows = read_file(f, provider=provider)
        if not rows:
            continue
        month = rows[0].period_start
        itemized = not any(r.service == NET_ONLY_SERVICE for r in rows)
        prev = by_month.get(month)
        if prev is None or (itemized and not prev[2]):
            if prev is not None:
                _say(f"{f.name} supersedes {prev[0].name} for {month:%Y-%m} (credits itemized)")
            by_month[month] = (f, rows, itemized)
        else:
            _say(f"skipping {f.name}: {month:%Y-%m} already imported from {prev[0].name}")

    out = [r for _, rows, _ in by_month.values() for r in rows]
    _say(
        f"imported {len(out)} historical rows covering "
        f"{', '.join(m.strftime('%Y-%m') for m in sorted(by_month)) or 'nothing'}"
    )
    return out


def months_covered(rows: list[CostRow]) -> set[date]:
    """Invoice months the CSV import is authoritative for."""
    return {r.period_start for r in rows}
