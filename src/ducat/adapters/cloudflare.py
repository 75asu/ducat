"""Cloudflare adapter , account spend.

Cloudflare cost comes from two places, and this adapter merges both so it works
today and gets richer automatically later:

  * subscriptions      , recurring plan cost (Workers Paid, Zero Trust seats,
    etc.). Readable now with an account API token carrying Billing:Read.
  * billable/usage (v2) , per-product usage/overage, emitted natively in the
    FinOps FOCUS v1.3 schema. It is an Alpha/Restricted endpoint: until
    Cloudflare enables it for the account it returns 403, which we skip
    silently. Once enabled it starts contributing rows with NO code change.

So a free-tier account shows its $0 plan lines today; a paid/usage account fills
in per-product detail the moment the FOCUS endpoint is switched on.

Auth: an account API token with Billing:Read, read from the env var named by
`token_env` (default CLOUDFLARE_API_TOKEN) , never from config.

Docs: https://developers.cloudflare.com/billing/  (billable-usage v2 = FOCUS)
"""

from __future__ import annotations

import datetime as _dt
import os
from typing import Any

import httpx

from ..focus import CostRow

_API = "https://api.cloudflare.com/client/v4"


class CloudflareAdapter:
    name = "cloudflare"

    def fetch(self, opts: dict[str, Any]) -> list[CostRow]:
        account = opts.get("account_id") or opts.get("account")
        if not account:
            raise RuntimeError("cloudflare: config is missing `account_id`")

        token_env = opts.get("token_env", "CLOUDFLARE_API_TOKEN")
        token = os.environ.get(token_env)
        if not token:
            raise RuntimeError(
                f"cloudflare: no token in ${token_env}. Create an account API token "
                "with 'Billing: Read' and export it as that variable."
            )

        base = opts.get("api_base", _API).rstrip("/")
        headers = {"Authorization": f"Bearer {token}"}
        today = _dt.date.today()

        rows: list[CostRow] = []
        rows.extend(self._subscriptions(base, str(account), headers, today))
        rows.extend(self._billable_usage(base, str(account), headers, opts, today))
        return rows

    def _subscriptions(self, base: str, account: str, headers: dict, today: _dt.date) -> list[CostRow]:
        resp = httpx.get(f"{base}/accounts/{account}/subscriptions", headers=headers, timeout=30.0)
        if resp.status_code in (401, 403):
            raise RuntimeError(
                f"cloudflare: {resp.status_code} reading subscriptions for account "
                f"'{account}'. The token needs 'Billing: Read'."
            )
        resp.raise_for_status()
        rows: list[CostRow] = []
        for s in resp.json().get("result") or []:
            rate_plan = s.get("rate_plan") or {}
            price = _money(s.get("price"))
            rows.append(
                CostRow(
                    provider="cloudflare",
                    billing_account=account,
                    service=str(rate_plan.get("id") or "subscription"),
                    billed_cost=price,
                    list_cost=price,  # subscriptions have no separate list price
                    period_start=_parse_date(s.get("current_period_start"), today),
                    currency=str(s.get("currency") or "USD"),
                    sku=(s.get("id") or None),
                )
            )
        return rows

    def _billable_usage(
        self, base: str, account: str, headers: dict, opts: dict[str, Any], today: _dt.date
    ) -> list[CostRow]:
        params = {
            "from": opts.get("from") or _dt.date(today.year, 1, 1).isoformat(),
            "to": opts.get("to") or today.isoformat(),
        }
        resp = httpx.get(
            f"{base}/accounts/{account}/billable/usage", headers=headers, params=params, timeout=30.0
        )
        # Alpha/Restricted: 403 until Cloudflare enables it for the account. Skip
        # silently so the adapter still returns the subscription rows; it starts
        # contributing usage automatically once the endpoint is turned on.
        if resp.status_code != 200:
            return []
        payload = resp.json()
        if not payload.get("success"):
            return []
        rows: list[CostRow] = []
        for r in payload.get("result") or []:
            billed = _money(r.get("BilledCost"))
            listc = _money(r.get("ListCost"))
            if billed == 0.0 and listc == 0.0:
                continue
            rows.append(
                CostRow(
                    provider="cloudflare",
                    billing_account=str(r.get("BillingAccountId") or account),
                    service=str(r.get("x_ProductFamilyName") or "unknown"),
                    billed_cost=billed,
                    list_cost=listc,
                    period_start=_parse_date(r.get("ChargePeriodStart"), today),
                    currency=str(r.get("BillingCurrency") or "USD"),
                    sub_account=(r.get("SubAccountName") or r.get("x_ZoneName") or None),
                    region=(r.get("RegionName") or None),
                    sku=(r.get("x_BillableMetricName") or None),
                )
            )
        return rows


def _money(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _parse_date(value: Any, fallback: _dt.date) -> _dt.date:
    if not value:
        return fallback
    try:
        return _dt.date.fromisoformat(str(value)[:10])  # handles ISO datetimes too
    except ValueError:
        return fallback
