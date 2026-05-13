## Summary

<!-- 1-3 sentences: what does this PR change and why? -->

## Component(s) touched

- [ ] Service (`service/`)
- [ ] Frontend MMGIS tool (`mmgis-tool/HyPlan/`)
- [ ] Docs / README
- [ ] CI / tooling

## Type of change

- [ ] Bug fix (non-breaking change which fixes an issue)
- [ ] New feature (non-breaking change which adds functionality)
- [ ] Breaking change (fix or feature that would change request/response shapes
      or UI behaviour relied on by other tools)
- [ ] Documentation only
- [ ] CI / build / tooling

## Test plan

<!-- How did you verify this works? Mark relevant items with [x]. -->

- [ ] `ruff check service/` — clean
- [ ] Service started locally and `/health` responded ok
- [ ] Affected endpoints exercised with curl or the MMGIS UI
- [ ] Frontend rebuilt inside an MMGIS checkout (if HyPlanTool.js changed)
- [ ] Docker image builds (`docker build service/`) — if service changed

## CHANGELOG

- [ ] CHANGELOG.md updated under the `## Unreleased` section, OR this
      change is internal-only and CHANGELOG-exempt.

## Backward compatibility

<!-- For changes touching service endpoints, MMGIS config keys, or
     persisted campaign on-disk format: confirm what is preserved and
     what is not. -->
