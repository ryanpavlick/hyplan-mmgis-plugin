# hyplan-mmgis-plugin - AI agent context

**Project**: hyplan-mmgis-plugin
**Version**: 0.2.0 (unreleased)
**Last Updated**: 2026-05-13

> **New to this repo?**  Skim this file top to bottom (~5 min), then
> check [.knowledge/](./.knowledge/) for non-obvious lessons we've
> learned during development.  [CONTRIBUTING.md](CONTRIBUTING.md) is
> the human-facing onboarding doc; this file is the agent-facing one.

## What this is

An [MMGIS](https://github.com/NASA-AMMOS/MMGIS) plugin for interactive
flight planning with [HyPlan](https://github.com/ryanpavlick/hyplan).
Two components that ship together:

- `service/` - FastAPI Python service wrapping HyPlan
- `mmgis-tool/HyPlan/` - browser-side MMGIS tool plugin (JS + CSS)

The frontend is intentionally thin: it owns map / UI state and
delegates planning work to the service over HTTP / JSON.

## Critical Rules

- **Never add `Co-Authored-By: Claude ...` trailers** to commits.  At
  v0.1.0 four commits had this trailer and the user requested a
  force-push history rewrite to remove them.  Persisted as a memory
  rule for this repo; do not regress.

- **Lint must be clean before commit.**  CI runs `ruff check service`
  and `npm run lint`.  Pre-commit hooks (`.pre-commit-config.yaml`)
  enforce both locally.  If a hook fails, fix the underlying issue;
  never bypass with `--no-verify`.

- **The service entrypoint is `service.app:app`**, not `app:app`.
  The Docker image and CI both rely on this contract.  If you split
  or move modules, update both `service/Dockerfile` and
  `service/entrypoint.sh`.

- **Campaign data lives in `HYPLAN_CAMPAIGNS_DIR`, never the repo.**
  Default is `/tmp/hyplan-campaigns`.  The directory is also gitignored
  via `/campaigns/` and `/tmp-campaigns/` patterns.

- **Service errors use the structured classifier.**  Don't write new
  `except Exception as exc: raise HTTPException(500, ...)` blocks;
  call `service.errors.raise_http(operation, exc)` instead so the
  HyPlan-aware status / code classification stays consistent.

- **HyPlan compatibility is staging-tracking, not tag-tracking.**  CI
  installs HyPlan from `ryanpavlick/hyplan@main`.  An API change in
  HyPlan main can break this repo before any released HyPlan tag does
  - that's an accepted risk in v0.x.  Don't pin the HyPlan dependency
  to a release tag without coordinating.

- **PRs often touch both halves.**  Service and frontend usually move
  together; a `/transform-pattern` endpoint without a UI button (or
  vice versa) is half a feature.  Acceptable as a stepping stone but
  call it out in the PR.

## Quick Start

End-to-end walkthrough (clean MMGIS → drawing flight lines on the
map in ~10 min): [docs/WALKTHROUGH.md](docs/WALKTHROUGH.md).

Service-only loop (no MMGIS):

```bash
# 1. Editable HyPlan checkout (assumed to live at ../hyplan)
pip install -e ../hyplan

# 2. Service runtime deps
pip install -r service/requirements.txt

# 3. Frontend lint tooling
npm install

# 4. (Optional but recommended) pre-commit hooks
pip install pre-commit
pre-commit install

# 5. Start the service
HYPLAN_CAMPAIGNS_DIR=/tmp/hyplan-campaigns \
  uvicorn service.app:app --reload --port 8100

# 6. Smoke test
curl http://127.0.0.1:8100/health
# {"status":"ok","hyplan_version":"...","service_version":"0.2.0"}
```

### Frontend dev loop (hot reload, no `npm run build`)

MMGIS's `npm start` runs webpack-dev-server with HMR on `PORT+1`
(default 8889).  Symlink the tool directory into MMGIS's source tree
once, then edits to `HyPlanTool.js` / `HyPlanTool.css` hot-reload
without rebuilding:

```bash
ln -s "$PWD/mmgis-tool/HyPlan" /path/to/MMGIS/src/essence/Tools/HyPlan
cd /path/to/MMGIS
npm start          # dev mode; browse the map at http://localhost:8889
```

Webpack-dev-server picks up file changes via its watcher; no
`npm run build` round-trip is needed during development.

⚠️ **Stock MMGIS needs two patches for the symlink loop to work.**
Without them the tool silently fails to register
(`Dirent.isDirectory()` returns `false` for symlinks) and webpack
fails to resolve `../../Basics/...` imports.  See
[.knowledge/knowledge-notes.md](./.knowledge/knowledge-notes.md) →
"Symlinking the tool into MMGIS needs two MMGIS-side patches".

Reserve the copy + build dance for a one-shot production install:

```bash
cp -r mmgis-tool/HyPlan /path/to/MMGIS/src/essence/Tools/HyPlan
cd /path/to/MMGIS && npm run build
```

## Architecture at a Glance

```text
MMGIS map + Draw tool
        |
        v
HyPlan MMGIS tool (frontend; jQuery + Leaflet + MMGIS singletons)
        |  HTTP / JSON
        v
FastAPI service (service/app.py = entry; routers under service/routers/)
        |
        v
HyPlan library (Campaign, Pattern, compute_flight_plan, GlintArc, ...)
        |
        +-- Campaign persistence (disk; SQLite slated for v0.4)
        +-- Flight-line / pattern generation
        +-- Plan computation + greedy optimization
        +-- Swath / glint / solar analysis
        +-- KML / GPX export
```

## Repository Layout

```text
hyplan-mmgis-plugin/
|-- AGENTS.md                       this file
|-- .knowledge/                     agent-optimized context
|   |-- README.md                   index
|   |-- conventions-and-gotchas.md  naming, placement, style
|   |-- knowledge-notes.md          non-obvious lessons learned
|-- mmgis-tool/HyPlan/
|   |-- HyPlanTool.js               single-file frontend controller
|   |-- HyPlanTool.css              tool panel styling
|   |-- config.json                 MMGIS tool descriptor
|-- service/
|   |-- app.py                      FastAPI app + router wiring (entry)
|   |-- state.py                    campaign + plan caches + persistence
|   |-- errors.py                   classify(exc) + raise_http(op, exc)
|   |-- schemas.py                  every Pydantic request / response
|   |-- routers/
|   |   |-- metadata.py             /health, /aircraft, /sensors
|   |   |-- tiles.py                /faa-tile, /imagery-layers
|   |   |-- wind.py                 /wind-grid
|   |   |-- generate.py             /generate-lines
|   |   |-- compute.py              /compute-plan, /optimize-sequence
|   |   |-- export.py               /export, /download/...
|   |   |-- analysis.py             /generate-swaths, /compute-glint,
|   |   |                           /optimize-azimuth, /solar-position
|   |   |-- lines.py                /add-line, /edit-line, ...
|   |   |-- patterns.py             /generate-pattern, /delete-pattern, ...
|   |   |-- campaigns.py            /campaigns, /campaigns/{id}
|   |-- Dockerfile / entrypoint.sh
|-- docs/                           human-facing reference docs
|   |-- API.md                      endpoint reference
|   |-- CODEMAP.md                  file map
|-- plans/                          local working notes (gitignored)
|-- .github/workflows/
|   |-- tests.yml                   ruff + service smoke + ESLint + Docker
|   |-- release.yml                 manual tag + GitHub Release
|-- eslint.config.js                flat config (@eslint/js recommended)
|-- package.json                    repo-root JS tooling
|-- pyproject.toml                  (none yet - service is not on PyPI)
```

## Workflow Notes

- **Trivial work** (bug fixes, lint cleanup, doc changes): commit
  directly to `main` with a focused message.  No PR ceremony needed.
- **Feature work** (new endpoint, frontend panel section, breaking
  schema change): keep `plans/` notes if scoping is non-trivial;
  update [CHANGELOG.md](CHANGELOG.md) under the appropriate
  `## vX.Y.0 (unreleased)` section.
- **Releases**: `gh workflow run release.yml -f version=X.Y.Z` -
  inline post-release housekeeping bumps `CITATION.cff` + regenerates
  `SECURITY.md` supported-versions table.  Don't tag manually.

## Knowledge Base

Agent-optimized lessons live in **[.knowledge/](./.knowledge/)**.
Full docs in [docs/](docs/).

| File                                                                  | What's there                                       |
| --------------------------------------------------------------------- | -------------------------------------------------- |
| [conventions-and-gotchas.md](./.knowledge/conventions-and-gotchas.md) | Naming, file placement, lint contracts, MMGIS-isms |
| [knowledge-notes.md](./.knowledge/knowledge-notes.md)                 | Non-obvious lessons from past sessions             |

## References

- **HyPlan core library**: <https://github.com/ryanpavlick/hyplan>
- **MMGIS**: <https://github.com/NASA-AMMOS/MMGIS>
- **CHANGELOG**: [CHANGELOG.md](CHANGELOG.md)
- **CONTRIBUTING** (human-facing): [CONTRIBUTING.md](CONTRIBUTING.md)
- **Roadmap (local-only)**: `plans/roadmap.md` (gitignored)
