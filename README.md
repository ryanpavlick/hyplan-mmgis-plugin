# hyplan-mmgis-plugin

> **Status: very early development.**  APIs, on-disk formats, and the
> MMGIS tool layout are still moving.  Not ready for production use.

MMGIS plugin for interactive flight planning with
[HyPlan](https://github.com/ryanpavlick/hyplan).

This repository contains two pieces that work together:

- `mmgis-tool/HyPlan` — the MMGIS frontend tool
- `service/` — a FastAPI service that wraps HyPlan and persists campaign state

The plugin is designed to let an MMGIS user move back and forth between
drawn geometry on the map and HyPlan's planning engine without leaving
the mission UI.

## What the plugin does

The current tool supports:

- generating flight boxes from drawn polygons
- adding and editing individual flight lines
- generating reusable patterns:
  `racetrack`, `rosette`, `polygon`, `sawtooth`, `spiral`, `glint_arc`
- selecting, ordering, and transforming lines
- optimizing line order with HyPlan's flight optimizer
- computing full flight plans with airports, aircraft, and wind settings
- displaying wind, swaths, glint results, and solar plots
- exporting the latest computed plan to `KML` and `GPX`
- persisting campaigns and patterns on the backend between service restarts

## Repository layout

```text
hyplan-mmgis-plugin/
├── mmgis-tool/
│   └── HyPlan/
│       ├── config.json
│       ├── HyPlanTool.js
│       └── HyPlanTool.css
└── service/
    ├── app.py
    ├── Dockerfile
    ├── entrypoint.sh
    └── requirements.txt
```

## Architecture

```text
MMGIS map + Draw tool
        │
        ▼
HyPlan MMGIS tool (frontend)
        │  HTTP/JSON
        ▼
FastAPI service
        │
        ▼
HyPlan library
        │
        ├── Campaign persistence
        ├── Flight-line / pattern generation
        ├── Plan computation + optimization
        ├── Swath / glint / solar analysis
        └── Export to KML / GPX
```

A few important design choices:

- The frontend is intentionally thin: it owns map/UI state and delegates
  planning work to the service.
- The service stores campaigns in `HYPLAN_CAMPAIGNS_DIR` and reloads them
  at startup.
- The service accepts both free-standing lines and first-class HyPlan
  `Pattern` objects.
- The plugin uses MMGIS Draw geometry as the starting point for box
  generation and center picking.

## Prerequisites

You will typically need:

- an MMGIS checkout or deployment you can customize
- this `hyplan-mmgis-plugin` repository
- a local checkout of `hyplan`
- Docker / Docker Compose for the service, or a local Python environment

## Installation

**Just want to see it working?**  See
[docs/WALKTHROUGH.md](docs/WALKTHROUGH.md) — a 10-minute, terminal-by-
terminal recipe that takes you from a clean MMGIS clone to drawing
flight lines on the map, using Docker Compose for Postgres + the
HyPlan service and `npm start` for hot-reloadable frontend dev.

The rest of this section is reference for the individual pieces.

### 1. Install the MMGIS tool

Copy the tool into your MMGIS source tree:

```bash
cp -r mmgis-tool/HyPlan /path/to/MMGIS/src/essence/Tools/HyPlan
```

Then rebuild MMGIS:

```bash
cd /path/to/MMGIS
npm run build
```

If you use the MMGIS Docker workflow, rebuild the relevant image with your
normal MMGIS process instead.

### 2. Run the service with Docker

Add the service to your MMGIS `docker-compose.yml`:

```yaml
hyplan-service:
  build:
    context: /path/to/hyplan-mmgis-plugin/service
  ports:
    - "8100:8100"
  environment:
    HYPLAN_CAMPAIGNS_DIR: /data/campaigns
  volumes:
    - hyplan-data:/data/campaigns
    - /path/to/hyplan:/hyplan:ro
  restart: on-failure
```

Important notes:

- the service container expects the HyPlan source tree to be mounted at
  `/hyplan`
- `entrypoint.sh` copies that source to `/tmp/hyplan-src` and installs
  it with the `winds` extras before starting `uvicorn`
- campaign data is persisted in `HYPLAN_CAMPAIGNS_DIR`

### 3. Expose the service to MMGIS through the adjacent server

Add this to the MMGIS `.env`:

```bash
ADJACENT_SERVER_CUSTOM_0=["true", "hyplan", "hyplan-service", "8100"]
```

Then set the tool's service URL to:

```text
/hyplan
```

This is the recommended setup for a deployed MMGIS environment.

### 4. Enable the tool in MMGIS Configure

In MMGIS Configure:

1. Open the mission
2. Go to the `Tools` tab
3. Enable `HyPlan`
4. Set `Service URL`
5. Save and reload the mission

## Local development

You can also run the service outside Docker.

### Service

```bash
cd /path/to/hyplan-mmgis-plugin/service
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e /path/to/hyplan[winds]
export HYPLAN_CAMPAIGNS_DIR=/tmp/hyplan-campaigns
uvicorn app:app --reload --host 0.0.0.0 --port 8100
```

In this mode, set the MMGIS tool `Service URL` to:

```text
http://localhost:8100
```

### Frontend

The frontend is just the MMGIS tool directory. After editing
`mmgis-tool/HyPlan/HyPlanTool.js` or `HyPlanTool.css`, copy it into the
MMGIS source tree and rebuild MMGIS.

## Typical workflow in the tool

The UI is organized into numbered sections.

FAA aeronautical charts (VFR Sectional, VFR Terminal, IFR Low, IFR High) and
GIBS cloud/satellite imagery are served via `GET /imagery-layers` as native
MMGIS tile layers. Toggle and adjust opacity from the MMGIS layer panel rather
than the HyPlan tool.

### 1. Campaign

- set campaign name
- choose aircraft and sensor
- set takeoff and return airports
- set takeoff time in local browser time
- choose wind mode:
  `still_air`, `constant`, `gfs`, `gmao`

The frontend converts the local browser time to UTC before calling the
service.

### 2. Generate flight lines

- draw a polygon with MMGIS Draw
- set altitude, overlap, and optional azimuth
- optionally run azimuth optimization for glint
- generate a flight box with HyPlan

### 2b. Individual lines

- add a single line by clicking two map points
- delete a selected line

### 2c. Flight patterns

- choose a pattern type
- set a center point on the map
- generate a HyPlan `Pattern` into the campaign

### 2d. Patterns in campaign

- inspect existing patterns
- include waypoint-based patterns in compute
- delete patterns as whole objects

### 3. Select and order lines

- select lines from the list or map
- select all or clear selection
- optimize the line order for the chosen aircraft and airports

### 3b. Transform selected lines

Current transform operations:

- `rotate`
- `offset_across`
- `offset_along`
- `offset_north_east`
- `reverse`

### 4. Compute flight plan

- compute the full plan from the selected sequence
- include selected line IDs and any chosen waypoint-pattern references
- display the plan geometry on the map
- view plan distance and duration summary

### 4b. Swath display

- generate swath polygons for selected lines and sensor
- show overlap / gap summary when available

### 4c. Glint analysis

- compute per-sample glint angles for selected lines
- visualize them on the map
- use transforms, sensor changes, or takeoff-time changes to improve geometry

### 5. Solar position

- pick a map point or use a selected line midpoint
- plot solar geometry over the day

### 6. Export

- export the latest computed plan to `KML` and `GPX`
- download generated files from the service

## Campaign and persistence model

The backend keeps an in-memory registry of campaigns keyed by campaign ID,
and also persists each campaign on disk.

Key behaviors:

- `POST /generate-lines`, `POST /add-line`, and `POST /generate-pattern`
  can create a campaign if needed
- campaigns are saved to `HYPLAN_CAMPAIGNS_DIR/<campaign_id>/`
- previously saved campaigns are loaded on service startup
- the service returns `revision` values so the frontend can reason about
  mutation order
- computed plans are cached in memory per campaign for export

This means the plugin behaves more like a lightweight planning workspace
than a stateless geometry endpoint.

## Service API

The service surface is now fairly broad. Endpoints are grouped below; a
more complete list is in [docs/API.md](docs/API.md). For a contributor-oriented
walkthrough of how the frontend and backend fit together, see
[docs/CODEMAP.md](docs/CODEMAP.md).

### Core

- `GET /health`
- `GET /aircraft`
- `GET /sensors`
- `GET /imagery-layers`

### Campaigns and lines

- `POST /campaigns`
- `GET /campaigns/{campaign_id}`
- `POST /generate-lines`
- `POST /add-line`
- `POST /edit-line`
- `POST /delete-line`
- `POST /transform-lines`

### Patterns

- `POST /generate-pattern`
- `POST /replace-pattern`
- `POST /delete-pattern`
- `GET /patterns/{campaign_id}`

### Planning and analysis

- `POST /optimize-sequence`
- `POST /compute-plan`
- `POST /generate-swaths`
- `POST /compute-glint`
- `POST /optimize-azimuth`
- `POST /solar-position`
- `POST /wind-grid`

### Export

- `POST /export`
- `GET /download/{campaign_id}/{filename}`

## Troubleshooting

### The tool says it cannot connect to the service

Check:

- the service container is running
- the tool `Service URL` is correct
- the MMGIS adjacent-server proxy is configured if you use `/hyplan`

### The service starts but cannot import HyPlan

Check:

- `/path/to/hyplan` is mounted to `/hyplan` in the container
- the HyPlan checkout is valid and installable
- the service logs for `pip install` failures during startup

### Exports fail

`/export` requires a previously computed plan. Run `Compute Plan` first.

### Wind visualization shows nothing

The frontend only requests the wind grid when the wind mode is `gfs` or
`gmao`.

### Patterns or lines disappear after MMGIS layer toggles

The frontend includes a layer self-healing mechanism for HyPlan-owned
Leaflet layers, but MMGIS layer resets can still be tricky during active
development. Reload the tool if the map state gets out of sync.

## Current limitations

- there is no formal versioned API contract yet between the MMGIS tool
  and the FastAPI service
- there are no dedicated automated tests in this repository today
- the frontend is still a single large `HyPlanTool.js` file
- the service is currently one large `app.py` module

## Related repositories

- [hyplan](https://github.com/ryanpavlick/hyplan) — planning engine used by the service
- [MMGIS](https://github.com/NASA-AMMOS/MMGIS) — host application for the frontend tool
