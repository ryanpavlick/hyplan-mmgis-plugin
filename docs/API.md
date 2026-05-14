---
title: Service API
nav_order: 30
permalink: /api/
---

This file summarizes the FastAPI surface exposed by
`hyplan-mmgis-plugin/service/app.py`.

It is intentionally grouped by workflow rather than by source order in
the file.

## Notes

- Most mutating responses include `campaign_id` and `revision`.
- Many endpoints return GeoJSON FeatureCollections for direct map use.
- A `campaign_id` is required whenever the request references existing
  lines or patterns.
- The frontend typically uses local browser time and converts to UTC
  before calling the service.

## Health and metadata

### `GET /health`

Returns basic service health and version information.

### `GET /aircraft`

Returns the available HyPlan aircraft class names.

### `GET /sensors`

Returns the available sensor names.

### `GET /imagery-layers`

Returns imagery-layer metadata exposed by the service.

## Campaigns

### `POST /campaigns`

Creates a new campaign from `name` and `bounds`.

### `GET /campaigns/{campaign_id}`

Returns campaign state, including:

- bounds
- revision
- flight lines
- groups
- patterns

## Flight lines

### `POST /generate-lines`

Generates a flight box from polygon geometry and generator parameters.

Primary current use:

- `box_around_polygon`

Returns:

- generated line GeoJSON
- campaign metadata
- revision
- summary information

### `POST /add-line`

Adds a single free-standing flight line to a campaign.

### `POST /edit-line`

Edits a single line by replacing it in place.

Works for:

- free-standing lines
- pattern-owned line legs

### `POST /delete-line`

Deletes a line from a campaign.

If the deleted line is the final leg in a line-based pattern, the service
removes the pattern as well.

### `POST /transform-lines`

Applies geometric transforms to one or more selected lines.

Supported operations currently include:

- `rotate`
- `offset_across`
- `offset_along`
- `offset_north_east`
- `reverse`
- direct endpoint replacement in some edit flows

## Patterns

### `POST /generate-pattern`

Generates a pattern and adds it to the campaign as a first-class HyPlan
`Pattern`.

Supported pattern types:

- `racetrack`
- `rosette`
- `polygon`
- `sawtooth`
- `spiral`
- `glint_arc`

For `glint_arc`, the request may also trigger preview generation for:

- arc swath
- per-sample glint points
- summary statistics

### `POST /replace-pattern`

Regenerates a pattern in place using parameter overrides merged into the
stored `Pattern.params`.

The `pattern_id` is preserved.

### `POST /delete-pattern`

Deletes a pattern and its owned geometry from the campaign.

### `GET /patterns/{campaign_id}`

Lists patterns attached to a campaign.

## Planning

### `POST /optimize-sequence`

Proposes an optimized line order for selected lines using HyPlan's
optimizer.

Inputs typically include:

- `campaign_id`
- selected `line_ids`
- `aircraft`
- takeoff / return airports

### `POST /compute-plan`

Computes a flight plan from a sequence of entries.

Sequence entries may be:

- `line`
- `waypoint`
- `pattern`

The request also supports:

- aircraft selection
- takeoff / return airports
- takeoff time
- wind configuration

Wind modes currently supported:

- `still_air`
- `constant`
- gridded forecast / analysis flows used by the frontend

The resulting plan is cached server-side for export.

### `POST /isochrone`

Single-budget wind-aware reach contour around a start point.  Wraps
`hyplan.planning.compute_isochrone`.

Request:

```json
{
  "aircraft": "NASA_GV",
  "start": {"airport": "KSBP"},
  "budget_min": 120,
  "mode": "round_trip",
  "wind": {"kind": "still_air"},
  "reserve_min": 0,
  "azimuth_resolution_deg": 5.0
}
```

`start` accepts either `{airport: "ICAO"}` or
`{latitude, longitude, altitude_msl_m}`.  `mode` is one of
`one_way` / `round_trip` / `return_safe` (the latter needs
`return_destination`).  Wind kinds `still_air` and `constant`
(`{kind, speed_kt, direction_deg}`) are implemented; gridded sources
return 400.

Response is a GeoJSON `FeatureCollection`:

- one `Polygon` Feature (the closed boundary)
- one `Point` Feature per ray with `azimuth_deg`, `target_lat/lon`,
  `distance_nmi`, `total_time_min`, `limiting_leg`
- top-level `summary` carrying the underlying GeoDataFrame's
  `attrs` (mode, n_rays, wind_source_kind, …)

### `POST /isochrone-concentric`

Same as `/isochrone` but accepts `budgets_min: [60, 120, 180]` and
emits one Polygon per budget in one O(M+N) sweep.

### `POST /isochrone-refuel`

Refuel-aware reach with a single optional fuel stop.  Adds
`flight_day_budget_min`, `refuel_airports: ["ICAO", ...]`,
`refuel_time_min`, `max_refuel_stops`.  Ray properties include the
chosen `itinerary`, `refuel_airport`, per-cycle sortie times, and
day-budget margins.

## Analysis

### `POST /wind-grid`

Returns a U/V wind grid formatted for Leaflet velocity visualization.

Used by the frontend "Show Wind on Map" action.

### `POST /generate-swaths`

Builds swath polygons for selected lines and sensor configuration.

May also return overlap / gap summary information.

### `POST /compute-glint`

Computes per-sample glint angles for selected lines.

Returns:

- GeoJSON point features
- summary statistics
- warnings, including sun-below-horizon cases

### `POST /optimize-azimuth`

Sweeps headings for a test point and returns the azimuth that best meets
the chosen glint criterion.

Used by the frontend "Optimize Azimuth" action.

### `POST /solar-position`

Returns a daily time series of solar geometry at a chosen point.

Used by the frontend "Solar Position" panel.

## Export

### `POST /export`

Exports the most recently computed plan for a campaign.

Current outputs:

- `KML`
- `GPX`

### `GET /download/{campaign_id}/{filename}`

Downloads an exported file from the persisted campaign directory.
