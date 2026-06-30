"""Config loading.

Config is a small YAML file that declares which providers are enabled and where
to push. Secrets are NEVER in the file: a provider names an env var
(`token_env`) and the value is read from the environment at runtime, so the same
config is safe to commit and the secret comes from your secret manager / CI.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import yaml


@dataclass
class SinkConfig:
    # remote_write target (one-shot `run` mode). Omit to only use scrape mode.
    remote_write_url: str | None = None
    tenant: str | None = None  # X-Scope-OrgID header (multi-tenant Mimir/Cortex)
    username: str | None = None
    password_env: str | None = None


@dataclass
class Config:
    providers: dict[str, dict[str, Any]] = field(default_factory=dict)
    sink: SinkConfig = field(default_factory=SinkConfig)

    def enabled_providers(self) -> dict[str, dict[str, Any]]:
        return {
            name: opts
            for name, opts in self.providers.items()
            if opts.get("enabled", True)
        }


def load(path: str) -> Config:
    with open(path) as fh:
        raw = yaml.safe_load(fh) or {}
    sink_raw = raw.get("sink", {}) or {}
    rw = sink_raw.get("remote_write", {}) or {}
    sink = SinkConfig(
        remote_write_url=rw.get("url"),
        tenant=rw.get("tenant"),
        username=rw.get("username"),
        password_env=rw.get("password_env"),
    )
    return Config(providers=raw.get("providers", {}) or {}, sink=sink)


def env(name: str | None, default: str | None = None) -> str | None:
    """Read an env var by name (used for token_env / password_env indirection)."""
    if not name:
        return default
    return os.environ.get(name, default)
