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

Two account topologies, switchable in config:

  * Single-principal (default) , one credential set (the ambient chain, or a
    `profile:`) reads whatever it can see. A payer/management (or delegated
    billing-admin) principal sees ALL linked accounts, split by LINKED_ACCOUNT;
    a plain member account sees only itself.

  * Per-account (`accounts:` is a list) , one credential set PER account, for
    when no single principal can see the whole org (the common case: teams with
    access only to their own accounts). Each entry auths independently , static
    keys from named env vars, a named `profile:`, or an assumed `role_arn:` ,
    and its rows are tagged with the entry's `id`/`name`. Cost Explorer is
    per-account, so this stitches several single-account views into one board.

Auth: the ambient AWS credential chain (boto3 default) , env vars, a shared
profile, IRSA, or an assumed role. The core never holds AWS secrets. Cost
Explorer is global, pinned to us-east-1.

Needs the `aws` extra: `pip install 'ducat[aws]'`. Requires `ce:GetCostAndUsage`.

Docs: https://docs.aws.amazon.com/aws-cost-management/latest/APIReference/API_GetCostAndUsage.html
"""

from __future__ import annotations

import datetime as _dt
import os
import sys
from collections import defaultdict
from typing import Any

from ..focus import CostRow
from ..metrics import SCRAPE_ERRORS

_CE_REGION = "us-east-1"  # Cost Explorer only lives here.
_Key = tuple[str, str, _dt.date]  # (service, account, period_start)
_DEFAULT_GROUP_BY = [
    {"Type": "DIMENSION", "Key": "SERVICE"},
    {"Type": "DIMENSION", "Key": "LINKED_ACCOUNT"},
]


class AwsAdapter:
    name = "aws"

    def fetch(self, opts: dict[str, Any]) -> list[CostRow]:
        try:
            import boto3
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("aws: needs the 'aws' extra , pip install 'ducat[aws]'") from exc

        accounts = opts.get("accounts")
        if accounts:
            # Per-account mode: one credential set per account. Each account's
            # Cost Explorer sees only itself, so we run the dual query once per
            # account and tag the rows from that account's own config entry.
            rows: list[CostRow] = []
            for acct in accounts:
                label = acct.get("id") or acct.get("name") or "?"
                # Isolate each account: one bad set of creds (or a CE permission
                # gap) must not abort the whole refresh and blank every board.
                try:
                    session = self._session_for_account(boto3, acct)
                    rows.extend(self._run(session, opts, acct=acct))
                except Exception as exc:
                    print(f"ducat: aws: account {label} failed ({exc}); skipping.", file=sys.stderr)
                    SCRAPE_ERRORS.labels(provider="aws", account=str(label)).inc()
                    continue
            return rows

        # Single-principal mode (default): the ambient creds, or a named profile.
        # Reads whatever that principal sees , a member account (itself) or a
        # payer/owner principal (all linked accounts, split by LINKED_ACCOUNT).
        session = (
            boto3.Session(profile_name=opts["profile"]) if opts.get("profile") else boto3.Session()
        )
        return self._run(session, opts, acct=None)

    def _session_for_account(self, boto3: Any, acct: dict[str, Any]) -> Any:
        """Build a boto3 Session for one account per its configured auth method.

        Precedence: static keys (named env vars) -> named profile -> assumed role.
        """
        label = acct.get("id") or acct.get("name") or "?"

        # 1) Static keys from NAMED env vars , one pair per account (e.g. ESO
        #    injects AWS_ACCESS_KEY_ID_INDIA1 / _SHARED). Keeps creds out of git
        #    and lets several accounts coexist without clobbering each other.
        ak_env = acct.get("access_key_id_env")
        if ak_env:
            sk_env = acct.get("secret_access_key_env")
            st_env = acct.get("session_token_env")
            ak = os.environ.get(ak_env)
            sk = os.environ.get(sk_env) if sk_env else None
            if not ak or not sk:
                raise RuntimeError(
                    f"aws: account {label} expects static creds in ${ak_env} / "
                    f"${sk_env}, but they are not set in the environment."
                )
            return boto3.Session(
                aws_access_key_id=ak,
                aws_secret_access_key=sk,
                aws_session_token=(os.environ.get(st_env) if st_env else None),
            )

        # 2) A named AWS profile (~/.aws/config).
        if acct.get("profile"):
            return boto3.Session(profile_name=acct["profile"])

        # 3) Assume a role from the ambient/base creds (one base principal that
        #    can sts:AssumeRole a read-only CE role in each member account).
        if acct.get("role_arn"):
            base = boto3.Session()
            creds = base.client("sts").assume_role(
                RoleArn=acct["role_arn"],
                RoleSessionName=acct.get("role_session_name", f"ducat-{label}"),
            )["Credentials"]
            return boto3.Session(
                aws_access_key_id=creds["AccessKeyId"],
                aws_secret_access_key=creds["SecretAccessKey"],
                aws_session_token=creds["SessionToken"],
            )

        raise RuntimeError(
            f"aws: account {label} has no auth configured , set one of "
            "access_key_id_env (+ secret_access_key_env), profile, or role_arn."
        )

    def _run(self, session: Any, opts: dict[str, Any], acct: dict[str, Any] | None) -> list[CostRow]:
        """Run the dual (consumption + invoice) Cost Explorer query on one session."""
        ce = session.client("ce", region_name=opts.get("region", _CE_REGION))

        granularity = opts.get("granularity", "MONTHLY").upper()
        list_metric = opts.get("list_metric", "UnblendedCost")  # usage at rate
        net_metric = opts.get("net_metric", "NetUnblendedCost")  # after credits/discounts
        today = _dt.date.today()
        # Default window = whole current year (month-by-month). CE End is EXCLUSIVE.
        start = opts.get("from") or _dt.date(today.year, 1, 1).isoformat()
        end = opts.get("to") or (today + _dt.timedelta(days=1)).isoformat()
        group_by = opts.get("group_by", _DEFAULT_GROUP_BY)

        if acct is not None:
            # Per-account: we KNOW which account these creds are for, so tag every
            # row with the configured id/name (authoritative, and works even for a
            # standalone account with no LINKED_ACCOUNT dimension).
            force_account = str(acct.get("id", "")) or None
            account_fallback = force_account or ""
            account_names = {account_fallback: acct.get("name", account_fallback)} if account_fallback else {}
        else:
            force_account = None
            account_fallback = str(opts.get("account", ""))
            # Optional {account_id: friendly_name} map so multi-account (payer)
            # views are readable (Cost Explorer returns ids, not names).
            account_names = opts.get("account_names", {}) or {}

        # Which record types count as "consumption". Default just real usage
        # (excludes Credit/Refund/Tax so consumption is not netted to zero).
        usage_record_types = opts.get("usage_record_types", ["Usage"])

        base: dict[str, Any] = {
            "TimePeriod": {"Start": start, "End": end},
            "Granularity": granularity,
            "GroupBy": group_by,
        }

        # 1) consumption: usage priced at list rate, filtered to real usage.
        cons_kwargs = {**base, "Metrics": [list_metric]}
        if usage_record_types:
            cons_kwargs["Filter"] = {"Dimensions": {"Key": "RECORD_TYPE", "Values": usage_record_types}}
        consumed, ccy = self._collect(ce, cons_kwargs, list_metric, account_fallback, force_account)

        # 2) invoice: what actually gets billed, all record types (credits net in).
        billed, ccy2 = self._collect(
            ce, {**base, "Metrics": [net_metric]}, net_metric, account_fallback, force_account
        )

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
        self,
        ce: Any,
        kwargs: dict[str, Any],
        metric: str,
        account_fallback: str,
        force_account: str | None = None,
    ) -> tuple[dict[_Key, float], str | None]:
        """Run a (paged) GetCostAndUsage and sum `metric` per (service, account, month).

        `force_account` overrides the account key on every row (per-account mode,
        where the credentials themselves scope the query to one known account).
        """
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
                    account = force_account or (keys[1] if len(keys) > 1 else account_fallback)
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
