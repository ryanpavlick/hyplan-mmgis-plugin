"""Process-wide state and persistence helpers shared by every router.

Two pieces of mutable state are important when reading the service:

- :data:`_campaigns` stores active :class:`Campaign` objects keyed by
  campaign UUID and any extra aliases used by the frontend (e.g. a
  browser-side temporary id assigned before the canonical UUID is
  known).
- :data:`_plans` stores the most recently computed flight plan per
  campaign so the ``/export`` endpoint can write KML / GPX without
  recomputing.

Campaigns are persisted to a SQLite database (one row per campaign,
the row's ``bundle_json`` column holds the same JSON envelope that
``/campaigns/{id}/export`` emits) and reloaded on service startup,
so the in-memory state is effectively a working cache over the
store.

Two env vars control the persistence layer:

- :envvar:`HYPLAN_CAMPAIGNS_DB` — path to the SQLite file (default:
  ``${HYPLAN_CAMPAIGNS_DIR}/campaigns.sqlite``).
- :envvar:`HYPLAN_CAMPAIGNS_DIR` — legacy directory tree.  On
  startup, any campaign UUIDs found here that aren't already in the
  store get one-shot-migrated into it.  Existing deployments
  upgrade transparently.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import HTTPException

import hyplan
from hyplan.aircraft import Aircraft
from hyplan.campaign import Campaign

from . import store as _store

logger = logging.getLogger("hyplan-service")

CAMPAIGNS_DIR = os.environ.get("HYPLAN_CAMPAIGNS_DIR", "/tmp/hyplan-campaigns")
CAMPAIGNS_DB = os.environ.get(
    "HYPLAN_CAMPAIGNS_DB",
    os.path.join(CAMPAIGNS_DIR, "campaigns.sqlite"),
)

# Active campaign objects keyed by campaign UUID plus any frontend alias
# used when the browser creates a campaign before it knows the canonical
# UUID.
_campaigns: dict[str, Campaign] = {}

# Most recent computed GeoDataFrame per campaign. ``/export`` depends on
# this cache rather than recomputing a plan from browser state.
_plans: dict[str, Any] = {}


def register_campaign(campaign: Campaign, *extra_keys: str) -> None:
    """Register a campaign in memory under its UUID and any extra keys."""
    _campaigns[campaign.campaign_id] = campaign
    for key in extra_keys:
        if key and key != campaign.campaign_id:
            _campaigns[key] = campaign


def persist_campaign(campaign: Campaign) -> None:
    """Persist a campaign to the SQLite store (atomic UPSERT)."""
    _store.save_campaign(campaign)


def load_persisted_campaigns() -> None:
    """Initialize the store, migrate any legacy on-disk campaigns,
    and hydrate in-memory state from the store.

    Called once at app startup by ``service.app``'s lifespan handler.
    """
    _store.init_store(CAMPAIGNS_DB)
    migrated = _store.migrate_filesystem_to_db(CAMPAIGNS_DIR)
    if migrated:
        logger.info("Migrated %d legacy campaign(s) into SQLite store.", migrated)
    for campaign in _store.iter_campaigns():
        register_campaign(campaign)
        logger.info(
            "Loaded persisted campaign '%s' (%s)",
            campaign.name, campaign.campaign_id,
        )


def get_or_create_campaign(
    campaign_id: str, name: str, bounds: list[float],
) -> Campaign:
    """Get an existing campaign or create a new one with the given bounds.

    The frontend sometimes computes degenerate bounds when the polygon
    used to seed the campaign is very small or a single point.  We add a
    1° margin in that case so the resulting :class:`Campaign` has a
    non-zero domain.
    """
    if campaign_id in _campaigns:
        return _campaigns[campaign_id]
    min_lon, min_lat, max_lon, max_lat = bounds
    if max_lon - min_lon < 0.01:
        min_lon -= 0.5
        max_lon += 0.5
    if max_lat - min_lat < 0.01:
        min_lat -= 0.5
        max_lat += 0.5
    campaign = Campaign(name=name, bounds=(min_lon, min_lat, max_lon, max_lat))
    register_campaign(campaign, campaign_id)
    persist_campaign(campaign)
    return campaign


def get_campaign(campaign_id: str) -> Campaign:
    """Look up a registered campaign or raise 404."""
    if campaign_id not in _campaigns:
        raise HTTPException(status_code=404, detail=f"Campaign '{campaign_id}' not found.")
    return _campaigns[campaign_id]


def make_aircraft(name: str) -> Aircraft:
    """Instantiate an aircraft by class name (e.g. ``"NASA_GV"``)."""
    cls = getattr(hyplan, name, None)
    if cls is None or not isinstance(cls, type) or not issubclass(cls, Aircraft):
        raise HTTPException(status_code=400, detail=f"Unknown aircraft: '{name}'")
    return cls()


def check_revision(campaign: Campaign, if_match: Any) -> None:
    """Concurrent-edit guard: enforce a client-supplied ``If-Match`` header.

    Patterned after the HTTP ``If-Match`` precondition.  The frontend
    sends ``If-Match: <revision>`` on every write; if the server's
    current ``campaign.revision`` no longer matches, two clients have
    raced and the write is rejected with a ``409 Conflict`` plus a
    structured detail so the UI can show the actual server revision
    and offer to refresh.

    No-op when:
    - ``if_match`` is ``None`` or empty string (legacy / unbounded
      clients can keep writing without a precondition).
    - The campaign was just created in this request (its revision
      is still 0).  This lets create-on-demand endpoints like
      ``/generate-lines`` carry an ``If-Match`` aimed at a future
      revision without tripping on the initial save.
    """
    if if_match is None or if_match == "":
        return
    try:
        expected = int(if_match)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=400,
            detail={
                "message": f"Invalid If-Match header: {if_match!r}.  Expected integer revision.",
                "code": "bad_if_match",
                "operation": "check_revision",
            },
        )
    if campaign.revision != expected:
        raise HTTPException(
            status_code=409,
            detail={
                "message": (
                    f"Revision mismatch: client has {expected}, server is at "
                    f"{campaign.revision}.  Refresh and retry."
                ),
                "code": "revision_mismatch",
                "operation": "check_revision",
                "client_revision": expected,
                "server_revision": campaign.revision,
            },
        )


def get_plan(campaign_id: str):
    """Return the most recently computed plan for ``campaign_id`` or ``None``."""
    return _plans.get(campaign_id)


def set_plan(campaign_id: str, plan) -> None:
    """Cache a computed plan for later export."""
    _plans[campaign_id] = plan
