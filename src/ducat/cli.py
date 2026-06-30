"""ducat command line.

  ducat run   --config config.yaml [--print] [--remote-write URL] [--tenant T]
  ducat serve --config config.yaml [--port 9090] [--interval 3600]
  ducat providers

`run` is the one-shot path (CI / cron): fetch every enabled provider, then push
dated samples to remote_write, or just print them with --print for a dry run.
`serve` is the long-running scrape path.
"""

from __future__ import annotations

import argparse
import sys

from . import adapters as adapters_mod
from . import config as config_mod
from .focus import CostRow
from .metrics import aggregate


def _fetch_all(cfg: config_mod.Config) -> list[CostRow]:
    rows: list[CostRow] = []
    for name, opts in cfg.enabled_providers().items():
        provider_rows = adapters_mod.get(name).fetch(opts)
        print(f"ducat: {name}: {len(provider_rows)} rows", file=sys.stderr)
        rows.extend(provider_rows)
    return rows


def _print_rows(rows: list[CostRow]) -> None:
    if not rows:
        print("(no cost rows returned)")
        return
    agg = aggregate(rows)  # label -> (net, list)
    width = max(len(f"{p}/{s}") for (p, _a, s, _c) in agg)
    for (provider, account, service, _currency), (net, gross) in sorted(
        agg.items(), key=lambda kv: -kv[1][0]
    ):
        label = f"{provider}/{service}"
        print(f"  {label:<{width}}  net {net:>11,.2f}  list {gross:>11,.2f}  [{account}]")
    tnet = sum(n for n, _ in agg.values())
    tgross = sum(g for _, g in agg.values())
    print(f"  {'TOTAL':<{width}}  net {tnet:>11,.2f}  list {tgross:>11,.2f}  USD")


def _cmd_run(args: argparse.Namespace) -> int:
    cfg = config_mod.load(args.config)
    if args.remote_write:
        cfg.sink.remote_write_url = args.remote_write
    if args.tenant:
        cfg.sink.tenant = args.tenant

    rows = _fetch_all(cfg)

    if args.print or not cfg.sink.remote_write_url:
        _print_rows(rows)
        if not cfg.sink.remote_write_url:
            print("ducat: no remote_write sink configured; printed only.", file=sys.stderr)
        return 0

    from .sinks import remote_write

    sent = remote_write.push(rows, cfg.sink)
    print(f"ducat: pushed {sent} series to {cfg.sink.remote_write_url}", file=sys.stderr)
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    cfg = config_mod.load(args.config)
    from .sinks import serve as serve_sink

    serve_sink.serve(cfg, port=args.port, interval=args.interval)
    return 0


def _cmd_providers(_args: argparse.Namespace) -> int:
    print("\n".join(adapters_mod.available()))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ducat", description="One cost view across every provider.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="one-shot: fetch and push (or --print)")
    run.add_argument("--config", default="config.yaml")
    run.add_argument("--print", action="store_true", help="print rows instead of pushing")
    run.add_argument("--remote-write", help="override sink.remote_write.url")
    run.add_argument("--tenant", help="override X-Scope-OrgID")
    run.set_defaults(func=_cmd_run)

    serve = sub.add_parser("serve", help="long-running /metrics scrape endpoint")
    serve.add_argument("--config", default="config.yaml")
    serve.add_argument("--port", type=int, default=9090)
    serve.add_argument("--interval", type=int, default=3600, help="refresh seconds")
    serve.set_defaults(func=_cmd_serve)

    prov = sub.add_parser("providers", help="list available adapters")
    prov.set_defaults(func=_cmd_providers)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:
        print(f"ducat: error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
