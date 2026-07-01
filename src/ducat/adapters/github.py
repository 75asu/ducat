"""GitHub adapter , org spend via the enhanced billing platform.

GitHub replaced the old per-product billing endpoints (Actions/Packages/storage)
with a single usage endpoint in 2025:

    GET /organizations/{org}/settings/billing/usage

It returns daily, per-SKU line items, each with `netAmount` (the actual charge
after discounts) , which is what we graph. `product` separates Actions /
Packages / Copilot / storage.

Auth: a fine-grained PAT with the org "Administration: read" permission, held by
an org owner/admin. GitHub App installation tokens are historically excluded
from billing endpoints, so a PAT is required here. The token is read from the
env var named by `token_env` (default GITHUB_TOKEN) , never from config.

Docs: https://docs.github.com/en/rest/billing/enhanced-billing
"""

from __future__ import annotations

import datetime as _dt
import os
from typing import Any

import httpx

from ..focus import CostRow

_API = "https://api.github.com"
# GitHub dates its REST API by breaking-change version. Overridable via config
# (`api_version`) so we can bump without a code change if GitHub moves it.
_DEFAULT_API_VERSION = "2022-11-28"


class GithubAdapter:
    name = "github"

    def fetch(self, opts: dict[str, Any]) -> list[CostRow]:
        org = opts.get("org")
        if not org:
            raise RuntimeError("github: config is missing `org`")

        token_env = opts.get("token_env", "GITHUB_TOKEN")
        token = os.environ.get(token_env)
        if not token:
            raise RuntimeError(
                f"github: no token in ${token_env}. Create a fine-grained PAT with "
                f"org 'Administration: read' and export it as {token_env}."
            )

        today = _dt.date.today()
        # Default: the whole current year (the API's default) so we get the
        # month-by-month history, not just the current month. Narrow via config
        # (year / month / day) when you want a specific window.
        params: dict[str, Any] = {}
        for k in ("year", "month", "day", "product", "sku", "cost_center_id"):
            if k in opts:
                params[k] = opts[k]

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": opts.get("api_version", _DEFAULT_API_VERSION),
        }
        url = f"{_API}/organizations/{org}/settings/billing/usage"

        resp = httpx.get(url, headers=headers, params=params, timeout=30.0)
        if resp.status_code in (401, 403):
            raise RuntimeError(
                f"github: {resp.status_code} reading billing for org '{org}'. The token "
                "needs org 'Administration: read' and you must be an org owner/admin."
            )
        if resp.status_code == 404:
            raise RuntimeError(
                f"github: 404 for org '{org}'. Check the org name and that the enhanced "
                "billing platform is enabled (legacy billing orgs use different endpoints)."
            )
        resp.raise_for_status()

        payload = resp.json()
        items = payload.get("usageItems", payload if isinstance(payload, list) else [])

        rows: list[CostRow] = []
        for it in items:
            net = _money(it.get("netAmount"))
            gross = _money(it.get("grossAmount"))
            if net == 0.0 and gross == 0.0:
                continue  # nothing billed and no list price -> nothing to show
            rows.append(
                CostRow(
                    provider="github",
                    billing_account=str(org),
                    billing_account_name=str(org),  # the org slug is already the name
                    service=str(it.get("product", "unknown")),
                    sku=it.get("sku"),
                    billed_cost=net,
                    list_cost=gross,
                    period_start=_parse_date(it.get("date"), today),
                    sub_account=(it.get("repositoryName") or None),  # "" for org-wide lines
                    currency="USD",
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
    text = str(value)
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m"):
        try:
            return _dt.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return fallback
