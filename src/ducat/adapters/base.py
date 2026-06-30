"""The adapter contract.

An adapter knows how to talk to one provider's billing API and return a list of
normalized `CostRow`s. That is the entire interface. Auth is the adapter's
concern (a bearer token from an env var, the ambient AWS/GCP credential chain,
etc.) so the core never holds secrets.
"""

from __future__ import annotations

from typing import Any, Protocol

from ..focus import CostRow


class Adapter(Protocol):
    #: short provider key, e.g. "github"
    name: str

    def fetch(self, opts: dict[str, Any]) -> list[CostRow]:
        """Pull cost for the current period and return normalized rows.

        `opts` is the provider's block from the config file. Should raise a
        clear, actionable error on auth/permission failures.
        """
        ...
