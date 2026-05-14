"""Campaign lifecycle: create, read, export / import as a JSON blob."""

from __future__ import annotations

import datetime
import json
import os
import tempfile
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from hyplan.campaign import Campaign

from ..errors import raise_http
from ..state import get_campaign, persist_campaign, register_campaign

router = APIRouter()


# Bundle format identifier + version.  Bump the version when the on-
# disk Campaign tree gains structural changes that older importers
# can't read; older bundles should keep importing as long as the
# underlying HyPlan Campaign.load() still tolerates them.
_BUNDLE_FORMAT = "hyplan-mmgis-plugin-campaign"
_BUNDLE_FORMAT_VERSION = 1

# Files within a Campaign directory that count as part of the
# round-trippable state.  Export artifacts (.kml / .gpx / .kmz)
# regenerate from /compute-plan + /export and are intentionally
# excluded so bundles stay self-contained and free of stale derived
# data.
_BUNDLE_FILE_EXTS = (".json", ".geojson")


class ImportCampaignRequest(BaseModel):
    bundle: dict
    # If true, replace any existing campaign with the same campaign_id;
    # otherwise the import always assigns a fresh UUID so it can't
    # clobber state.
    replace: bool = False
    # Optional rename on import.
    name: Optional[str] = Field(
        default=None,
        description="Override the bundle's mission name on import.",
    )


@router.post("/campaigns")
def create_campaign(name: str, bounds: list[float]):
    """Create a new campaign."""
    campaign = Campaign(name=name, bounds=tuple(bounds))
    register_campaign(campaign)
    persist_campaign(campaign)
    return {
        "campaign_id": campaign.campaign_id,
        "name": campaign.name,
        "bounds": campaign.bounds,
        "revision": campaign.revision,
    }


@router.get("/campaigns/{campaign_id}")
def read_campaign(campaign_id: str):
    """Get campaign state."""
    campaign = get_campaign(campaign_id)
    return {
        "campaign_id": campaign.campaign_id,
        "name": campaign.name,
        "bounds": campaign.bounds,
        "revision": campaign.revision,
        "flight_lines": campaign.flight_lines_to_geojson(),
        "groups": campaign.groups,
        "patterns": campaign.patterns_to_geojson(),
    }


@router.get("/campaigns/{campaign_id}/export")
def export_campaign(campaign_id: str):
    """Export a campaign as a single JSON bundle for sharing or backup.

    Wraps HyPlan's ``Campaign.save()`` directory tree into one JSON
    object: ``files`` maps repo-relative paths (``campaign.json``,
    ``flight_lines/all_lines.geojson``, ``patterns/all_patterns.json``,
    etc.) to their parsed JSON contents.  Export artifacts
    (``*.kml`` / ``*.gpx`` / ``*.kmz``) are excluded because they
    regenerate from /compute-plan + /export and would otherwise
    bloat the bundle with stale derived data.

    The bundle round-trips losslessly through /campaigns/import as
    long as the receiving service speaks the same
    ``format_version``.
    """
    campaign = get_campaign(campaign_id)

    with tempfile.TemporaryDirectory(prefix="hyplan-export-") as tmp:
        try:
            campaign.save(tmp)
        except Exception as exc:
            raise_http("campaigns-export", exc)

        files: dict = {}
        for root, _dirs, fnames in os.walk(tmp):
            for fname in fnames:
                if not fname.endswith(_BUNDLE_FILE_EXTS):
                    continue
                abspath = os.path.join(root, fname)
                rel = os.path.relpath(abspath, tmp)
                try:
                    with open(abspath) as f:
                        files[rel] = json.load(f)
                except Exception as exc:
                    raise_http("campaigns-export", exc)

    return {
        "format": _BUNDLE_FORMAT,
        "format_version": _BUNDLE_FORMAT_VERSION,
        "exported_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "service_version": "0.4.0",
        "campaign_id": campaign.campaign_id,
        "name": campaign.name,
        "bounds": list(campaign.bounds),
        "revision": campaign.revision,
        "files": files,
    }


@router.post("/campaigns/import")
def import_campaign(req: ImportCampaignRequest):
    """Import a campaign from a JSON bundle produced by ``/export``.

    By default assigns a fresh ``campaign_id`` on import so a re-import
    doesn't clobber a still-active campaign — set ``replace: true`` to
    keep the bundle's id (e.g. when restoring from a backup).
    """
    bundle = req.bundle
    if not isinstance(bundle, dict):
        raise HTTPException(status_code=400, detail="bundle must be a JSON object.")
    if bundle.get("format") != _BUNDLE_FORMAT:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unrecognized bundle format: {bundle.get('format')!r}.  "
                f"Expected {_BUNDLE_FORMAT!r}."
            ),
        )
    fmt_v = bundle.get("format_version")
    if fmt_v is None or not isinstance(fmt_v, int) or fmt_v > _BUNDLE_FORMAT_VERSION:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported bundle format_version: {fmt_v!r}.  This service "
                f"understands up to {_BUNDLE_FORMAT_VERSION}."
            ),
        )

    files = bundle.get("files")
    if not isinstance(files, dict) or not files:
        raise HTTPException(status_code=400, detail="bundle.files is missing or empty.")

    # Decide the target campaign_id (and optional rename) up front so
    # we can patch campaign.json *before* Campaign.load() — HyPlan's
    # Campaign exposes campaign_id as a read-only property, so we
    # can't rebind it post-load.
    import copy
    import uuid as _uuid

    files = copy.deepcopy(files)
    cj = files.get("campaign.json")
    if not isinstance(cj, dict):
        raise HTTPException(
            status_code=400, detail="bundle.files['campaign.json'] is missing or malformed.",
        )
    if not req.replace:
        cj["campaign_id"] = str(_uuid.uuid4())
    if req.name:
        cj["name"] = req.name
    files["campaign.json"] = cj

    with tempfile.TemporaryDirectory(prefix="hyplan-import-") as tmp:
        # Materialize the bundle as the on-disk tree HyPlan.Campaign
        # expects, with a path-escape guard: the tarball-style attack
        # of a relative path containing '..' is rejected.
        for rel, content in files.items():
            if not isinstance(rel, str) or rel.startswith("/") or ".." in rel.split("/"):
                raise HTTPException(
                    status_code=400, detail=f"Invalid bundle file path: {rel!r}",
                )
            if not rel.endswith(_BUNDLE_FILE_EXTS):
                # Tolerate but skip files in unknown extensions.
                continue
            target = os.path.join(tmp, rel)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            try:
                with open(target, "w") as f:
                    json.dump(content, f)
            except Exception as exc:
                raise_http("campaigns-import", exc)

        try:
            campaign = Campaign.load(tmp)
        except Exception as exc:
            raise_http("campaigns-import", exc)

    register_campaign(campaign)
    persist_campaign(campaign)
    return {
        "campaign_id": campaign.campaign_id,
        "name": campaign.name,
        "bounds": list(campaign.bounds),
        "revision": campaign.revision,
        "flight_lines": campaign.flight_lines_to_geojson(),
        "groups": campaign.groups,
        "patterns": campaign.patterns_to_geojson(),
    }
