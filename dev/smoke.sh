#!/usr/bin/env bash
#
# dev/smoke.sh — end-to-end curl walkthrough of the running service.
#
# Exercises the main HyPlan workflow: campaign creation -> flight-line
# generation -> compute plan -> pattern generation -> coverage % ->
# KML export.  Each step is a real HTTP call against the live service,
# so this doubles as a "is HyPlan happy with my install" check.
#
# Run the service first (in another terminal):
#
#   dev/run-service.sh
#
# Then:
#
#   dev/smoke.sh                    # default http://127.0.0.1:8100
#   BASE=http://localhost:8100 dev/smoke.sh

set -euo pipefail

BASE="${BASE:-http://127.0.0.1:8100}"

# --- Helpers --------------------------------------------------------------
# No jq dependency — Python (which the service already requires) handles
# all JSON.  `summarize` runs a snippet against stdin JSON, `extract`
# prints one raw value.  Snippets avoid backslash-escapes inside
# f-strings because Python 3.11 rejected those (PEP 701 lifted the
# restriction in 3.12); destructuring into locals first works on all
# supported versions.

summarize() {
    python3 -c "
import json, sys
d = json.load(sys.stdin)
$1
"
}

extract() {
    python3 -c "
import json, sys
d = json.load(sys.stdin)
print($1)
"
}

step() {
    echo
    echo "── $1 ──────────────────────────────────────────────"
}

# --- 1. Service health & metadata -----------------------------------------

step "1. /health"
curl -fsS "$BASE/health" | summarize '
status = d["status"]
hyplan = d["hyplan_version"]
svc = d["service_version"]
print(f"  status={status}  hyplan={hyplan}  service={svc}")
'

step "2. /aircraft (first 5)"
curl -fsS "$BASE/aircraft" | summarize '
aircraft = d["aircraft"]
for a in aircraft[:5]:
    print(f"  {a}")
print(f"  ... ({len(aircraft)} total)")
'

step "3. /sensors (first 5)"
curl -fsS "$BASE/sensors" | summarize '
sensors = d["sensors"]
for s in sensors[:5]:
    print(f"  {s}")
print(f"  ... ({len(sensors)} total)")
'

# --- 2. Generate a small flight box ---------------------------------------

step "4. /generate-lines  (~30 nm box near San Luis Obispo, AVIRIS-NG)"
GEN=$(curl -fsS -X POST "$BASE/generate-lines" \
    -H 'Content-Type: application/json' \
    -d '{
        "campaign_id": "smoke-campaign",
        "campaign_bounds": [-120.5, 35.0, -119.5, 35.5],
        "generator": {
            "kind": "box_around_polygon",
            "params": {
                "sensor": "AVIRIS-NG",
                "altitude_msl_m": 7000,
                "overlap_pct": 20,
                "azimuth": 0,
                "box_name": "Smoke"
            }
        },
        "geometry": {
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [-120.3, 35.1], [-119.7, 35.1],
                    [-119.7, 35.4], [-120.3, 35.4],
                    [-120.3, 35.1]
                ]]
            }
        }
    }')
echo "$GEN" | summarize '
cid = d["campaign_id"]
n = len(d["flight_lines"]["features"])
rev = d["revision"]
group_id = d["summary"]["group_id"]
print(f"  campaign_id={cid}")
print(f"  lines={n}  revision={rev}  group_id={group_id}")
'
CID=$(echo "$GEN" | extract 'd["campaign_id"]')

# --- 3. Compute plan ------------------------------------------------------

step "5. /compute-plan  (NASA_GV, KSBP turnaround, still air, two lines)"
curl -fsS -X POST "$BASE/compute-plan" \
    -H 'Content-Type: application/json' \
    -d "{
        \"campaign_id\": \"$CID\",
        \"sequence\": [
            {\"kind\": \"line\", \"line_id\": \"line_001\"},
            {\"kind\": \"line\", \"line_id\": \"line_002\"}
        ],
        \"aircraft\": \"NASA_GV\",
        \"wind\": {\"kind\": \"still_air\"},
        \"takeoff_airport\": \"KSBP\",
        \"return_airport\": \"KSBP\"
    }" | summarize '
s = d["summary"]
segs = s["segments"]
dist = s["total_distance_nm"]
mins = s["total_time_min"]
fls = s["flight_line_segments"]
print(f"  segments={segs}  total_distance={dist:.1f} nm  total_time={mins:.1f} min")
print(f"  flight_line_segments={fls}")
'

# --- 4. Pattern generation ------------------------------------------------

step "6. /generate-pattern  (racetrack at polygon center)"
curl -fsS -X POST "$BASE/generate-pattern" \
    -H 'Content-Type: application/json' \
    -d "{
        \"campaign_id\": \"$CID\",
        \"campaign_bounds\": [-120.5, 35.0, -119.5, 35.5],
        \"pattern\": \"racetrack\",
        \"center_lat\": 35.25, \"center_lon\": -120.0,
        \"heading\": 90,
        \"altitude_msl_m\": 5000,
        \"params\": {\"leg_length_m\": 8000, \"n_legs\": 2}
    }" | summarize '
pid = d["pattern_id"]
kind = d["pattern_kind"]
name = d["pattern_name"]
params = d["pattern_params"]
print(f"  pattern_id={pid}  kind={kind}  name={name}")
print(f"  params={params}")
'

# --- 5. Coverage % --------------------------------------------------------

step "7. /generate-swaths with target_polygon  (coverage % readout)"
curl -fsS -X POST "$BASE/generate-swaths" \
    -H 'Content-Type: application/json' \
    -d "{
        \"campaign_id\": \"$CID\",
        \"line_ids\": [\"line_001\", \"line_002\", \"line_003\", \"line_004\"],
        \"sensor\": \"AVIRIS-NG\",
        \"target_polygon\": {
            \"type\": \"Feature\",
            \"geometry\": {
                \"type\": \"Polygon\",
                \"coordinates\": [[
                    [-120.3, 35.1], [-119.7, 35.1],
                    [-119.7, 35.4], [-120.3, 35.4],
                    [-120.3, 35.1]
                ]]
            }
        }
    }" | summarize '
n = d["count"]
go = d["gap_overlap"]
overlaps = go.get("overlapping_pairs", 0)
gaps = go.get("gap_pairs", 0)
cov = d["coverage_fraction"]
print(f"  swaths={n}  overlap_pairs={overlaps}  gap_pairs={gaps}")
if cov is None:
    print("  coverage=None (no target_polygon)")
else:
    pct = cov * 100
    print(f"  coverage={pct:.1f}% of target polygon")
'

# --- 6. Export -----------------------------------------------------------

step "8. /export  (KML + GPX from the computed plan)"
curl -fsS -X POST "$BASE/export" \
    -H 'Content-Type: application/json' \
    -d "{\"campaign_id\": \"$CID\", \"formats\": [\"kml\", \"gpx\"]}" \
    | summarize '
for a in d["artifacts"]:
    fmt = a["format"]
    fn = a["filename"]
    url = a["download_url"]
    print(f"  {fmt}: {fn}  ({url})")
warns = d["warnings"]
if warns:
    print(f"  warnings: {warns}")
'

echo
echo "── smoke complete ──────────────────────────────────────"
echo "Try the interactive API at:  $BASE/docs"
