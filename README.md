# hyplan-mmgis-plugin

MMGIS plugin for flight line planning with [HyPlan](https://github.com/ryanpavlick/hyplan).

## Components

- **service/** — Python FastAPI service that wraps hyplan's planning engine
- **mmgis-tool/** — MMGIS frontend tool plugin

## Setup

### 1. Install the MMGIS tool

Copy the tool into your MMGIS source tree:

```bash
cp -r mmgis-tool/HyPlan /path/to/MMGIS/src/essence/Tools/HyPlan
```

Then rebuild MMGIS (`npm run build` or `docker compose build`).

### 2. Run the service

Add the service to your MMGIS `docker-compose.yml`:

```yaml
hyplan-service:
  build:
    context: /path/to/hyplan-mmgis-plugin/service
  ports:
    - 8100:8100
  environment:
    - HYPLAN_CAMPAIGNS_DIR=/data/campaigns
  volumes:
    - hyplan-data:/data/campaigns
    - /path/to/hyplan:/hyplan:ro
  restart: on-failure
```

Add the adjacent server proxy to your MMGIS `.env`:

```
ADJACENT_SERVER_CUSTOM_0=["true", "hyplan", "hyplan-service", "8100"]
```

### 3. Enable the tool

In MMGIS Configure:

1. Open your mission → Tools tab
2. Enable **HyPlan**
3. Set Service URL to `/hyplan`
4. Save

## Workflow

1. Use the **Draw** tool to draw a polygon on the map
2. Open the **HyPlan** tool
3. Select aircraft, sensor, altitude, and overlap
4. Click **Generate Flight Box**
5. Select lines (click in list or on map)
6. Set takeoff/return airports
7. Click **Optimize Order** (optional)
8. Click **Compute Plan** — Dubins flight path displayed on map
9. Click **Export KML + GPX** — download links appear

## API Endpoints

- `GET /health` — service status
- `GET /aircraft` — available aircraft list
- `GET /sensors` — available sensor list
- `POST /generate-lines` — generate flight lines from polygon
- `POST /compute-plan` — compute flight plan from sequence
- `POST /optimize-sequence` — optimize line ordering
- `POST /export` — export to KML/GPX
- `GET /download/{campaign_id}/{filename}` — download exported file
