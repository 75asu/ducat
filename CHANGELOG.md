# Changelog

## [0.5.0](https://github.com/75asu/ducat/compare/v0.4.1...v0.5.0) (2026-08-04)


### Features

* **config:** substitute ${VAR} references in config values ([8396103](https://github.com/75asu/ducat/commit/83961034548dc413ea32fd8b3d6d926b54728e84))
* **gcp:** add Cloud Billing adapter with historical Cost table CSV import ([73cb383](https://github.com/75asu/ducat/commit/73cb38362272b58131a303a1cb980bef1e6c368f))
* **helm:** support extra volumes for supplying historical CSVs ([e0edee6](https://github.com/75asu/ducat/commit/e0edee6704c148be56ea531fcf275cf9999393d1))
* **local:** docker compose stack with env-driven config ([0730725](https://github.com/75asu/ducat/commit/0730725f412634f9b48ca925dfe780e34669c20f))
* **metrics:** add sub_account label for per-project cost attribution ([a392581](https://github.com/75asu/ducat/commit/a392581531ec26647087e511614a259eba84479e))


### Bug Fixes

* **ci:** install the dev extra so ruff is available ([5b8f6f9](https://github.com/75asu/ducat/commit/5b8f6f94102d2ba54a28e95bc9d61e63adedd2de))
* **dashboards:** scope every panel by project and service ([bed813b](https://github.com/75asu/ducat/commit/bed813b164c12b3f0c60512d26a127a1d638a531))
* **gcp:** validate source before importing the optional bigquery dep ([09a0ea5](https://github.com/75asu/ducat/commit/09a0ea5092319191d8ce81276c049b6096429c6e))

## [0.4.1](https://github.com/75asu/ducat/compare/v0.4.0...v0.4.1) (2026-07-20)


### Bug Fixes

* isolate per-account/provider fetch failures and add scrape guardrails (FP-1288) ([ba2d9d4](https://github.com/75asu/ducat/commit/ba2d9d4f18226b8bd947c0ff842c6c06f7379ca0))


### Documentation

* landing page + OG card (GitHub Pages) ([2f3a6f1](https://github.com/75asu/ducat/commit/2f3a6f1bdc926ad690886e6cf300c8c465b084b3))

## [0.4.0](https://github.com/75asu/ducat/compare/v0.3.0...v0.4.0) (2026-07-03)


### Features

* **aws:** per-account credentials for multi-account cost ([713f9d8](https://github.com/75asu/ducat/commit/713f9d875fdc0674573b0b86f18b3bd1bbaa6848))


### Bug Fixes

* root-anchor local config ignore so examples/config.yaml is tracked ([8fd942b](https://github.com/75asu/ducat/commit/8fd942b5892cf346ef51ff1cd2de46e62d35d6ea))

## [0.3.0](https://github.com/75asu/ducat/compare/v0.2.1...v0.3.0) (2026-07-01)


### Features

* per-month cost history and friendly account names as labels ([3f09ab6](https://github.com/75asu/ducat/commit/3f09ab65819c9bb6a6007f3a9356764b16db486d))

## [0.2.1](https://github.com/75asu/ducat/compare/v0.2.0...v0.2.1) (2026-07-01)


### Bug Fixes

* bound dashboard snapshot queries to a 1h window so stale series do not double-count ([aa88915](https://github.com/75asu/ducat/commit/aa88915cf08339a26591141e0997ea7b5e1a2c46))
* set ServiceMonitor honorLabels so provider/service labels survive the operator's target labels ([91e2187](https://github.com/75asu/ducat/commit/91e2187a2be179a15ca2b1f1ad8d490ad626a5bf))

## [0.2.0](https://github.com/75asu/ducat/compare/v0.1.0...v0.2.0) (2026-07-01)


### Features

* add AWS Cost Explorer and Cloudflare cost adapters ([a11b9d5](https://github.com/75asu/ducat/commit/a11b9d5185b5584f525ceda21d8e2d55b50045a1))


### Bug Fixes

* show data on all dashboard panels; add list-cost, savings, by-account and by-service views ([c2652a6](https://github.com/75asu/ducat/commit/c2652a678e294090587228096990271c89035990))

## 0.1.0 (2026-06-30)


### Features

* initial ducat cost exporter with GitHub adapter ([c17e9cc](https://github.com/75asu/ducat/commit/c17e9cccfbb495e9eb753cfec50b97ccf9b6f782))

## Changelog

This file is maintained automatically by [release-please](https://github.com/googleapis/release-please)
from [Conventional Commit](https://www.conventionalcommits.org/) messages. Curate the
wording in the Release PR before merging; do not hand-edit already-released sections.
