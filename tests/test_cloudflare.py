import httpx
import pytest

from ducat.adapters.cloudflare import CloudflareAdapter

_SUBS = {
    "success": True,
    "result": [
        {
            "id": "sub1",
            "rate_plan": {"id": "workers_paid"},
            "price": 5.0,
            "currency": "USD",
            "current_period_start": "2026-06-01T00:00:00Z",
        },
        {"id": "sub2", "rate_plan": {"id": "free"}, "price": 0.0, "currency": "USD"},
    ],
}
_USAGE_403 = {"success": False, "errors": [{"code": 1171, "message": "insufficient_permissions"}]}
_USAGE_OK = {
    "success": True,
    "result": [
        {
            "BilledCost": 0.0,
            "ListCost": 12.5,
            "BillingCurrency": "USD",
            "ChargePeriodStart": "2026-06-01",
            "x_ProductFamilyName": "R2",
            "SubAccountName": "acct",
            "RegionName": "wnam",
            "x_BillableMetricName": "r2_storage",
        },
        {"BilledCost": 0.0, "ListCost": 0.0, "x_ProductFamilyName": "Workers"},  # zero -> dropped
    ],
}


def _router(usage_status, usage_resp):
    def _get(url, headers=None, params=None, timeout=None):
        if url.endswith("/subscriptions"):
            return httpx.Response(200, json=_SUBS, request=httpx.Request("GET", url))
        if url.endswith("/billable/usage"):
            return httpx.Response(usage_status, json=usage_resp, request=httpx.Request("GET", url))
        raise AssertionError(f"unexpected url {url}")

    return _get


def test_subscriptions_mapped_usage_gated(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "x")
    monkeypatch.setattr("ducat.adapters.cloudflare.httpx.get", _router(403, _USAGE_403))
    rows = CloudflareAdapter().fetch({"account_id": "acct1"})
    # both subscriptions kept (even $0, so CF still shows on the board); usage 403 skipped
    assert len(rows) == 2
    by = {r.service: r for r in rows}
    assert by["workers_paid"].billed_cost == 5.0 and by["workers_paid"].list_cost == 5.0
    assert by["workers_paid"].period_start.isoformat() == "2026-06-01"
    assert by["free"].billed_cost == 0.0
    assert all(r.provider == "cloudflare" and r.billing_account == "acct1" for r in rows)


def test_billable_usage_included_when_enabled(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "x")
    monkeypatch.setattr("ducat.adapters.cloudflare.httpx.get", _router(200, _USAGE_OK))
    rows = CloudflareAdapter().fetch({"account_id": "acct1"})
    assert len(rows) == 3  # 2 subs + R2 (zero-cost Workers row dropped)
    r2 = next(r for r in rows if r.service == "R2")
    assert r2.list_cost == 12.5 and r2.sub_account == "acct"
    assert r2.region == "wnam" and r2.sku == "r2_storage"


def test_missing_token_errors(monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    with pytest.raises(RuntimeError):
        CloudflareAdapter().fetch({"account_id": "acct1"})


def test_missing_account_errors(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "x")
    with pytest.raises(RuntimeError):
        CloudflareAdapter().fetch({})
