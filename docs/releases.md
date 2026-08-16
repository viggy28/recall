# Release cadence and deployment

This document is for Recall maintainers.

Normal changes merge to `main` without publishing immediately. Use conventional commits (`feat:`, `fix:`, and similar prefixes). When a release is ready, manually run the **Release Please** workflow. It opens or updates a reviewable release pull request with the next version, changelog, and synchronized `package.json` and `pyproject.toml` updates.

Merge the release pull request when it is time to publish, typically after a small batch of user-visible changes or immediately for an urgent fix.

## Publishing

Merging the release pull request creates a GitHub release and tag. The npm publish workflow runs only for that release tag, or through `workflow_dispatch` against an existing tag. It:

1. Runs tests, type checking, and package inspection.
2. Publishes `recall-pi` from the checked-out tag.
3. Reads the package back from npm.
4. Verifies the version, integrity, tarball URL, and required files.

## Maintainer checklist

1. Merge conventional commits normally.
2. Manually run the **Release Please** workflow when ready to prepare a release pull request.
3. Review and merge the generated pull request when ready to ship.
4. Watch the **Publish npm package** workflow.
5. Confirm that npm read-back verification succeeds.
6. For urgent recovery, rerun `workflow_dispatch` against an existing release tag instead of publishing from a laptop.

## npm authentication

Configure npm trusted publishing for `recall-pi` to trust this repository's `publish-npm.yml` workflow. If OIDC trusted publishing is unavailable, use a granular npm automation token only as a temporary fallback and rotate it after the recovery publish.
