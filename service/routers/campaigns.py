"""Campaign lifecycle: create, read, list, export / import as a JSON bundle."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from hyplan.campaign import Campaign

from .. import store
from ..errors import raise_http
from ..serialize import (
    BundleError,
    bundle_to_campaign,
    campaign_to_bundle,
    fresh_uuid,
)
from ..state import get_campaign, persist_campaign, register_campaign

router = APIRouter()


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


@router.get("/campaigns")
def list_campaigns():
    """List all persisted campaigns (lightweight metadata only)."""
    return {"campaigns": store.list_campaign_meta()}


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
    object (see :mod:`service.serialize` for the envelope).  Export
    artifacts (``*.kml`` / ``*.gpx`` / ``*.kmz``) are excluded so
    bundles stay free of stale derived data.

    The bundle round-trips losslessly through ``/campaigns/import``
    as long as the receiving service speaks the same
    ``format_version``.
    """
    campaign = get_campaign(campaign_id)
    try:
        return campaign_to_bundle(campaign)
    except Exception as exc:
        raise_http("campaigns-export", exc)


@router.post("/campaigns/import")
def import_campaign(req: ImportCampaignRequest):
    """Import a campaign from a JSON bundle produced by ``/export``.

    By default assigns a fresh ``campaign_id`` on import so a
    re-import doesn't clobber a still-active campaign — set
    ``replace: true`` to keep the bundle's id (e.g. when restoring
    from a backup).
    """
    new_id = None if req.replace else fresh_uuid()
    try:
        campaign = bundle_to_campaign(
            req.bundle,
            new_campaign_id=new_id,
            name_override=req.name,
        )
    except BundleError as exc:
        # User-input validation failure -> 400 with the message the
        # serializer wrote.
        raise HTTPException(status_code=400, detail=str(exc))
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
