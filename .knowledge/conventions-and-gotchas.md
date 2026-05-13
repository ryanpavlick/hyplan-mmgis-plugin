# Conventions & Gotchas

Quick-reference for patterns that aren't enforced by lint and common
issues that bite new contributors.

## Python service (`service/`)

### Module layout

| Where                       | What                                                    |
| --------------------------- | ------------------------------------------------------- |
| `service/app.py`            | FastAPI app, middleware, router wiring. Stay slim.      |
| `service/state.py`          | Campaign and plan caches; persistence helpers.          |
| `service/errors.py`         | `classify(exc) -> (status, code)` + `raise_http(...)`.  |
| `service/schemas.py`        | Every Pydantic model. Shared so cross-router refs work. |
| `service/routers/<area>.py` | One `APIRouter` per functional area.                    |

New endpoints go in the matching `routers/<area>.py`.  Bigger-than-30-line
helpers used by exactly one endpoint also live in that router module;
helpers shared across routers go up a level (in `state.py` or a new
module).  Don't put helpers in `app.py` - it's the entry point.

### Errors

Always classify exceptions through the shared helper:

```python
from ..errors import raise_http

try:
    pattern = compute_something(...)
except HTTPException:
    raise
except Exception as exc:
    raise_http("generate-pattern", exc)
```

`raise_http`:

- Maps `HyPlanValueError` and `HyPlanTypeError` to **400** with stable
  `code: hyplan_value_error` / `hyplan_type_error`.
- Other `HyPlanError` subclasses -> **400** with `code: hyplan_error`.
- `ValueError` / `KeyError` -> **400** with `code: bad_input`.
- Anything else -> **500** with `code: internal_error`, full
  traceback logged.

Response detail is `{message, code, operation}`.  Frontend
`getErrorMessage` handles both this shape and FastAPI's legacy string
`detail`.

### Python style

- PEP 8.  CI runs `ruff check service`.
- 4-space indent, double-quoted strings outside of f-strings (ruff
  default).
- Type hints on public functions and router handlers.

### What ruff catches

- Unused imports (F401)
- Unused variables (F841)
- Module-level imports below other code (E402)
- f-strings with no placeholders (F541)

Common workaround that's wrong: don't suppress with `# noqa`.  Fix
the underlying issue (drop the unused import, hoist the import to
module top, etc.).

## Frontend (`mmgis-tool/HyPlan/`)

### Single-file convention

Everything lives in `HyPlanTool.js`.  Resist the urge to split it
into multiple modules - MMGIS plugins are loaded as a single tool
and the build pipeline is the host MMGIS's, not ours.

If `HyPlanTool.js` grows past ~3000 lines reconsider; until then the
existing organization (sections with `// --- ... ---` headers) is the
expected pattern.

### Dev loop with HMR

MMGIS dev mode (`npm start` from the MMGIS checkout) runs
webpack-dev-server with HMR on PORT+1.  Symlink our tool directory
once and edits to `HyPlanTool.js` / `HyPlanTool.css` hot-reload
without any rebuild:

```bash
ln -s "$PWD/mmgis-tool/HyPlan" /path/to/MMGIS/src/essence/Tools/HyPlan
```

Reserve `cp -r` + `npm run build` for one-shot installs into an
MMGIS instance you aren't actively iterating on.

### MMGIS singletons

| Symbol  | Where it comes from                              | Role                                    |
| ------- | ------------------------------------------------ | --------------------------------------- |
| `$`     | `import $ from 'jquery'`                         | DOM / events.  MMGIS-wide convention.   |
| `L_`    | `import L_ from '../../Basics/Layers_/Layers_'`  | MMGIS layer manager.                    |
| `Map_`  | `import Map_ from '../../Basics/Map_/Map_'`      | Wraps Leaflet `Map_.map`.               |
| `L`     | global (loaded by MMGIS before our tool boots)   | Plain Leaflet.                          |

Do not `import L from 'leaflet'` inside our tool - the MMGIS build
expects `L` to come from the page-level Leaflet, not a bundled copy.
ESLint config marks `L` as a known global.

### State

Tool state is module-level `let` declarations near the top of
`HyPlanTool.js`.  Layers in particular need the
`hyplanOwn(layer)` / `hyplanDisownAndRemove(layer)` lifecycle so MMGIS
toggling the Draw layer doesn't collaterally remove ours.

### Error display

Service responses pass through `getErrorMessage(data)`, which handles
both legacy `{detail: "string"}` and the structured
`{detail: {message, code, operation}}` shape.  Use it for all `fetch`
error paths.

### JS style

- 4-space indent, single quotes, no trailing semicolons.
- Match the surrounding MMGIS style (no enforced Prettier).
- ESLint flat config (`eslint.config.js`) uses
  `@eslint/js` recommended.  CI runs `npm run lint`.

## CI surface

CI (`.github/workflows/tests.yml`) runs three jobs:

| Job             | Python         | What it does                                                                              |
| --------------- | -------------- | ----------------------------------------------------------------------------------------- |
| `service (3.x)` | 3.10/3.11/3.12 | `ruff check service` -> import smoke -> uvicorn start + curl /health, /aircraft, /sensors |
| `frontend`      | -              | `npm ci` -> `npm run lint` -> `npm run validate:config`                                   |
| `docker`        | -              | `docker build ./service` -> mount HyPlan -> hit /health                                   |

All three install HyPlan from `ryanpavlick/hyplan@main` (not a release
tag).

## Pre-commit hooks

`.pre-commit-config.yaml` mirrors CI lint:

- ruff on `service/`
- ESLint on `mmgis-tool/HyPlan/`
- trailing-whitespace, EOF-fixer, YAML / JSON / merge-conflict checks

Install once per clone:

```bash
pip install pre-commit
pre-commit install
```

If a hook reformats a file (e.g. EOF-fixer trims a trailing blank
line), re-stage the modified file and re-commit.  Don't bypass with
`--no-verify`.

## CHANGELOG conventions

Entries live under `## vX.Y.0 (unreleased)` at the top.  Use a Keep
a Changelog-style breakdown of `### Added`, `### Changed`,
`### Fixed`, `### Removed`.  When releasing, `release.yml` extracts
the section between `## vX.Y.Z` headers as the GitHub Release notes,
so keep the section heading anchored at column 0 and use `##` followed
by a space (not bare `##`) as the prefix.

## Git

- Direct commits to `main` are fine for trivial work (the project is
  pre-1.0 with one contributor).  Branches per PR start making sense
  when you have more than one open feature at once.
- Imperative commit subject; first line under ~70 chars.
- No `Co-Authored-By: Claude` trailers.  Ever.
