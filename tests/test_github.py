import httpx
import pytest

from ducat.adapters.github import GithubAdapter

_SAMPLE = {
    "usageItems": [
        # net > 0 (actually billed)
        {"date": "2026-06-01", "product": "copilot", "sku": "Copilot Business",
         "netAmount": 133.0, "grossAmount": 133.0},
        # net 0 but gross > 0 (fully covered by the included plan) -> keep for list cost
        {"date": "2026-06-01", "product": "actions", "sku": "Actions Linux",
         "netAmount": 0.0, "grossAmount": 40.0, "repositoryName": "fravityai/fravity"},
        # both 0 -> dropped
        {"date": "2026-06-01", "product": "actions", "sku": "Actions storage",
         "netAmount": 0.0, "grossAmount": 0.0},
    ]
}


def _fake_get(status, payload):
    def _get(url, headers=None, params=None, timeout=None):
        return httpx.Response(status, json=payload, request=httpx.Request("GET", url))
    return _get


def test_keeps_net_and_gross_only_rows(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    monkeypatch.setattr("ducat.adapters.github.httpx.get", _fake_get(200, _SAMPLE))
    rows = GithubAdapter().fetch({"org": "acme"})
    assert len(rows) == 2  # the all-zero row is dropped; the gross-only row is kept
    by_service = {r.service: r for r in rows}
    assert by_service["copilot"].billed_cost == 133.0
    assert by_service["copilot"].list_cost == 133.0
    # fully discounted: net 0 but list price retained
    assert by_service["actions"].billed_cost == 0.0
    assert by_service["actions"].list_cost == 40.0
    assert by_service["actions"].sub_account == "fravityai/fravity"
    assert all(r.provider == "github" and r.billing_account == "acme" for r in rows)


def test_403_hints_at_permission(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    monkeypatch.setattr("ducat.adapters.github.httpx.get", _fake_get(403, {"message": "no"}))
    with pytest.raises(RuntimeError, match="Administration"):
        GithubAdapter().fetch({"org": "acme"})


def test_missing_token(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="no token"):
        GithubAdapter().fetch({"org": "acme"})


def test_missing_org():
    with pytest.raises(RuntimeError, match="missing `org`"):
        GithubAdapter().fetch({})
