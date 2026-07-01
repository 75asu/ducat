# Changelog

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
