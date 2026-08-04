"""Guard: every metric a provisioned dashboard queries must actually be exposed.

Why this exists: the "Adapter errors" panel originally queried `ducat_scrape_error`,
but prometheus_client exposes Counters with a `_total` suffix. The bare name matched
nothing, and the panel's `or vector(0)` fallback rendered a confident green 0 -- so
the one panel whose job is to say "these numbers are incomplete" reported healthy
while the adapter was failing. Exactly the silent-blank-board failure the metric was
added to prevent.

A dashboard is code. This test treats it that way.
"""

from __future__ import annotations

import json
import pathlib
import re

from prometheus_client import CollectorRegistry, Counter

from ducat.metrics import METRIC_LIST, METRIC_NET, METRIC_SCRAPE_ERROR, build_registry

# EVERY dashboard in the repo, not just the local one. The chart's dashboard is the
# artifact users actually get, and it went a long time with none of these checks
# applied to it: no project filter, no trustworthiness panel, and the same
# All-drops-empty-labels bug. A guard that only covers your own dev copy is theatre.
DASHBOARDS = sorted(
    [
        *pathlib.Path("deploy/local/grafana/provisioning/dashboards").glob("*.json"),
        *pathlib.Path("helm/ducat/dashboards").glob("*.json"),
    ]
)


def _exposed_series() -> set[str]:
    """Series names ducat really emits, taken from the code, not from memory."""
    names: set[str] = set()

    # Gauges keep their name.
    for metric in build_registry([]).collect():
        names.add(metric.name)
        for sample in metric.samples:
            names.add(sample.name)

    # The scrape-error Counter lives on the default registry; rebuild it in
    # isolation so we capture the real exposed suffixes (_total / _created).
    # A Counter with labels emits NO samples until a labelled child exists, so
    # touch one -- otherwise we'd only see the base name and miss the very
    # `_total` suffix this test is here to catch.
    reg = CollectorRegistry()
    counter = Counter(METRIC_SCRAPE_ERROR, "probe", ("provider", "account"), registry=reg)
    counter.labels(provider="probe", account="probe").inc(0)
    for metric in reg.collect():
        names.add(metric.name)
        for sample in metric.samples:
            names.add(sample.name)
    return names


def _queried_metrics(expr: str) -> set[str]:
    """Pull ducat_* identifiers out of a PromQL expression."""
    return set(re.findall(r"\bducat_[a-z0-9_]+", expr))


def test_dashboards_exist():
    assert DASHBOARDS, "no provisioned dashboards found"


def test_every_queried_metric_is_actually_exposed():
    exposed = _exposed_series()
    # sanity: the three we care about are discoverable
    assert METRIC_NET in exposed and METRIC_LIST in exposed
    assert f"{METRIC_SCRAPE_ERROR}_total" in exposed

    problems: list[str] = []
    for path in DASHBOARDS:
        dash = json.loads(path.read_text())
        for panel in dash.get("panels", []):
            for target in panel.get("targets", []):
                expr = target.get("expr", "")
                for name in _queried_metrics(expr):
                    if name not in exposed:
                        problems.append(
                            f"{path.name} panel {panel.get('id')} "
                            f"({panel.get('title')!r}) queries {name!r}, which ducat "
                            f"never exposes. Did you mean one of: "
                            f"{sorted(n for n in exposed if name in n)}?"
                        )
    assert not problems, "dashboard queries reference non-existent metrics:\n  " + "\n  ".join(problems)


def test_error_panel_uses_the_total_suffix():
    """Specifically pin the bug that motivated this file."""
    for path in DASHBOARDS:
        for panel in json.loads(path.read_text()).get("panels", []):
            title = (panel.get("title") or "").lower()
            if "error" in title:
                exprs = " ".join(t.get("expr", "") for t in panel.get("targets", []))
                assert "ducat_scrape_error_total" in exprs, (
                    f"{path.name}: error panel must query the _total series, got: {exprs}"
                )


def test_filter_variables_are_actually_wired_into_queries():
    """A template variable nobody filters on is a dropdown that does nothing.

    The project selector was defined and populated from label_values, but no panel
    referenced $project, so choosing a project changed nothing on screen. Any panel
    that already scopes by $provider is a panel a user expects every other filter to
    scope too, so this checks all of them rather than one by name.
    """
    # Variables that select a subset of the data. `month` is excluded: it is
    # single-select and already interpolated as an exact matcher everywhere.
    FILTERS = ("project", "service")

    problems: list[str] = []
    for path in DASHBOARDS:
        dash = json.loads(path.read_text())
        names = {v["name"] for v in dash.get("templating", {}).get("list", [])}
        for var in FILTERS:
            if var not in names:
                continue
            for panel in dash.get("panels", []):
                exprs = " ".join(t.get("expr", "") for t in panel.get("targets", []))
                if "$provider" in exprs and f"${var}" not in exprs:
                    problems.append(
                        f"{path.name} panel {panel.get('id')} ({panel.get('title')!r}) "
                        f"filters $provider but ignores ${var}: {exprs}"
                    )
    assert not problems, "template variable not wired into panel queries:\n  " + "\n  ".join(problems)


def test_all_option_cannot_drop_empty_label_series():
    """`All` must interpolate to a regex, not to the list of known label values.

    label_values never returns the empty string, so without allValue='.*' the All
    option expands to an explicit alternation and silently drops series whose label
    is empty. Here that is account-level spend (Support, Invoice, tax, committed-use)
    which has no project, so totals would quietly understate the bill.
    """
    for path in DASHBOARDS:
        for var in json.loads(path.read_text()).get("templating", {}).get("list", []):
            if var.get("includeAll"):
                assert var.get("allValue") == ".*", (
                    f"{path.name}: variable {var['name']!r} has includeAll but "
                    f"allValue={var.get('allValue')!r}; set '.*' or All drops "
                    f"series with an empty {var['name']} label"
                )


def test_health_panel_is_not_a_bare_cumulative_counter():
    """A latching counter is worse than no panel: it reads red forever.

    `sum(ducat_scrape_error_total)` never decreases, so one transient failure at
    startup pinned the "0 = numbers trustworthy" stat at 1 permanently, which trains
    people to ignore it. The health question needs a range window.
    """
    for path in DASHBOARDS:
        for panel in json.loads(path.read_text()).get("panels", []):
            if "error" not in (panel.get("title") or "").lower():
                continue
            for t in panel.get("targets", []):
                expr = t.get("expr", "")
                assert re.search(r"(increase|rate)\s*\(", expr), (
                    f"{path.name}: health panel {panel.get('title')!r} shows a raw "
                    f"cumulative counter and will latch; wrap it in increase()/rate(): {expr}"
                )


def test_every_dashboard_surfaces_the_trustworthiness_signal():
    """A cost board with no scrape-error panel silently lies when a source fails.

    ducat exposes ducat_scrape_error_total precisely so a partial refresh is visible
    rather than reading as a real drop in spend. A dashboard that never queries it
    presents incomplete figures as complete, which is the failure mode the metric was
    added for. Cheap to include, and there is no dashboard where it is unwanted.
    """
    missing = [
        path.name
        for path in DASHBOARDS
        if "ducat_scrape_error" not in path.read_text()
    ]
    assert not missing, (
        "dashboards with no scrape-error panel, so a failed source shows as a spend "
        f"drop instead of an error: {missing}"
    )
