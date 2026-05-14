# Changelog

All notable changes to hyplan-mmgis-plugin will be documented in this file.

The format is loosely based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project follows [Semantic Versioning](https://semver.org/).

## v0.4.0 (unreleased)

_Persistence + collaboration release.  Move campaigns off `/tmp` flat
files, let users round-trip a mission as JSON, guard against
concurrent overwrites._

### Added

- **Campaign import / export as a single JSON bundle.**  Two new
  endpoints:
  - `GET /campaigns/{campaign_id}/export` packs the campaign's
    on-disk tree (campaign.json + flight_lines + patterns) into one
    JSON object.  Export artifacts (KML / GPX / KMZ) are excluded —
    they regenerate from `/compute-plan` + `/export` and would
    otherwise bloat the bundle with stale derived data.
  - `POST /campaigns/import` accepts a bundle and materializes a new
    campaign.  Defaults to a **fresh UUID** so re-importing into a
    live service can't clobber state; pass `replace: true` to keep
    the bundle's id (e.g. restoring a known mission from backup).
    Optional `name` override.  Path-traversal guard on bundle file
    paths; format/version checks reject foreign or future bundles
    with a structured 400.
  - Bundle envelope: `{format, format_version, exported_at,
    service_version, campaign_id, name, bounds, revision, files}`
    so future readers can negotiate.
  - 9 new pytest cases (round-trip, fresh-UUID default, replace mode,
    rename, wrong-format reject, future-version reject, path-traversal
    reject, empty-files reject, lines + patterns round-trip).
- **Import / Export panel section** (collapsible, inside Section 1
  Campaign).  Export downloads `<mission>_campaign.json` via a
  client-side blob URL; Import takes a `.json` file via a hidden
  file picker, posts the bundle, and adopts the imported campaign
  as the active one.

- **Concurrent-edit guard via `If-Match: <revision>` precondition.**
  Two browsers can no longer silently clobber each other's edits.
  - Backend: new `service.state.check_revision(campaign, if_match)`
    helper.  When the client sends `If-Match: N` and the server's
    `campaign.revision` doesn't match, the write is rejected with
    **`409 Conflict`** + a structured detail
    (`code: revision_mismatch`, plus `client_revision` and
    `server_revision` so the UI can show the actual drift).
    Garbage value → `400` + `code: bad_if_match`.  Missing header
    is a no-op (legacy clients can still write).
  - Threaded through every mutating endpoint: `/generate-lines`,
    `/add-line`, `/edit-line`, `/delete-line`, `/transform-lines`,
    `/generate-pattern`, `/delete-pattern`, `/replace-pattern`,
    `/transform-pattern`.
  - Frontend (`HyPlanTool.js`): two new helpers — `trackRevision()`
    park the server's latest revision in module state from every
    response that carries one; `withIfMatch()` merges the
    `If-Match` header into a write-fetch's headers object.  Wired
    into all 21 write fetches and chained on all 25 response
    `.then()`s.  Frontend now always sends `If-Match` once a
    campaign exists; reads skip the precondition.
  - 7 new pytest cases (no header is unconditional, matching rev
    writes, stale rev 409s, garbage header 400s, plus the same
    guard on `/transform-lines`, `/generate-pattern`,
    `/transform-pattern`).

- **SQLite-backed campaign store** replaces the v0.1–v0.3 flat-file
  tree.  Each campaign lives in one row of one table; the row's
  ``bundle_json`` column holds the same JSON envelope that
  `/campaigns/{id}/export` emits, so persistence and the
  share-a-mission feature share their encoding (see
  `service.serialize`).  Two new env vars:
  - `HYPLAN_CAMPAIGNS_DB` — path to the SQLite file (default:
    `${HYPLAN_CAMPAIGNS_DIR}/campaigns.sqlite`).
  - `HYPLAN_CAMPAIGNS_DIR` — kept for backwards compat; on startup
    `service.store.migrate_filesystem_to_db()` imports any
    legacy `<uuid>/campaign.json` trees that aren't already in the
    store.  Idempotent.

  Wins: **atomic writes** (UPSERT under a single transaction; no
  more partial-on-disk-tree failure modes), **no `/tmp` fragility**
  (point the env var at any persistent path), **single artifact**
  to back up, ship, or mount read-only.  WAL journaling enabled
  for concurrent reads.

  Plumbing:
  - New `service/serialize.py`: `campaign_to_bundle()` /
    `bundle_to_campaign()` factored out of the router into a
    shared module so both the import/export endpoints and the
    store use the same encoding.
  - New `service/store.py`: `init_store(path)`,
    `save_campaign(c)` (UPSERT), `load_campaign(id)`,
    `iter_campaigns()`, `list_campaign_meta()`,
    `delete_campaign(id)`, `migrate_filesystem_to_db(dir)`.
  - `service.state.persist_campaign()` and
    `load_persisted_campaigns()` route through the store; legacy
    flat-file write path retired.
  - `tests/conftest.py` autouse fixture initializes a fresh
    SQLite db per test (in `tmp_path / campaigns.sqlite`) so the
    suite stays hermetic.

  New endpoint: `GET /campaigns` — lightweight metadata list of
  all persisted campaigns (id, name, bounds, revision,
  updated_at).  Sets up the v0.4 multi-campaign UI.

  10 new pytest cases in `tests/test_store.py`: round-trip, missing
  → None, UPSERT, iteration, delete, migration from legacy dir +
  idempotence, no-op when legacy dir is absent, persistence across
  in-memory clear (simulates service restart), `/campaigns`
  listing shape, env-var exposure.  Plus
  `test_generate_lines_persists_campaign` rewritten to check the
  store instead of the old on-disk tree.

  Total pytest count: **87** (was 77).

## v0.3.0 — 2026-05-14

_HyPlan v1.7 features the plugin doesn't expose yet — pattern movement
(`Pattern.translate / move_to / rotate / from_relative`) and geodesic
waypoint placement (`Waypoint.relative_to`), surfaced through the
service and the MMGIS panel.  Plus a 10-minute walkthrough doc for
new users, a UI rework (accordion + map-object context menus), and
the [documentation site](https://ryanpavlick.github.io/hyplan-mmgis-plugin/)._

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

- **`/resolve-relative` endpoint** exposing HyPlan's
  `Waypoint.relative_to` (Vincenty) as a geodesic calculator: pass
  `{anchor_lat, anchor_lon, bearing_deg, distance_m}`, get back
  `{latitude, longitude}` plus the echoed inputs.  Composes with
  existing endpoints (`/add-line`, `/edit-line`, `/compute-plan`
  waypoint entries) — no per-feature "*-relative" variant needed.
  Three pytest cases (north 1 km, east 1 km, end-to-end composition
  with `/add-line`).

- **Relative-to calculator** in the MMGIS Add-Line section.  A
  collapsible `<details>` panel takes an anchor (lat/lon), bearing
  (°true), and distance (m), and either displays the resolved
  coordinates for copy/paste or — if the user has already clicked a
  first endpoint on the map — offers a "Use as line endpoint" button
  that closes the line via `/add-line`.

- **`docs/WALKTHROUGH.md`** — a 10-minute, terminal-by-terminal
  recipe that takes a new user from a clean MMGIS clone to drawing
  flight lines on the map.  Covers the workspace layout, the two
  MMGIS-side patches required for symlink-friendly plugin dev, the
  Docker Compose service for Postgres + hyplan-service, hot-reload
  MMGIS via `npm start`, mission config, and a step-by-step
  exercise of the panel that hits the v0.3 features (Move Pattern,
  Coverage % readout).  Linked from `README.md` and `AGENTS.md`.

- **Documentation site at
  <https://ryanpavlick.github.io/hyplan-mmgis-plugin>**, built with
  [Just the Docs](https://just-the-docs.com).  Auto-deploys on push
  to `main` via `.github/workflows/docs.yml`.  Pages: Home
  (architecture + status), Walkthrough, Service API, Code Map.  Front-
  matter `nav_order` keys keep the sidebar in workflow order
  (Walkthrough first for new users, API + Code Map for contributors).
  Local preview via `cd docs && bundle exec jekyll serve` against
  the committed `Gemfile`.

### Changed

- **Panel reorganized as accordion sections.**  Each of the 13
  workflow sections is now a `<details>` element; only Section 1
  (Campaign) is open by default.  Custom chevron + hover styling
  for the `<summary>` headers.  Eliminates the previous vertical-
  scroll wall; users see only what they're working on.

- **Right-click context menus on map objects.**  Per-object
  operations are now available in place — no more "open Section 3b,
  click line in list, configure, apply".
  - Right-click a flight line: Reverse direction, Rotate ±15°,
    Offset across ±500 m, Shift N/E 1 km, Delete.  Wired to the
    existing `/transform-lines` and `/delete-line` endpoints with
    pre-filled params; the panel sections remain available for
    custom params and batch ops.
  - Right-click a pattern (waypoint-based; line-based pattern legs
    are still per-line): Translate N/S/E/W 1 km, Rotate ±15°,
    Delete pattern.  Wired to `/transform-pattern` and
    `/delete-pattern`.
  - Menu auto-dismisses on outside click or Escape; clips against
    the viewport edges.  Lives outside `#hyplanTool` so it can
    overlay the entire map.

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
