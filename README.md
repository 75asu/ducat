# ducat

**One cost view across every provider.** Cloud, SaaS, and AI spend, normalized on the FinOps [FOCUS](https://focus.finops.org/) spec and exported to Prometheus / Grafana.

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

> Named for the gold ducat, the coin that for centuries was accepted across every border , one unit of value everywhere. `ducat` does the same for your bills: one normalized cost signal across every provider you use.

---

## Why

Every provider has a billing API, and none of them speak Prometheus. Tools that *do* speak Prometheus (OpenCost, Kubecost) only see in-cluster Kubernetes cost, not your actual cloud + SaaS + AI invoices. So whole-account, multi-provider cost never reaches the Grafana you already run.

`ducat` is the thin layer in between: a small set of per-provider adapters that each call a billing API, normalize the result to a common FOCUS-shaped record, and emit two Prometheus metrics , `ducat_cost_usd` (net / billed) and `ducat_list_cost_usd` (list / pre-discount), both labeled `{provider, billing_account, service, currency}`. The gap between them is what your committed-use/included plans save you. Point Grafana at them and you have one board for everything.

It is deliberately *not* a platform. No database, no UI, no operator. The storage and dashboards are the Prometheus + Grafana you already have; ducat only does the fetch-and-normalize.

## How it works

```
        adapters/  (one file per provider)
   github · cloudflare · aws · gcp · openai · anthropic · ...
                        │
                        │  each returns FOCUS-shaped CostRows
                        ▼
         core: normalize → ducat_cost_usd + ducat_list_cost_usd
              ┌─────────────┴─────────────┐
              ▼                           ▼
        serve: /metrics            run: remote_write → Mimir/Prometheus
        (Prometheus scrapes)       (one-shot, dated samples; for CI/cron)
                        │
                        ▼
                     Grafana
```

Two run modes, same image:

- **`serve`** , long-running, exposes `/metrics`, scraped by Prometheus/Alloy. The conventional exporter UX.
- **`run`** , one-shot: fetch, push dated samples via `remote_write`, exit. For CI/cron, and the right shape for a daily bill (each sample is stamped with the day it represents). Pushgateway is intentionally not used (it strips timestamps).

Adding a provider is one file implementing `fetch(opts) -> list[CostRow]`. Nothing downstream changes.

## Providers

| Provider | Status | Source | Auth |
|---|---|---|---|
| GitHub | ✅ | Enhanced billing usage API (`netAmount`) | fine-grained PAT, org `Administration: read` |
| Cloudflare | 🚧 next | `paygo-usage` + `subscriptions` | API token with `Billing Read` |
| AWS | 🔜 | Cost Explorer `GetCostAndUsage` | keyless (OIDC / assume-role) |
| GCP (incl. Vertex/Gemini) | 🔜 | BigQuery billing export | keyless (Workload Identity) |
| OpenAI | 🔜 | `/v1/organization/costs` | admin key |
| Anthropic | 🔜 | `/v1/organizations/cost_report` | admin key |

## Quickstart

```bash
pip install -e ".[push]"          # or run the container image

cp examples/config.yaml config.yaml   # edit: your org + token env var
export GITHUB_TOKEN=github_pat_...     # fine-grained PAT, org Administration: read

ducat run --print                  # dry run: print the cost breakdown
ducat run                          # push dated samples to remote_write
ducat serve --port 9090            # or: long-running /metrics for Prometheus
```

`ducat run --print` output:

```
  github/copilot   net    908.20   list    908.27   [my-org]
  github/actions   net      9.44   list    109.79   [my-org]
  TOTAL            net    917.64   list  1,019.02   USD
```

## Configuration

See [`examples/config.yaml`](examples/config.yaml). Secrets are never in the file , a provider names an env var (`token_env`) and the value is read from the environment, so the same config is safe to commit and the secret comes from your secret manager or CI.

## Deploy on Kubernetes (Helm)

```bash
# 1. Put your provider tokens in a Secret (never in values)
kubectl create secret generic ducat-secrets --from-literal=GITHUB_TOKEN=github_pat_...

# 2. Install (serve mode, scraped by Prometheus) , from the published OCI chart
helm install ducat oci://ghcr.io/75asu/charts/ducat \
  --set config.providers.github.org=my-org \
  --set 'envFrom[0].secretRef.name=ducat-secrets' \
  --set serviceMonitor.enabled=true        # on a Prometheus-Operator cluster
# (or from a clone of this repo: helm install ducat ./helm/ducat ...)
```

For the one-shot push model instead of scraping (e.g. keyless cloud creds via a CronJob):

```bash
helm install ducat oci://ghcr.io/75asu/charts/ducat \
  --set mode=cronjob \
  --set cronjob.schedule="0 * * * *" \
  --set config.sink.remote_write.url=http://mimir-gateway.mimir.svc/api/v1/push \
  --set config.sink.remote_write.tenant=my-tenant \
  --set 'envFrom[0].secretRef.name=ducat-secrets'
```

Tokens always come from an existing Secret via `envFrom` , they are never placed in the chart values or the ConfigMap.

## Dashboard

A Grafana dashboard ships with the chart ([`helm/ducat/dashboards/ducat.json`](helm/ducat/dashboards/ducat.json)) , total spend, by provider, by service, over time. With `dashboards.enabled=true` (default) it is published as a sidecar-labeled ConfigMap that the Grafana kiwigrid sidecar auto-imports. Not on Helm? Import the JSON straight into Grafana. On grafana-operator clusters, point a `GrafanaDashboard` CR at the same JSON and set `dashboards.enabled=false`.

## Access control

ducat is an exporter , it emits metrics and has no users or UI, so "who may see which costs" is governed by your visualization/storage layer, not by ducat. Two tiers:

- **Coarse (free, OSS Grafana):** drop the dashboard into a Grafana **folder** and grant **View** to a team (e.g. SRE + Finance). Set `dashboards.folder` so the sidecar files it there, then restrict that folder in Grafana. Not everyone needs to see every bill.
- **Fine-grained (per-provider within a shared view):** use label-based access control (Grafana Enterprise/Cloud LBAC, or Mimir per-tenant/label policies) keyed on ducat's `provider` label. ducat keeps its labels (`provider`, `billing_account`, `service`) clean precisely so these policies slice cleanly.

ducat deliberately does not implement its own RBAC , that belongs to the layer that serves the data.

## Releases

Versioning and releases are fully automated by [release-please](https://github.com/googleapis/release-please) , see [CONTRIBUTING.md](CONTRIBUTING.md). Commit with [Conventional Commits](https://www.conventionalcommits.org/); merging the auto-generated Release PR cuts the release. `0.x` is the pre-production line; `1.0.0` will mark the first production release. Every release publishes:

- the image `ghcr.io/75asu/ducat:<version>`
- the Helm chart `oci://ghcr.io/75asu/charts/ducat`

## Contributing

Conventional Commits are enforced (commitlint, in CI and via a local pre-commit hook). See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

Apache-2.0. See [LICENSE](LICENSE).
