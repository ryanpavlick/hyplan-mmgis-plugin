"""FAA aeronautical chart proxy and MMGIS-native tile-layer manifest.

vfrmap.com serves the current AIRAC cycle's VFR/IFR charts as XYZ/TMS
tiles.  The cycle string (e.g. ``"20260319"``) is embedded in the URL
path and rolls every 28 days, so we scrape it from vfrmap's frontend at
runtime and cache it for an hour.  Tile requests from MMGIS hit
``/faa-tile/...`` here, we look up the current cycle, forward to
vfrmap, and stream the response back.

``/imagery-layers`` returns the FAA proxy plus NASA GIBS cloud /
satellite layers in the shape MMGIS expects for its layer panel — the
operator does not need to wire any tile URLs by hand.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

import requests
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

logger = logging.getLogger("hyplan-service")

router = APIRouter()


_VFRMAP_KINDS = {
    "vfrc": "VFR Sectional",
    "sectc": "Sectional (secondary)",
    "helic": "Helicopter Route",
    "ifrlc": "IFR Low Enroute",
    "ehc": "IFR High Enroute",
}
_VFRMAP_CYCLE_TTL_SEC = 3600
_VFRMAP_CYCLE_PATTERN = re.compile(r"""f\s*=\s*['"](\d{8})['"]""")
_vfrmap_cycle_cache: dict[str, Any] = {"cycle": None, "fetched_at": 0.0}


def _get_vfrmap_cycle() -> str:
    now = time.time()
    cached = _vfrmap_cycle_cache["cycle"]
    if cached and (now - _vfrmap_cycle_cache["fetched_at"]) < _VFRMAP_CYCLE_TTL_SEC:
        return cached
    try:
        r = requests.get("https://vfrmap.com/js/map.js", timeout=10)
        r.raise_for_status()
        m = _VFRMAP_CYCLE_PATTERN.search(r.text)
        if m:
            _vfrmap_cycle_cache["cycle"] = m.group(1)
            _vfrmap_cycle_cache["fetched_at"] = now
            return m.group(1)
        logger.warning("vfrmap map.js did not contain AIRAC cycle pattern")
    except Exception as e:
        logger.warning("Failed to fetch vfrmap cycle: %s", e)
    if cached:
        return cached
    raise HTTPException(status_code=503, detail="vfrmap AIRAC cycle unavailable")


@router.get("/faa-tile/{kind}/{z}/{y}/{x}")
def faa_tile(kind: str, z: int, y: int, x: int):
    """Proxy FAA chart tiles from vfrmap.com with auto-refreshed AIRAC cycle.

    MMGIS points its tile layer URL at this endpoint with
    ``tileformat: "tms"``, so ``y`` arrives already in TMS convention
    (``y=0`` at bottom), which is what vfrmap expects.
    """
    if kind not in _VFRMAP_KINDS:
        raise HTTPException(status_code=404, detail=f"Unknown FAA chart kind: {kind}")
    cycle = _get_vfrmap_cycle()
    upstream = f"https://vfrmap.com/{cycle}/tiles/{kind}/{z}/{y}/{x}.jpg"
    try:
        r = requests.get(upstream, timeout=15)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"vfrmap fetch failed: {e}")
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail="tile not found")
    if not r.ok:
        raise HTTPException(status_code=502, detail=f"vfrmap returned {r.status_code}")
    return Response(
        content=r.content,
        media_type=r.headers.get("Content-Type", "image/jpeg"),
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/imagery-layers")
def imagery_layers():
    """Return MMGIS-shaped tile layer objects for cloud/satellite + FAA charts."""
    gibs_base = "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best"
    # FAA charts are served via the /faa-tile proxy so the vfrmap AIRAC
    # cycle stays fresh without the operator touching the mission config.
    faa_proxy = "/faa-tile"
    return {
        "layers": [
            {
                "name": "FAA VFR Sectional",
                "type": "tile",
                "url": f"{faa_proxy}/vfrc/{{z}}/{{y}}/{{x}}",
                "tileformat": "tms",
                "visibility": False,
                "minZoom": 5,
                "maxZoom": 15,
                "maxNativeZoom": 12,
                "attribution": "FAA charts via vfrmap.com",
            },
            {
                "name": "FAA IFR Low Enroute",
                "type": "tile",
                "url": f"{faa_proxy}/ifrlc/{{z}}/{{y}}/{{x}}",
                "tileformat": "tms",
                "visibility": False,
                "minZoom": 5,
                "maxZoom": 14,
                "maxNativeZoom": 11,
                "attribution": "FAA charts via vfrmap.com",
            },
            {
                "name": "FAA IFR High Enroute",
                "type": "tile",
                "url": f"{faa_proxy}/ehc/{{z}}/{{y}}/{{x}}",
                "tileformat": "tms",
                "visibility": False,
                "minZoom": 4,
                "maxZoom": 13,
                "maxNativeZoom": 10,
                "attribution": "FAA charts via vfrmap.com",
            },
            {
                "name": "VIIRS SNPP True Color",
                "type": "tile",
                "url": f"{gibs_base}/VIIRS_SNPP_CorrectedReflectance_TrueColor/default/{{time}}/GoogleMapsCompatible_Level9/{{z}}/{{y}}/{{x}}.jpg",
                "tileformat": "wmts",
                "visibility": False,
                "minZoom": 1,
                "maxZoom": 9,
                "maxNativeZoom": 9,
                "time": {"enabled": True, "type": "requery", "format": "%Y-%m-%d"},
            },
            {
                "name": "MODIS Terra True Color",
                "type": "tile",
                "url": f"{gibs_base}/MODIS_Terra_CorrectedReflectance_TrueColor/default/{{time}}/GoogleMapsCompatible_Level9/{{z}}/{{y}}/{{x}}.jpg",
                "tileformat": "wmts",
                "visibility": False,
                "minZoom": 1,
                "maxZoom": 9,
                "maxNativeZoom": 9,
                "time": {"enabled": True, "type": "requery", "format": "%Y-%m-%d"},
            },
            {
                "name": "GOES-East GeoColor",
                "type": "tile",
                "url": f"{gibs_base}/GOES-East_ABI_GeoColor/default/{{time}}/GoogleMapsCompatible_Level7/{{z}}/{{y}}/{{x}}.png",
                "tileformat": "wmts",
                "visibility": False,
                "minZoom": 1,
                "maxZoom": 7,
                "maxNativeZoom": 7,
                "time": {"enabled": True, "type": "requery", "format": "%Y-%m-%dT%H:%M:%SZ"},
            },
            {
                "name": "GOES-West GeoColor",
                "type": "tile",
                "url": f"{gibs_base}/GOES-West_ABI_GeoColor/default/{{time}}/GoogleMapsCompatible_Level7/{{z}}/{{y}}/{{x}}.png",
                "tileformat": "wmts",
                "visibility": False,
                "minZoom": 1,
                "maxZoom": 7,
                "maxNativeZoom": 7,
                "time": {"enabled": True, "type": "requery", "format": "%Y-%m-%dT%H:%M:%SZ"},
            },
            {
                "name": "Himawari-9 Band 3",
                "type": "tile",
                "url": f"{gibs_base}/Himawari_AHI_Band3_Red_Visible/default/{{time}}/GoogleMapsCompatible_Level7/{{z}}/{{y}}/{{x}}.png",
                "tileformat": "wmts",
                "visibility": False,
                "minZoom": 1,
                "maxZoom": 7,
                "maxNativeZoom": 7,
                "time": {"enabled": True, "type": "requery", "format": "%Y-%m-%dT%H:%M:%SZ"},
            },
            {
                "name": "MODIS Cloud Fraction",
                "type": "tile",
                "url": f"{gibs_base}/MODIS_Terra_Cloud_Fraction_Day/default/{{time}}/GoogleMapsCompatible_Level6/{{z}}/{{y}}/{{x}}.png",
                "tileformat": "wmts",
                "visibility": False,
                "minZoom": 1,
                "maxZoom": 6,
                "maxNativeZoom": 6,
                "time": {"enabled": True, "type": "requery", "format": "%Y-%m-%d"},
            },
        ]
    }
