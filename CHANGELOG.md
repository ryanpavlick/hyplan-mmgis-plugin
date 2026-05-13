# Changelog

All notable changes to hyplan-mmgis-plugin will be documented in this file.

The format is loosely based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project follows [Semantic Versioning](https://semver.org/).

## Unreleased

_No changes yet._

## v0.1.0 — 2026-05-12

Initial public-facing release of the MMGIS plugin for HyPlan.  Tracks
HyPlan v1.7.0-staging APIs (`Campaign`, `Pattern`,
`compute_flight_plan`, `greedy_optimize`, `GlintArc`,
`solar_position_increments`).

### Service (`service/app.py`)

- FastAPI bridge between the MMGIS HyPlan tool and the core HyPlan
  library, with on-disk campaign persistence under
  `HYPLAN_CAMPAIGNS_DIR`.
- Endpoints for campaign lifecycle, line generation, pattern generation
  and mutation, line transforms, plan computation, route optimization,
  swath / glint / solar analysis, and KML / GPX export.
- FAA aeronautical chart tile proxy (`/faa-tile/{kind}/{z}/{y}/{x}`)
  with auto-refreshed AIRAC cycle scraped from vfrmap.com, and a
  pre-baked `/imagery-layers` block exposing FAA charts and NASA GIBS
  cloud / satellite layers as MMGIS-native tile layers.
- Docker entrypoint that installs HyPlan from a mounted source tree.

### Frontend (`mmgis-tool/HyPlan/`)

- MMGIS tool panel with sections for campaign setup, flight-line
  generation, individual line editing, pattern generation, line
  selection / ordering / transforming, plan computation, and analysis
  overlays.
- Glint analysis overlay coloured with a RdYlBu-PowerNorm map to
  match the reference notebooks.
- Solar-position helper plot.
- Wind, swath, glint, and pattern Leaflet layers managed through a
  shared disown-and-remove lifecycle so MMGIS layer state stays clean.

### Open-source infrastructure

- `LICENSE.md` (Apache 2.0), `CITATION.cff`, `CODE_OF_CONDUCT.md`,
  `CONTRIBUTING.md`, `CONTRIBUTORS.md`, `SECURITY.md`, GitHub issue /
  PR templates.
- CI workflows in `.github/workflows/`: `tests.yml` (ruff +
  service-start smoke + Docker build smoke + ESLint), `release.yml`
  (manual tag + GitHub Release from CHANGELOG), `post-release.yml`
  (auto-bump `SECURITY.md` and `CITATION.cff` on tag push).
- Frontend lint tooling: `eslint.config.js` (flat config,
  `@eslint/js` recommended) and a `package.json` with `lint` and
  `validate:config` scripts.
