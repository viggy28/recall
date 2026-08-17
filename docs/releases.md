# Release cadence and deployment

This document is for Recall maintainers.

Normal changes merge to `main` without publishing immediately. Use conventional commits (`feat:`, `fix:`, and similar prefixes). When a release is ready, manually run the **Release Please** workflow. It opens or updates a reviewable release pull request with the next version, changelog, and synchronized `package.json` and `pyproject.toml` updates.

Merge the release pull request when it is time to publish, typically after a small batch of user-visible changes or immediately for an urgent fix.

## Publishing

Merging the release pull request creates a GitHub release and tag. The npm publish workflow runs only for that release tag, or through `workflow_dispatch` against an existing tag. It:

1. Resolves the release tag to one immutable commit.
2. Runs blocking 10,000- and 100,000-session retrieval evaluations against that commit.
3. Runs tests, type checking, and package inspection.
4. Publishes `recall-pi` from the same checked-out commit.
5. Reads the package back from npm.
6. Verifies the version, integrity, tarball URL, and required files.

The GitHub release exists before this workflow starts, but npm publication is
blocked when retrieval correctness, artifact generation, or broad catastrophic
performance limits fail. Candidate runs read `benchmarks/retrieval/baseline.json`
and never update it. Baseline changes must be made in a separate reviewed pull
request and retain immutable source-tag and source-commit provenance.

## Maintainer checklist

1. Merge conventional commits normally.
2. Manually run the **Release Please** workflow when ready to prepare a release pull request.
3. Review and merge the generated pull request when ready to ship.
4. Watch the **Publish npm package** workflow and inspect its retrieval artifact.
5. Confirm that the retrieval job and npm read-back verification succeed.
6. For urgent recovery, rerun `workflow_dispatch` against an existing release tag instead of publishing from a laptop; the retrieval gate cannot be weakened by dispatch inputs.

## npm authentication

Configure npm trusted publishing for `recall-pi` to trust this repository's `publish-npm.yml` workflow. If OIDC trusted publishing is unavailable, use a granular npm automation token only as a temporary fallback and rotate it after the recovery publish.
