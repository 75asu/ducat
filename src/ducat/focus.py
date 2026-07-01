"""The normalized cost record every adapter produces.

ducat speaks one language internally: the FinOps Open Cost & Usage Specification
(FOCUS). Each provider's billing API gets translated by its adapter into a list
of `CostRow`s, and every sink (Prometheus scrape, remote_write) consumes that
same shape. Adding a provider means writing one adapter that returns these rows;
nothing downstream changes.

Reference: https://focus.finops.org/
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True, slots=True)
class CostRow:
    """One billed amount, for one service, in one billing account, over one period.

    Field names mirror FOCUS columns so the mapping stays obvious:
      provider        -> ProviderName        (lowercased: aws, gcp, github, ...)
      billing_account -> BillingAccountId
      service         -> ServiceName
      billed_cost     -> BilledCost           (the after-discount actual charge)
      period_start    -> ChargePeriodStart    (the day/month this cost is for)
      currency        -> BillingCurrency
      sub_account     -> SubAccountId          (project / linked account / workspace)
      region          -> RegionId
      sku             -> SkuId
      tags            -> Tags
    """

    provider: str
    billing_account: str
    service: str
    billed_cost: float
    period_start: date
    currency: str = "USD"
    list_cost: float = 0.0  # FOCUS ListCost: pre-discount / public-rate cost
    # FOCUS BillingAccountName: human-friendly account/project name. Falls back to
    # billing_account (the id) when an adapter can't resolve a name.
    billing_account_name: str = ""
    sub_account: str | None = None
    region: str | None = None
    sku: str | None = None
    tags: dict[str, str] = field(default_factory=dict)
