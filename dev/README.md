# dev/

Local-development helper scripts.  Not part of the published service or
the MMGIS tool — these are just convenience wrappers around the
commands we'd otherwise type by hand.

## Quick start

In one terminal — start the service:

```bash
dev/run-service.sh
```

Defaults: editable HyPlan at `../hyplan`, campaigns in `dev/.campaigns/`,
listening on `127.0.0.1:8100`.  Override with env vars or flags:

```bash
HYPLAN_SRC=~/code/hyplan dev/run-service.sh --port 8200
dev/run-service.sh --install      # also pip-install deps on this run
```

In a second terminal — walk through the main HyPlan workflow:

```bash
dev/smoke.sh
```

This hits 8 endpoints in sequence:

1. `/health`
2. `/aircraft` (first 5)
3. `/sensors` (first 5)
4. `/generate-lines` — a ~30 nm box near San Luis Obispo at FL230 with
   AVIRIS-NG
5. `/compute-plan` — NASA_GV, still air, KSBP turnaround, two lines
6. `/generate-pattern` — a racetrack
7. `/generate-swaths` with `target_polygon` — coverage % readout
8. `/export` — KML + GPX of the computed plan

Each step prints a structured summary.  No `jq` dependency — the
script uses inline Python (which the service already requires) for
all JSON parsing and extraction.

## Files

| File              | What it does                                        |
| ----------------- | --------------------------------------------------- |
| `run-service.sh`  | Start uvicorn with sensible local-dev defaults      |
| `smoke.sh`        | Curl-driven end-to-end smoke against a live service |
| `.campaigns/`     | Default `HYPLAN_CAMPAIGNS_DIR` (created on demand)  |

## Why not just type the commands?

Two reasons:

- `run-service.sh` does a quick `import hyplan; assert NASA_GV exists`
  pre-flight that catches the namespace-package shadow described in
  [.knowledge/knowledge-notes.md](../.knowledge/knowledge-notes.md).
  If you ever rename the parent dir to `hyplan/`, this catches it
  before uvicorn returns confusing 400s.
- `smoke.sh` is the curl version of `tests/routers/*.py` — convenient
  for sanity-checking the service against a *new* HyPlan version
  without firing pytest.

## See also

- [AGENTS.md](../AGENTS.md) — top-level repo orientation
- [.knowledge/conventions-and-gotchas.md](../.knowledge/conventions-and-gotchas.md)
- For frontend changes, symlink `mmgis-tool/HyPlan` into an MMGIS
  checkout's `src/essence/Tools/HyPlan` and `npm start` MMGIS;
  webpack-dev-server hot-reloads the tool.
