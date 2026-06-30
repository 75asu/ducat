# Contributing

## Commit messages , Conventional Commits (required)

Every commit must follow [Conventional Commits](https://www.conventionalcommits.org/).
This is how versions and the changelog are derived , there is no manual versioning.

```
feat: add the cloudflare adapter        # -> minor bump (0.1.0 -> 0.2.0)
fix: handle empty usageItems            # -> patch bump (0.1.0 -> 0.1.1)
feat!: rename the cost metric           # -> with "!" / BREAKING CHANGE: footer, major bump
docs: ...   chore: ...   refactor: ...   test: ...   ci: ...   # no release on their own
```

A CI check (`commitlint`) rejects non-conforming commits in a PR. For fast local
feedback, install the hook once:

```bash
pip install pre-commit
pre-commit install --hook-type commit-msg
```

## Releases , fully automated, do not touch

You never bump a version, write a changelog by hand, build an image, or push a tag.

1. Merge conventional commits to `main`.
2. **release-please** opens/updates a **Release PR** , it bumps the version in
   `pyproject.toml`, `src/ducat/__init__.py`, and `helm/ducat/Chart.yaml`, and writes
   `CHANGELOG.md`. Edit the changelog wording in that PR if you want.
3. **Merge the Release PR** , the only human action. CI then tags it, creates the
   GitHub Release, and publishes:
   - the image `ghcr.io/75asu/ducat:<version>`
   - the Helm chart `oci://ghcr.io/75asu/charts/ducat` (so `helm upgrade` works)

### Versioning

`0.x` is the pre-production line (anything may change). When ducat is polished and
ready to call production, graduate to `1.0.0` by adding a footer to any commit:

```
feat: <whatever>

Release-As: 1.0.0
```

That single footer is the deliberate "go to prod" signal. Otherwise versions are
computed automatically from commit types.
