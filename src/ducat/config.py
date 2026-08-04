"""Config loading.

Config is a small YAML file that declares which providers are enabled and where
to push. Secrets are NEVER in the file: a provider names an env var
(`token_env`) and the value is read from the environment at runtime, so the same
config is safe to commit and the secret comes from your secret manager / CI.

The same reasoning extends past secrets to anything that identifies *your* estate:
billing account ids, project names, dataset names, account numbers. None of it is
secret, all of it is specific to one org, and none of it belongs in a committed
example. So every string value supports shell-style substitution:

    project: ${DUCAT_GCP_PROJECT}
    dataset: ${DUCAT_GCP_DATASET:-billing_export}

`${VAR}` is required and fails loudly when unset; `${VAR:-default}` falls back.
A missing required variable raises rather than resolving to an empty string,
because an empty project name surfaces later as an inscrutable API error instead
of "you forgot to set DUCAT_GCP_PROJECT".
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any

import yaml

# ${VAR} or ${VAR:-default}. Deliberately not $VAR: braces keep it unambiguous
# and avoid mangling anything that merely contains a dollar sign.
_SUBST = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


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
        return {name: opts for name, opts in self.providers.items() if opts.get("enabled", True)}


class MissingConfigEnv(RuntimeError):
    """A `${VAR}` in the config has no value and no default."""


def expand(value: Any, environ: dict[str, str] | None = None) -> Any:
    """Recursively substitute `${VAR}` / `${VAR:-default}` in every string.

    Ints, bools and None pass through untouched, so numeric settings such as
    `max_bytes_billed` keep their YAML types.
    """
    env_map = os.environ if environ is None else environ

    if isinstance(value, str):

        def sub(m: re.Match[str]) -> str:
            name, default = m.group(1), m.group(2)
            if name in env_map:
                return env_map[name]
            if default is not None:
                return default
            raise MissingConfigEnv(
                f"config references ${{{name}}} but {name} is not set. "
                f"Export it, or give the reference a default: ${{{name}:-value}}"
            )

        return _SUBST.sub(sub, value)
    if isinstance(value, dict):
        return {k: expand(v, environ) for k, v in value.items()}
    if isinstance(value, list):
        return [expand(v, environ) for v in value]
    return value


def load(path: str) -> Config:
    with open(path) as fh:
        raw = expand(yaml.safe_load(fh) or {})
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
