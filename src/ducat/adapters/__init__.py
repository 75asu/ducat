"""Adapter registry. Add a provider here once its module exists."""

from __future__ import annotations

from .base import Adapter
from .github import GithubAdapter

_REGISTRY: dict[str, type] = {
    GithubAdapter.name: GithubAdapter,
    # cloudflare, aws, gcp, openai, anthropic, ... land here as they ship.
}


def get(name: str) -> Adapter:
    try:
        return _REGISTRY[name]()
    except KeyError:
        known = ", ".join(sorted(_REGISTRY)) or "(none)"
        raise RuntimeError(f"unknown provider '{name}'. Available: {known}")


def available() -> list[str]:
    return sorted(_REGISTRY)


__all__ = ["Adapter", "get", "available"]
