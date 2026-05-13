"""Export the most recently computed plan to KML / GPX, plus download proxy."""

from __future__ import annotations

import os
import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ..schemas import ExportRequest, ExportResponse
from ..state import CAMPAIGNS_DIR, get_campaign, get_plan

router = APIRouter()


@router.post("/export", response_model=ExportResponse)
def export_plan(req: ExportRequest):
    """Export the most recently computed plan to KML/GPX files."""
    warnings: list[str] = []
    campaign = get_campaign(req.campaign_id)
    plan = get_plan(req.campaign_id)

    if plan is None:
        raise HTTPException(status_code=400, detail="No computed plan. Run compute-plan first.")

    campaign_dir = os.path.join(CAMPAIGNS_DIR, campaign.campaign_id)
    os.makedirs(campaign_dir, exist_ok=True)

    campaign.save(campaign_dir)

    safe_name = re.sub(r'[^\w\-.]', '_', campaign.name).strip('_') or 'flight_plan'

    artifacts = []
    for fmt in req.formats:
        if fmt == "kml":
            try:
                filepath = os.path.join(campaign_dir, f"{safe_name}_flight_plan.kml")
                from hyplan.exports import to_kml
                to_kml(plan, filepath)
                artifacts.append({
                    "format": "kml",
                    "filename": os.path.basename(filepath),
                    "download_url": f"/download/{campaign.campaign_id}/{os.path.basename(filepath)}",
                })
            except Exception as exc:
                warnings.append(f"KML export failed: {exc}")
        elif fmt == "gpx":
            try:
                filepath = os.path.join(campaign_dir, f"{safe_name}_flight_plan.gpx")
                from hyplan.exports import to_gpx
                to_gpx(plan, filepath, mission_name=campaign.name)
                artifacts.append({
                    "format": "gpx",
                    "filename": os.path.basename(filepath),
                    "download_url": f"/download/{campaign.campaign_id}/{os.path.basename(filepath)}",
                })
            except Exception as exc:
                warnings.append(f"GPX export failed: {exc}")
        else:
            warnings.append(f"Unsupported format: '{fmt}'")

    return ExportResponse(artifacts=artifacts, warnings=warnings)


@router.get("/download/{campaign_id}/{filename}")
def download_file(campaign_id: str, filename: str):
    """Download an exported file."""
    # Prevent path traversal
    if ".." in filename or "/" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename.")
    filepath = os.path.join(CAMPAIGNS_DIR, campaign_id, filename)
    if not os.path.isfile(filepath):
        raise HTTPException(status_code=404, detail="File not found.")
    return FileResponse(filepath, filename=filename)
