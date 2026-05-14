---
title: Home
layout: default
nav_order: 1
---

# hyplan-mmgis-plugin

MMGIS plugin for interactive flight planning with
[HyPlan](https://github.com/ryanpavlick/hyplan).

This repository contains two pieces that work together:

- **`mmgis-tool/HyPlan`** — the [MMGIS](https://github.com/NASA-AMMOS/MMGIS)
  frontend tool plugin (jQuery + Leaflet)
- **`service/`** — a FastAPI service that wraps HyPlan and persists
  campaign state

The plugin lets an MMGIS user move back and forth between drawn
geometry on the map and HyPlan's planning engine without leaving the
mission UI.

> **Status: very early development.**  APIs, on-disk formats, and the
> MMGIS tool layout are still moving.  Not ready for production use.

## Where to go next

| Page | What's there |
|---|---|
| [Walkthrough]({{ "/walkthrough" | relative_url }}) | Clean MMGIS to drawing flight lines on the map in 10 minutes |
| [Service API]({{ "/api" | relative_url }}) | The 27 HTTP endpoints, request / response shapes, error contract |
| [Code Map]({{ "/codemap" | relative_url }}) | File map for contributors who want to change the code |

## What the plugin does today

- Generate flight boxes from drawn polygons
- Add, edit, transform, and delete individual flight lines
- Generate reusable patterns: `racetrack`, `rosette`, `polygon`,
  `sawtooth`, `spiral`, `glint_arc`
- Move whole patterns in place (`translate`, `move_to`, `rotate`)
- Geodesic "from anchor + bearing + distance" endpoint placement
- Optimize line order with HyPlan's flight optimizer
- Compute full flight plans with airports, aircraft, and wind settings
- Display wind, swaths (with coverage % readout), glint results, and
  solar plots
- Export the latest plan to KML and GPX

## Architecture at a glance

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
        +-- Campaign persistence
        +-- Flight-line / pattern generation
        +-- Plan computation + greedy optimization
        +-- Swath / glint / solar analysis
        +-- KML / GPX export
```

## Reference repos

- HyPlan core library: <https://github.com/ryanpavlick/hyplan>
- MMGIS: <https://github.com/NASA-AMMOS/MMGIS>
- This plugin: <https://github.com/ryanpavlick/hyplan-mmgis-plugin>
