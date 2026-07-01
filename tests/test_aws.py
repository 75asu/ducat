import sys
import types

from ducat.adapters.aws import AwsAdapter

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


class _FakeSession:
    def __init__(self, *a, **k):
        pass

    def client(self, name, region_name=None):
        assert name == "ce"
        return _FakeCE()


def _stub_boto3(monkeypatch):
    _FakeCE.calls = []
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
