"""GCP adapter , cost from the Cloud Billing BigQuery export.

**GCP has no cost API.** Unlike AWS (Cost Explorer `GetCostAndUsage`), the Cloud
Billing API only returns account metadata, IAM, budget *config*, and SKU *price
lists* , never your incurred spend. The BigQuery export IS the access path, which
is why this adapter reads SQL instead of calling an endpoint. OpenCost lands on
the same path for the same reason.

Two export tables are supported, and the choice matters:

  * ``focus`` (default) , ``gcp_billing_export_focus_<BILLING_ACCOUNT_ID>``.
    Google emits FOCUS-named columns natively (``BilledCost``, ``ListCost``,
    ``ServiceName``, ``SubAccountId``, ...), so the mapping here is nearly 1:1
    and almost nothing can drift in translation. Caveats: it is **Preview**, it
    lives in a Google-managed *immutable* dataset, and it carries a **2-year
    TTL**.
  * ``standard`` , ``gcp_billing_export_v1_<BILLING_ACCOUNT_ID>``. GA and
    durable, but the schema is GCP-native so this adapter does the FOCUS
    translation itself, including the credits arithmetic below.

Because FOCUS is pre-GA, the recommended shape is: enable both exports, point
this adapter at ``focus``, and keep ``standard`` as the fallback you can switch
to with one config line.

**The credits trap (``standard`` only).** In the native schema ``cost`` is the
charge *before* credits, and credits are negative rows inside a repeated
``credits`` field. Effective spend is therefore
``cost + SUM(credits.amount)`` , which requires an ``UNNEST``. Skip it and every
number you publish is inflated. On a credit-funded account that difference is
the entire bill: ``list_cost`` shows what you consumed at rate, ``billed_cost``
shows what you actually pay (often ~0). Same split as the AWS adapter's
Unblended vs NetUnblended, and the reason a credit-funded account still reports
real consumption instead of a flat zero.

**The query-cost trap.** These tables get large. Every query here is bounded by
a date range on the partition column so BigQuery prunes rather than scanning the
whole table , a naive ``SELECT *`` on a busy billing account costs real money
each scrape. Aggregation happens server-side in SQL, so what crosses the wire is
already grouped.

Auth: Application Default Credentials, i.e. ambient , ``gcloud auth
application-default login`` locally, or Workload Identity in-cluster. No token
env var, nothing in config. The reader needs only ``roles/bigquery.dataViewer``
on the dataset plus ``roles/bigquery.jobUser`` on the project running the query;
it needs **no billing permissions at all**, because it is reading a table.

Docs: https://cloud.google.com/billing/docs/how-to/export-data-bigquery
"""

from __future__ import annotations

import datetime as _dt
import sys
from typing import Any

from ..focus import CostRow
from ..metrics import SCRAPE_ERRORS
from . import gcp_csv

# FOCUS export: columns already carry FOCUS names, so this is a projection, not
# a translation. ChargePeriodStart is the partition column.
_SQL_FOCUS = """
SELECT
  DATE(ChargePeriodStart)                    AS day,
  BillingAccountId                           AS billing_account,
  IFNULL(x_Project.name, SubAccountId)       AS account_name,
  SubAccountId                               AS sub_account,
  ServiceName                                AS service,
  RegionId                                   AS region,
  SkuId                                      AS sku,
  BillingCurrency                            AS currency,
  SUM(BilledCost)                            AS billed_cost,
  SUM(ListCost)                              AS list_cost
FROM `{table}`
WHERE DATE(ChargePeriodStart) BETWEEN @from_date AND @to_date
GROUP BY day, billing_account, account_name, sub_account, service, region, sku, currency
HAVING billed_cost != 0 OR list_cost != 0
"""

# Standard export: GCP-native schema. `cost` is pre-credit; credits are negative
# rows in a repeated field, hence the UNNEST subquery for billed_cost.
_SQL_STANDARD = """
SELECT
  DATE(usage_start_time)                     AS day,
  billing_account_id                         AS billing_account,
  IFNULL(project.name, project.id)           AS account_name,
  project.id                                 AS sub_account,
  service.description                        AS service,
  location.region                            AS region,
  sku.id                                     AS sku,
  currency                                   AS currency,
  SUM(cost + IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) c), 0)) AS billed_cost,
  SUM(cost)                                  AS list_cost
FROM `{table}`
WHERE DATE(usage_start_time) BETWEEN @from_date AND @to_date
GROUP BY day, billing_account, account_name, sub_account, service, region, sku, currency
HAVING billed_cost != 0 OR list_cost != 0
"""


class GcpAdapter:
    name = "gcp"

    def fetch(self, opts: dict[str, Any]) -> list[CostRow]:
        """BigQuery export, plus any historical Console CSVs, as one series.

        The two sources cover disjoint time: BigQuery starts at its backfill window
        and runs forward forever; the CSVs cover months before that window, which
        BigQuery can never obtain. Where they overlap the CSV wins, because a CSV is
        a whole invoice while BigQuery's edge months are partial, and adding them
        would double-count.
        """
        historical = gcp_csv.read_dir(opts["csv_dir"]) if opts.get("csv_dir") else []
        live = self._fetch_bigquery(opts) if opts.get("dataset") or opts.get("table") else []

        if historical:
            covered = gcp_csv.months_covered(historical)
            kept, dropped = [], 0
            for row in live:
                if row.period_start.replace(day=1) in covered:
                    dropped += 1
                    continue
                kept.append(row)
            if dropped:
                print(
                    f"ducat: gcp: dropped {dropped} BigQuery rows for months covered by "
                    f"CSV ({', '.join(m.strftime('%Y-%m') for m in sorted(covered))}); "
                    "the invoice CSV is authoritative for those months.",
                    file=sys.stderr,
                )
            live = kept

        return historical + live

    def _fetch_bigquery(self, opts: dict[str, Any]) -> list[CostRow]:
        try:
            from google.cloud import bigquery
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("gcp: needs the 'gcp' extra , pip install 'ducat[gcp]'") from exc

        table = self._table(opts)
        source = (opts.get("source") or "focus").lower()
        if source not in ("focus", "standard"):
            raise RuntimeError(f"gcp: source must be 'focus' or 'standard', got {source!r}")

        from_date, to_date = self._window(opts)
        sql = (_SQL_FOCUS if source == "focus" else _SQL_STANDARD).format(table=table)

        client = bigquery.Client(project=opts.get("query_project") or opts.get("project"))
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("from_date", "DATE", from_date),
                bigquery.ScalarQueryParameter("to_date", "DATE", to_date),
            ],
            # A billing export can be huge and a runaway scan is a real bill.
            # Fail loudly rather than quietly spending. Configurable, default 20 GiB.
            maximum_bytes_billed=int(opts.get("max_bytes_billed", 20 * 1024**3)),
        )

        try:
            result = client.query(sql, job_config=job_config).result()
        except Exception as exc:
            # One bad billing account must not blank the whole board.
            SCRAPE_ERRORS.labels(provider="gcp", account=str(table)).inc()
            print(f"ducat: gcp: query failed for {table} ({exc}); skipping.", file=sys.stderr)
            return []

        rows: list[CostRow] = []
        for r in result:
            rows.append(
                CostRow(
                    provider="gcp",
                    billing_account=str(r["billing_account"] or ""),
                    billing_account_name=str(r["account_name"] or ""),
                    service=str(r["service"] or "unknown"),
                    billed_cost=float(r["billed_cost"] or 0.0),
                    list_cost=float(r["list_cost"] or 0.0),
                    period_start=r["day"],
                    currency=str(r["currency"] or "USD"),
                    sub_account=str(r["sub_account"]) if r["sub_account"] else None,
                    region=str(r["region"]) if r["region"] else None,
                    sku=str(r["sku"]) if r["sku"] else None,
                )
            )
        return rows

    # -- helpers ---------------------------------------------------------------

    def _table(self, opts: dict[str, Any]) -> str:
        """Resolve the fully-qualified table, either given whole or assembled."""
        if opts.get("table"):
            return str(opts["table"])

        project = opts.get("project")
        dataset = opts.get("dataset")
        account = opts.get("billing_account_id")
        if not (project and dataset and account):
            raise RuntimeError(
                "gcp: set `table`, or all of `project`, `dataset` and "
                "`billing_account_id` so the table name can be assembled."
            )
        # GCP substitutes hyphens for underscores in the table suffix.
        suffix = str(account).replace("-", "_")
        prefix = (
            "gcp_billing_export_focus_"
            if (opts.get("source") or "focus").lower() == "focus"
            else "gcp_billing_export_v1_"
        )
        return f"{project}.{dataset}.{prefix}{suffix}"

    def _window(self, opts: dict[str, Any]) -> tuple[_dt.date, _dt.date]:
        """Date range to pull. Defaults to Jan 1 this year through today.

        Matches the AWS adapter's default so a fresh install shows full
        month-by-month history rather than a single day.
        """
        today = _dt.date.today()
        frm = opts.get("from")
        to = opts.get("to")
        from_date = _as_date(frm) if frm else _dt.date(today.year, 1, 1)
        to_date = _as_date(to) if to else today
        if from_date > to_date:
            raise RuntimeError(f"gcp: `from` ({from_date}) is after `to` ({to_date})")
        return from_date, to_date


def _as_date(value: Any) -> _dt.date:
    if isinstance(value, _dt.date):
        return value
    return _dt.date.fromisoformat(str(value))
