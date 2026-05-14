# Changelog

All notable changes to hyplan-mmgis-plugin will be documented in this file.

The format is loosely based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project follows [Semantic Versioning](https://semver.org/).

## v0.3.0 (unreleased)

_HyPlan v1.7 features the plugin doesn't expose yet — pattern movement
(`Pattern.translate / move_to / rotate / from_relative`) and geodesic
waypoint placement (`Waypoint.relative_to`), surfaced through the
service and the MMGIS panel.  Plus a 10-minute walkthrough doc for
new users._

### Added

- **`/transform-pattern` endpoint** wrapping HyPlan's whole-pattern
  movement DSL.  Three operations, each preserving the pattern_id so
  any compute sequences referencing it keep working:
  - `translate` — geodesic offset by `north_m` / `east_m`
  - `move_to` — re-anchor at `latitude` / `longitude`
  - `rotate` — rotate by `angle_deg` about an optional
    `(around_lat, around_lon)` pivot (default: pattern center)

  Contained flight lines receive fresh `line_id`s on each transform.
  Five pytest cases cover the happy paths plus 400/404 error paths.

- **Move Pattern panel section (2e)** in the MMGIS tool — picks a
  pattern from the campaign, picks an operation, and applies it
  in place.  The pattern selector mirrors the existing
  `patternsCache` and re-renders whenever the patterns list changes.

## v0.2.0 — 2026-05-13

_DevEx + tests release.  Lays the groundwork for landing larger feature
work in v0.3+ safely._

### Changed

- **GitHub Actions pins bumped to Node 24 runtime.**  `actions/checkout`
  v4 → v6, `actions/setup-python` v5 → v6, `actions/setup-node` v4 →
  v6.  Clears the Node 20 deprecation banner that appeared on every
  v0.1.0 run.

- **Release housekeeping moved inline into `release.yml`.**  At v0.1.0
  the standalone `post-release.yml` never fired because a workflow's
  `GITHUB_TOKEN` does not trigger derivative `push: tags:` workflows.
  `release.yml` now bumps `CITATION.cff`, regenerates the SECURITY.md
  supported-versions table, commits, tags, and creates the GitHub
  Release in one job.  `post-release.yml` deleted.

### Changed (continued)

- **FastAPI `on_event("startup")` → `lifespan` context manager.**
  The legacy startup handler was deprecated in FastAPI 0.110+; moved
  the campaign-rehydration call into an async lifespan context.  No
  behavioural change; clears two DeprecationWarnings on every test
  run.

- **`service/app.py` split into a package.**  The 1951-line monolith
  is now a 10-module layout under `service/`: `state.py` (campaign and
  plan caches + persistence helpers), `errors.py` (HyPlan-aware
  exception → HTTP classifier), `schemas.py` (Pydantic models), and
  `service/routers/{metadata,tiles,wind,generate,compute,export,analysis,lines,patterns,campaigns}.py`
  — one `APIRouter` per functional area.  `service/app.py` is now an
  83-line entry that wires the routers; `uvicorn service.app:app`
  still works.  Largest file in the new layout is
  `routers/analysis.py` at 381 lines.

### Added

- **`.pre-commit-config.yaml`** mirroring CI's lint surface: ruff on
  `service/`, ESLint on `mmgis-tool/HyPlan/`, plus trailing-whitespace,
  EOF-fixer, YAML / JSON / merge-conflict checks.

- **Pytest harness for the service** (`tests/routers/`).  ~50 tests
  organized one file per router plus `test_errors.py` for the
  classifier.  Uses FastAPI's `TestClient` (no uvicorn process), runs
  in ~4 s, makes real HyPlan calls (compute_flight_plan, GlintArc,
  sunpos).  Wired into the `service` job in
  `.github/workflows/tests.yml`.

- **Vitest unit tests for pure frontend helpers** (`tests/js/`).
  19 tests for `getErrorMessage`, `glintColor`, `formatUtcOffset`,
  and `parseLocalDateTimeToUtcIso`.  Helpers extracted from
  `HyPlanTool.js` into a sibling `mmgis-tool/HyPlan/helpers.js` so
  they're importable outside an MMGIS host.  Wired into the
  `frontend` CI job.

- **Coverage % readout** for `/generate-swaths`.  When the request
  includes the user's drawn `target_polygon` (a GeoJSON Feature),
  the response carries a `coverage_fraction` field
  (`area(target ∩ union(swaths)) / area(target)`).  The MMGIS panel
  picks up the drawn polygon via `getDrawnPolygon()` and appends
  "Coverage: XX.X%" to the swath status line.

- **`AGENTS.md` + `.knowledge/`** for AI-agent onboarding, lifted
  from the MMGIS development branch pattern.  Top-level
  agent-facing entry (critical rules, quick start, architecture,
  knowledge index) plus longer-form context in
  `.knowledge/conventions-and-gotchas.md` and
  `.knowledge/knowledge-notes.md`.

- **Structured service errors** with stable codes.  Service responses
  for failures now carry
  `detail: {message, code, operation}` instead of a bare string.
  `HyPlanValueError` / `HyPlanTypeError` consistently map to **400** with
  `code: hyplan_value_error` / `hyplan_type_error` (previously most
  endpoints returned a generic 500 with a stringified traceback for
  these user-actionable errors).  Other `HyPlanError` subclasses map to
  `400 / hyplan_error`; unexpected exceptions stay 500 with
  `code: internal_error` and a full server-side traceback.  Legacy
  string-shaped `detail` is preserved for FastAPI validation errors and
  the simple input-validation paths; the frontend's `getErrorMessage`
  handles both shapes.

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
