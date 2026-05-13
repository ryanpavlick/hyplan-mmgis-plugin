"""Campaign lifecycle: create and read."""

from __future__ import annotations

from fastapi import APIRouter

from hyplan.campaign import Campaign

from ..state import get_campaign, persist_campaign, register_campaign

router = APIRouter()


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
