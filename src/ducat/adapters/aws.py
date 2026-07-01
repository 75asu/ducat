"""AWS adapter , account spend via Cost Explorer.

Two numbers matter and we surface both, so a credit-funded account still shows
what it actually consumed:

    list_cost   <- what you USED, priced at rate (RECORD_TYPE=Usage)  , "consumed"
    billed_cost <- what you actually PAY, after credits/discounts/refunds , "invoice"

For an account fully covered by credits the invoice is ~0 while the usage is
real; the gap between the two is the credit/discount value ("list-price-avoided").

Nothing is hardcoded to a service list: results are grouped by SERVICE (+
LINKED_ACCOUNT) so any service used at any point in the window shows up
automatically. Every knob (metrics, granularity, window, grouping, which record
types count as "usage") is overridable from config; the defaults are sensible.

Auth: the ambient AWS credential chain (boto3 default) , env vars, a shared
profile (`profile:` in config), IRSA, or a GitHub Actions OIDC role assumed
before `ducat run`. The core never holds AWS secrets. Cost Explorer is global,
pinned to us-east-1.

Needs the `aws` extra: `pip install 'ducat[aws]'`. Requires `ce:GetCostAndUsage`.

Docs: https://docs.aws.amazon.com/aws-cost-management/latest/APIReference/API_GetCostAndUsage.html
"""

from __future__ import annotations

import datetime as _dt
from collections import defaultdict
from typing import Any

from ..focus import CostRow

_CE_REGION = "us-east-1"  # Cost Explorer only lives here.
_Key = tuple[str, str, _dt.date]  # (service, account, period_start)


class AwsAdapter:
    name = "aws"

    def fetch(self, opts: dict[str, Any]) -> list[CostRow]:
        try:
            import boto3
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("aws: needs the 'aws' extra , pip install 'ducat[aws]'") from exc

        session = (
            boto3.Session(profile_name=opts["profile"]) if opts.get("profile") else boto3.Session()
        )
        ce = session.client("ce", region_name=opts.get("region", _CE_REGION))

        granularity = opts.get("granularity", "MONTHLY").upper()
        list_metric = opts.get("list_metric", "UnblendedCost")  # usage at rate
        net_metric = opts.get("net_metric", "NetUnblendedCost")  # after credits/discounts
        today = _dt.date.today()
        # Default window = whole current year (month-by-month). CE End is EXCLUSIVE.
        start = opts.get("from") or _dt.date(today.year, 1, 1).isoformat()
        end = opts.get("to") or (today + _dt.timedelta(days=1)).isoformat()
        group_by = opts.get(
            "group_by",
            [{"Type": "DIMENSION", "Key": "SERVICE"}, {"Type": "DIMENSION", "Key": "LINKED_ACCOUNT"}],
        )
        # Which record types count as "consumption". Default just real usage
        # (excludes Credit/Refund/Tax so consumption is not netted to zero).
        # Set to null/[] in config to treat every record type as usage.
        usage_record_types = opts.get("usage_record_types", ["Usage"])
        account_fallback = str(opts.get("account", ""))
        # Optional {account_id: friendly_name} map so multi-account views are
        # readable (Cost Explorer returns ids, not names). Falls back to the id.
        account_names = opts.get("account_names", {}) or {}

        base: dict[str, Any] = {
            "TimePeriod": {"Start": start, "End": end},
            "Granularity": granularity,
            "GroupBy": group_by,
        }

        # 1) consumption: usage priced at list rate, filtered to real usage.
        cons_kwargs = {**base, "Metrics": [list_metric]}
        if usage_record_types:
            cons_kwargs["Filter"] = {"Dimensions": {"Key": "RECORD_TYPE", "Values": usage_record_types}}
        consumed, ccy = self._collect(ce, cons_kwargs, list_metric, account_fallback)

        # 2) invoice: what actually gets billed, all record types (credits net in).
        billed, ccy2 = self._collect(ce, {**base, "Metrics": [net_metric]}, net_metric, account_fallback)

        currency = ccy or ccy2 or "USD"
        rows: list[CostRow] = []
        for key in set(consumed) | set(billed):
            service, account, period = key
            list_cost = consumed.get(key, 0.0)
            billed_cost = billed.get(key, 0.0)
            if list_cost == 0.0 and billed_cost == 0.0:
                continue
            rows.append(
                CostRow(
                    provider="aws",
                    billing_account=account,
                    billing_account_name=str(account_names.get(account, account)),
                    service=service,
                    billed_cost=billed_cost,
                    list_cost=list_cost,
                    period_start=period,
                    currency=currency,
                )
            )
        return rows

    def _collect(
        self, ce: Any, kwargs: dict[str, Any], metric: str, account_fallback: str
    ) -> tuple[dict[_Key, float], str | None]:
        """Run a (paged) GetCostAndUsage and sum `metric` per (service, account, month)."""
        out: dict[_Key, float] = defaultdict(float)
        currency: str | None = None
        token: str | None = None
        while True:
            call = dict(kwargs, NextPageToken=token) if token else kwargs
            try:
                resp = ce.get_cost_and_usage(**call)
            except Exception as exc:  # botocore ClientError et al.
                raise RuntimeError(
                    f"aws: Cost Explorer GetCostAndUsage failed ({exc}). The "
                    "credentials need the 'ce:GetCostAndUsage' permission."
                ) from exc
            for period in resp.get("ResultsByTime", []):
                pstart = _parse_date(period["TimePeriod"]["Start"], _dt.date.today())
                for group in period.get("Groups", []):
                    keys = group.get("Keys", [])
                    service = keys[0] if keys else "unknown"
                    account = keys[1] if len(keys) > 1 else account_fallback
                    m = group.get("Metrics", {}).get(metric)
                    currency = currency or (m or {}).get("Unit")
                    out[(service, account, pstart)] += _amount(m)
            token = resp.get("NextPageToken")
            if not token:
                break
        return out, currency


def _amount(metric: Any) -> float:
    try:
        return float((metric or {}).get("Amount", 0.0))
    except (TypeError, ValueError):
        return 0.0


def _parse_date(value: Any, fallback: _dt.date) -> _dt.date:
    try:
        return _dt.date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return fallback
