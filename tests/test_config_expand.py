"""Tests for ${VAR} substitution in config.

Why this matters beyond convenience: without it, a working config has to carry the
billing account id, project and dataset of whoever wrote it. That makes the shipped
example either useless (placeholders that don't run) or a leak (real identifiers in
a public repo). Substitution is what lets one committed file be both.
"""

from __future__ import annotations

import pytest

from ducat.config import MissingConfigEnv, expand


def test_required_var_is_substituted():
    assert expand("${P}", {"P": "acme-prod"}) == "acme-prod"


def test_default_is_used_when_unset():
    assert expand("${P:-focus}", {}) == "focus"


def test_value_wins_over_default():
    assert expand("${P:-focus}", {"P": "standard"}) == "standard"


def test_empty_default_yields_empty_string():
    """Used for optional settings where the adapter falls back on falsiness."""
    assert expand("${P:-}", {}) == ""


def test_missing_required_var_raises_rather_than_emptying():
    """An empty project name surfaces later as an inscrutable API error.

    Failing at load time names the variable the operator forgot, which is the
    difference between a two-second fix and reading a BigQuery stack trace.
    """
    with pytest.raises(MissingConfigEnv, match="DUCAT_GCP_PROJECT"):
        expand({"project": "${DUCAT_GCP_PROJECT}"}, {})


def test_substitution_is_recursive_through_dicts_and_lists():
    got = expand(
        {"providers": {"gcp": {"project": "${P}", "accounts": ["${A}", "static"]}}},
        {"P": "acme-prod", "A": "111"},
    )
    assert got == {"providers": {"gcp": {"project": "acme-prod", "accounts": ["111", "static"]}}}


def test_non_strings_keep_their_yaml_types():
    """max_bytes_billed must stay an int, enabled must stay a bool."""
    got = expand({"max_bytes_billed": 21474836480, "enabled": True, "to": None}, {})
    assert got["max_bytes_billed"] == 21474836480
    assert got["enabled"] is True
    assert got["to"] is None


def test_embedded_and_repeated_references():
    assert expand("prefix-${P}-${P}-suffix", {"P": "x"}) == "prefix-x-x-suffix"


def test_bare_dollar_is_left_alone():
    """PromQL and passwords contain dollars; only ${...} is a reference."""
    assert expand("cost in $USD, $5", {}) == "cost in $USD, $5"
