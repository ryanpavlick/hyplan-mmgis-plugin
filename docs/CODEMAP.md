---
title: Code Map
nav_order: 40
permalink: /codemap/
---

This document is for contributors who want to understand how the
`hyplan-mmgis-plugin` codebase is laid out before changing it.

## Mental model

The repository is split into two halves:

- `mmgis-tool/HyPlan/HyPlanTool.js`
  The browser-side controller that owns MMGIS UI state, map layers, user
  interaction modes, and HTTP requests.
- `service/app.py`
  The FastAPI bridge that turns those requests into `hyplan` campaign edits,
  planning runs, analysis products, and exports.

The frontend does not try to reimplement HyPlan logic. It mostly manages:

- current campaign ID
- current selected lines and included patterns
- Leaflet layers for lines, plan segments, swaths, glint, solar, and wind
- temporary map-picking modes such as draw-line, pattern-center, and solar pick

The backend does not store user sessions in a database. Instead it keeps
campaigns and most-recent plans in memory, and persists campaign directories to
disk under `HYPLAN_CAMPAIGNS_DIR`.

## Frontend map

### `mmgis-tool/HyPlan/config.json`

This is the MMGIS tool manifest. Right now the only config variable is
`serviceUrl`, which defaults to `http://localhost:8100` for local development.

### `mmgis-tool/HyPlan/HyPlanTool.js`

This file is a large single-controller tool, so it helps to read it by section:

1. Top-level state and overlay references
   This is where `campaignId`, `selectedLineIds`, `patternsCache`, and the
   various Leaflet layer handles live.

2. MMGIS self-heal layer helpers
   `hyplanOwn`, `hyplanDisownAndRemove`, and `installHyplanSelfHeal` exist
   because MMGIS layer toggles can remove plugin-owned overlays unexpectedly.

3. `markup`
   The tool panel HTML. The numbered headings in the UI mirror the major
   workflow stages and are a good table of contents for the file.

4. `interfaceWithMMGIS()`
   The main bootstrap function. It:
   - mounts the panel
   - reads config
   - loads service metadata
   - binds button and map events
   - orchestrates frontend state transitions

5. Helper/rendering section near the bottom
   Functions like `displayFlightLines`, `displayPlan`, `renderPatternsLayer`,
   `renderGlintSummary`, and `renderSolarPlot` are the best place to start if
   the UI looks wrong but the service response is correct.

### Frontend state conventions

- `selectedLineIds` controls ordering as well as selection.
  The compute request preserves the order shown in the line list.
- `patternsCache` is the lightweight list used to render the campaign pattern
  panel.
- `patternRefsForCompute` contains only waypoint-based patterns that should be
  appended to the compute sequence. Line-based patterns are already represented
  through their legs.
- Only one `glintArcLayer`, `solarMarkerLayer`, `windLayer`, etc. is tracked at
  a time, so new results replace the old visualization.

## Backend map

### `service/app.py`

Read this file in the following order:

1. Module docstring and global caches
   These explain the persistent state model: `_campaigns` and `_plans`.

2. Persistence helpers
   `_register_campaign`, `_persist_campaign`, `_load_persisted_campaigns`,
   `_get_or_create_campaign`, and `_get_campaign`.

3. Pydantic request/response models
   These are the API contract used by the MMGIS tool.

4. Endpoint groups
   The route handlers are grouped by function:
   - health / metadata / planning
   - map analysis overlays
   - manual line editing
   - pattern generation and mutation
   - campaign lifecycle / rehydration

### Important backend behaviors

- `/compute-plan` accepts a mixed ordered sequence of `line`, `pattern`, and
  `waypoint` entries.
- `/export` depends on `_plans[campaign_id]`, so it only works after a
  successful `/compute-plan`.
- `/generate-pattern` and `/replace-pattern` are the bridge from generic UI
  forms to concrete HyPlan pattern constructors.
- The service is intentionally stateful. If you need stateless behavior later,
  the campaign reload and plan cache design will need revisiting.

### Runtime files

- `service/Dockerfile`
  Builds the service image and expects the core `hyplan` repo to be mounted at
  `/hyplan`.
- `service/entrypoint.sh`
  Copies the mounted source, installs `hyplan[winds]`, then starts `uvicorn`.
- `service/requirements.txt`
  Only contains bridge/runtime dependencies. Most domain functionality still
  comes from the mounted `hyplan` checkout.

## Change guide

If you are changing the UI:

- start in `HyPlanTool.js`
- check whether the change needs a new route or just a new render path
- keep layer ownership paired with `hyplanOwn` / `hyplanDisownAndRemove`

If you are changing request/response shapes:

- update the relevant Pydantic model in `service/app.py`
- update the corresponding `fetch(...)` call in `HyPlanTool.js`
- update [API.md](./API.md) so the route contract stays discoverable

If you are changing campaign or pattern semantics:

- inspect both the mutation route in `service/app.py` and the line/pattern list
  rendering in `HyPlanTool.js`
- verify the compute sequence still reflects the intended MMGIS ordering

If you are debugging exports:

- confirm `/compute-plan` ran successfully first
- inspect `_plans[campaign_id]` handling in `service/app.py`
- remember that campaign state is saved to disk, but exported artifacts are
  generated from the cached most-recent plan
