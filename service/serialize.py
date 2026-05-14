"""Campaign ↔ JSON bundle serialization.

A *bundle* is the on-disk Campaign tree HyPlan's ``Campaign.save()``
emits, repacked into one JSON object so it can flow through HTTP
(``/campaigns/{id}/export`` / ``/campaigns/import``) or persist as a
single SQLite row (``service.store``).

The bundle envelope is intentionally stable across both call sites
so a future contributor adding a new persistence path doesn't have
to reinvent the encoding:

.. code-block:: text

    {
      "format": "hyplan-mmgis-plugin-campaign",
      "format_version": 1,
      "exported_at": "<ISO8601 UTC>",
      "service_version": "<plugin version>",
      "campaign_id": "<uuid>",
      "name": "<mission name>",
      "bounds": [min_lon, min_lat, max_lon, max_lat],
      "revision": <int>,
      "files": {
        "campaign.json": {...},
        "domain.geojson": {...},
        "flight_lines/all_lines.geojson": {...},
        "flight_lines/groups.json": [...],
        "patterns/all_patterns.json": [...]
      }
    }

Export artifacts (``*.kml`` / ``*.gpx`` / ``*.kmz``) are deliberately
excluded; they regenerate from ``/compute-plan`` + ``/export`` and
would otherwise bloat bundles with stale derived data.
"""

from __future__ import annotations

import copy
import datetime
import json
import os
import tempfile
import uuid as _uuid

from hyplan.campaign import Campaign

# Identifier + version stamped into every bundle we produce.  Bump
# the version when the on-disk Campaign tree gains structural
# changes older importers can't read.
BUNDLE_FORMAT = "hyplan-mmgis-plugin-campaign"
BUNDLE_FORMAT_VERSION = 1

# File extensions that count as part of the round-trippable state.
# Anything else (notably .kml / .gpx / .kmz export artifacts) is
# filtered out on serialize and ignored on deserialize.
BUNDLE_FILE_EXTS = (".json", ".geojson")


def campaign_to_bundle(campaign: Campaign, *, service_version: str = "0.4.0") -> dict:
    """Serialize a :class:`Campaign` to a JSON-safe bundle dict.

    Uses ``Campaign.save()`` into a tmp directory, walks the result,
    and packs all `.json` / `.geojson` files into ``files``.  The
    tmp directory is cleaned up on exit.
    """
    with tempfile.TemporaryDirectory(prefix="hyplan-bundle-") as tmp:
        campaign.save(tmp)
        files: dict = {}
        for root, _dirs, fnames in os.walk(tmp):
            for fname in fnames:
                if not fname.endswith(BUNDLE_FILE_EXTS):
                    continue
                abspath = os.path.join(root, fname)
                rel = os.path.relpath(abspath, tmp)
                with open(abspath) as f:
                    files[rel] = json.load(f)
    return {
        "format": BUNDLE_FORMAT,
        "format_version": BUNDLE_FORMAT_VERSION,
        "exported_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "service_version": service_version,
        "campaign_id": campaign.campaign_id,
        "name": campaign.name,
        "bounds": list(campaign.bounds),
        "revision": campaign.revision,
        "files": files,
    }


class BundleError(ValueError):
    """Raised when a bundle is structurally invalid or unsupported."""


def bundle_to_campaign(
    bundle: dict,
    *,
    new_campaign_id: str | None = None,
    name_override: str | None = None,
) -> Campaign:
    """Deserialize a bundle back into a HyPlan ``Campaign``.

    Validates the envelope, optionally rewrites ``campaign_id`` and
    ``name`` in the embedded ``campaign.json`` (since HyPlan's
    ``Campaign.campaign_id`` is read-only post-load), materializes the
    files in a tmp directory, and returns ``Campaign.load(tmp)``.

    Raises :class:`BundleError` for unrecognized formats, unsupported
    future ``format_version``, path-traversal attempts, or missing
    ``campaign.json``.
    """
    if not isinstance(bundle, dict):
        raise BundleError("bundle must be a JSON object.")
    if bundle.get("format") != BUNDLE_FORMAT:
        raise BundleError(
            f"Unrecognized bundle format: {bundle.get('format')!r}.  "
            f"Expected {BUNDLE_FORMAT!r}."
        )
    fmt_v = bundle.get("format_version")
    if (
        fmt_v is None
        or not isinstance(fmt_v, int)
        or fmt_v > BUNDLE_FORMAT_VERSION
    ):
        raise BundleError(
            f"Unsupported bundle format_version: {fmt_v!r}.  This service "
            f"understands up to {BUNDLE_FORMAT_VERSION}."
        )

    files = bundle.get("files")
    if not isinstance(files, dict) or not files:
        raise BundleError("bundle.files is missing or empty.")

    # Patch campaign_id / name in campaign.json *before* Campaign.load()
    # since HyPlan exposes campaign_id as a read-only property post-
    # load.  Deep-copy so we don't mutate the caller's bundle.
    files = copy.deepcopy(files)
    cj = files.get("campaign.json")
    if not isinstance(cj, dict):
        raise BundleError("bundle.files['campaign.json'] is missing or malformed.")
    if new_campaign_id is not None:
        cj["campaign_id"] = new_campaign_id
    if name_override is not None:
        cj["name"] = name_override
    files["campaign.json"] = cj

    with tempfile.TemporaryDirectory(prefix="hyplan-import-") as tmp:
        for rel, content in files.items():
            if not isinstance(rel, str) or rel.startswith("/") or ".." in rel.split("/"):
                raise BundleError(f"Invalid bundle file path: {rel!r}")
            if not rel.endswith(BUNDLE_FILE_EXTS):
                # Tolerate but skip files in unknown extensions.
                continue
            target = os.path.join(tmp, rel)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "w") as f:
                json.dump(content, f)
        return Campaign.load(tmp)


def fresh_uuid() -> str:
    """Return a fresh UUID4 string; isolated here so tests can patch it."""
    return str(_uuid.uuid4())
