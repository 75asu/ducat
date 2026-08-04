import sys
import types

from ducat.adapters.aws import AwsAdapter
from ducat.metrics import SCRAPE_ERRORS


def _scrape_errors(account: str) -> float:
    """Current ducat_scrape_error count for an aws account label."""
    return SCRAPE_ERRORS.labels(provider="aws", account=account)._value.get()


# One month, three services. boto3 is stubbed (below) so this runs without the
# SDK or any AWS credentials. The fake returns this for BOTH the consumption
# query (reads UnblendedCost -> list_cost) and the invoice query
# (reads NetUnblendedCost -> billed_cost).
_SAMPLE = {
    "ResultsByTime": [
        {
            "TimePeriod": {"Start": "2026-06-01", "End": "2026-07-01"},
            "Groups": [
                # consumed but fully credit-covered: list 100, invoice 0 -> kept
                {
                    "Keys": ["Amazon Elastic Compute Cloud - Compute", "111122223333"],
                    "Metrics": {
                        "UnblendedCost": {"Amount": "100.0", "Unit": "USD"},
                        "NetUnblendedCost": {"Amount": "0.0", "Unit": "USD"},
                    },
                },
                {
                    "Keys": ["Amazon Simple Storage Service", "111122223333"],
                    "Metrics": {
                        "UnblendedCost": {"Amount": "10.0", "Unit": "USD"},
                        "NetUnblendedCost": {"Amount": "8.0", "Unit": "USD"},
                    },
                },
                # both zero -> dropped
                {
                    "Keys": ["AWS Lambda", "111122223333"],
                    "Metrics": {
                        "UnblendedCost": {"Amount": "0.0", "Unit": "USD"},
                        "NetUnblendedCost": {"Amount": "0.0", "Unit": "USD"},
                    },
                },
            ],
        }
    ]
    # no NextPageToken -> single page per query
}


class _FakeCE:
    calls: list = []

    def get_cost_and_usage(self, **kwargs):
        _FakeCE.calls.append(kwargs)
        return _SAMPLE


class _FakeSTS:
    calls: list = []

    def assume_role(self, **kwargs):
        _FakeSTS.calls.append(kwargs)
        return {
            "Credentials": {
                "AccessKeyId": "ASIA_TEMP",
                "SecretAccessKey": "temp-secret",
                "SessionToken": "temp-token",
            }
        }


class _FakeSession:
    #: records the kwargs each Session was built with (per-account cred assertions)
    sessions: list = []

    def __init__(self, *a, **k):
        _FakeSession.sessions.append(k)

    def client(self, name, region_name=None):
        if name == "sts":
            return _FakeSTS()
        assert name == "ce"
        return _FakeCE()


def _stub_boto3(monkeypatch):
    _FakeCE.calls = []
    _FakeSTS.calls = []
    _FakeSession.sessions = []
    fake = types.ModuleType("boto3")
    fake.Session = _FakeSession
    monkeypatch.setitem(sys.modules, "boto3", fake)


def test_maps_consumed_and_billed(monkeypatch):
    _stub_boto3(monkeypatch)
    rows = AwsAdapter().fetch({})
    assert len(rows) == 2  # the all-zero Lambda row is dropped
    by = {r.service: r for r in rows}
    ec2 = by["Amazon Elastic Compute Cloud - Compute"]
    s3 = by["Amazon Simple Storage Service"]
    # list_cost <- consumption (UnblendedCost); billed_cost <- invoice (NetUnblendedCost)
    assert ec2.list_cost == 100.0 and ec2.billed_cost == 0.0  # used, but credit-covered
    assert s3.list_cost == 10.0 and s3.billed_cost == 8.0
    assert all(r.provider == "aws" and r.billing_account == "111122223333" for r in rows)
    assert s3.period_start.isoformat() == "2026-06-01"
    assert s3.currency == "USD"


def test_dual_query_shape(monkeypatch):
    _stub_boto3(monkeypatch)
    AwsAdapter().fetch({})
    assert len(_FakeCE.calls) == 2
    consumption, invoice = _FakeCE.calls
    # consumption: list metric, filtered to real usage
    assert consumption["Metrics"] == ["UnblendedCost"]
    assert consumption["Filter"] == {"Dimensions": {"Key": "RECORD_TYPE", "Values": ["Usage"]}}
    assert [g["Key"] for g in consumption["GroupBy"]] == ["SERVICE", "LINKED_ACCOUNT"]
    # invoice: net metric, ALL record types (credits net in)
    assert invoice["Metrics"] == ["NetUnblendedCost"]
    assert "Filter" not in invoice


def test_usage_record_types_configurable(monkeypatch):
    _stub_boto3(monkeypatch)
    AwsAdapter().fetch({"usage_record_types": None})  # null -> every record type is "usage"
    consumption = _FakeCE.calls[0]
    assert "Filter" not in consumption


# ---- per-account mode (no single org-wide principal) -------------------------


def test_per_account_static_creds(monkeypatch):
    _stub_boto3(monkeypatch)
    monkeypatch.setenv("AK_A", "keyA")
    monkeypatch.setenv("SK_A", "secA")
    monkeypatch.setenv("AK_B", "keyB")
    monkeypatch.setenv("SK_B", "secB")
    rows = AwsAdapter().fetch(
        {
            "accounts": [
                {
                    "id": "111111111111",
                    "name": "acct-a",
                    "access_key_id_env": "AK_A",
                    "secret_access_key_env": "SK_A",
                },
                {
                    "id": "222222222222",
                    "name": "acct-b",
                    "access_key_id_env": "AK_B",
                    "secret_access_key_env": "SK_B",
                },
            ]
        }
    )
    # force_account tags each account's rows with the CONFIGURED id (not the
    # sample's LINKED_ACCOUNT), and account_names labels them.
    assert {r.billing_account for r in rows} == {"111111111111", "222222222222"}
    names = {r.billing_account: r.billing_account_name for r in rows}
    assert names["111111111111"] == "acct-a"
    assert names["222222222222"] == "acct-b"
    # 2 non-zero services x 2 accounts = 4 rows.
    assert len(rows) == 4
    # each account built its own Session with its own static creds.
    used_keys = {s.get("aws_access_key_id") for s in _FakeSession.sessions}
    assert {"keyA", "keyB"} <= used_keys
    # two accounts -> two dual queries = 4 CE calls.
    assert len(_FakeCE.calls) == 4


def test_per_account_missing_env_skips_and_counts(monkeypatch):
    _stub_boto3(monkeypatch)
    before = _scrape_errors("1")
    # A bad account is skipped (not raised) so it can't abort the whole refresh.
    rows = AwsAdapter().fetch(
        {
            "accounts": [
                {"id": "1", "access_key_id_env": "NOPE_AK", "secret_access_key_env": "NOPE_SK"}
            ]
        }
    )
    assert rows == []
    assert _scrape_errors("1") == before + 1


def test_per_account_assume_role(monkeypatch):
    _stub_boto3(monkeypatch)
    rows = AwsAdapter().fetch(
        {
            "accounts": [
                {
                    "id": "333333333333",
                    "name": "assumed",
                    "role_arn": "arn:aws:iam::333333333333:role/ce-reader",
                }
            ]
        }
    )
    assert {r.billing_account for r in rows} == {"333333333333"}
    # the base session assumed the configured role, then a session was built from
    # the returned temp creds.
    assert any(c.get("RoleArn", "").endswith("role/ce-reader") for c in _FakeSTS.calls)
    assert any(s.get("aws_session_token") == "temp-token" for s in _FakeSession.sessions)


def test_per_account_no_auth_skips_and_counts(monkeypatch):
    _stub_boto3(monkeypatch)
    before = _scrape_errors("orphan")
    rows = AwsAdapter().fetch({"accounts": [{"name": "orphan"}]})
    assert rows == []
    assert _scrape_errors("orphan") == before + 1


def test_per_account_isolation_keeps_good_accounts(monkeypatch):
    _stub_boto3(monkeypatch)
    monkeypatch.setenv("AK_OK", "keyOK")
    monkeypatch.setenv("SK_OK", "secOK")
    before = _scrape_errors("bad")
    # One bad account (no creds) alongside a good one: the good one still returns.
    rows = AwsAdapter().fetch(
        {
            "accounts": [
                {
                    "id": "bad",
                    "access_key_id_env": "MISSING_AK",
                    "secret_access_key_env": "MISSING_SK",
                },
                {
                    "id": "good",
                    "name": "acct-ok",
                    "access_key_id_env": "AK_OK",
                    "secret_access_key_env": "SK_OK",
                },
            ]
        }
    )
    assert {r.billing_account for r in rows} == {"good"}
    assert _scrape_errors("bad") == before + 1
