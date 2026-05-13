#!/usr/bin/env bash
#
# dev/run-service.sh — start the HyPlan service for local development.
#
# Defaults:
#   HYPLAN_SRC                ../hyplan (editable HyPlan checkout)
#   HYPLAN_CAMPAIGNS_DIR      <repo>/dev/.campaigns/
#   host:port                 127.0.0.1:8100
#
# Override with env vars or flags:
#   HYPLAN_SRC=~/code/hyplan dev/run-service.sh --port 8200
#
# This script does NOT install dependencies — run once:
#
#   pip install -e ../hyplan
#   pip install -r service/requirements.txt
#
# (or pass --install on the first run).

set -euo pipefail

# --- Resolve repo root ----------------------------------------------------

cd "$(dirname "${BASH_SOURCE[0]}")/.."
REPO_ROOT="$PWD"

# --- Defaults -------------------------------------------------------------

HYPLAN_SRC="${HYPLAN_SRC:-$REPO_ROOT/../hyplan}"
HYPLAN_CAMPAIGNS_DIR="${HYPLAN_CAMPAIGNS_DIR:-$REPO_ROOT/dev/.campaigns}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8100}"
INSTALL=0

# --- Flags ----------------------------------------------------------------

while (( "$#" )); do
    case "$1" in
        --host)    HOST="$2"; shift 2 ;;
        --port)    PORT="$2"; shift 2 ;;
        --install) INSTALL=1; shift ;;
        -h|--help)
            sed -n '2,/^$/p' "$0" | sed 's/^#//; s/^ //'
            exit 0
            ;;
        *)
            echo "Unknown flag: $1" >&2
            exit 1
            ;;
    esac
done

# --- Sanity --------------------------------------------------------------

if [[ ! -d "$HYPLAN_SRC" ]]; then
    echo "HYPLAN_SRC does not exist: $HYPLAN_SRC" >&2
    echo "Set HYPLAN_SRC to the location of your hyplan checkout." >&2
    exit 1
fi

mkdir -p "$HYPLAN_CAMPAIGNS_DIR"

if (( INSTALL )); then
    echo "==> Installing dev deps"
    pip install -e "$HYPLAN_SRC"
    pip install -r "$REPO_ROOT/service/requirements.txt"
    pip install -r "$REPO_ROOT/tests/requirements.txt"
fi

# Quick import smoke before starting — gives a clear error if the
# editable HyPlan install is broken / missing.
python -c "import hyplan; assert hasattr(hyplan, 'NASA_GV'), 'hyplan.NASA_GV missing — see .knowledge/knowledge-notes.md'" || {
    echo "==> Re-running with --install would re-install HyPlan into the active env." >&2
    exit 1
}

echo "==> Starting service"
echo "    repo:      $REPO_ROOT"
echo "    hyplan:    $HYPLAN_SRC"
echo "    campaigns: $HYPLAN_CAMPAIGNS_DIR"
echo "    listening: http://$HOST:$PORT"
echo "    docs:      http://$HOST:$PORT/docs"
echo

export HYPLAN_CAMPAIGNS_DIR

exec uvicorn service.app:app \
    --host "$HOST" \
    --port "$PORT" \
    --reload \
    --log-level info
